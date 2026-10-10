"""Werte aus dem Graphen in die Attributtabelle übernehmen -- die Bedienung.

Drei Schritte, in der Reihenfolge, in der man sie denkt:

1. **Layer und Schlüssel.** Dasselbe Formular wie bei den Verknüpfungen
   (``linkform.py``) -- der Join-Key ist derselbe, und zwei Dialoge, die ihn
   verschieden nennen, wären zwei Gelegenheiten, ihn verschieden einzustellen.
2. **Werte zusammenstellen.** Links, was der Graph hergibt; rechts die Tabelle,
   die geschrieben wird. Dazwischen: ziehen.
3. **Vorschau, dann übernehmen.** Erst die Zeilen sehen, dann schreiben.

**Warum die Vorschau nicht optional ist.** Was hier passiert, verändert fremde
Daten, und der häufigste Fehler ist keiner, der eine Fehlermeldung auslöst: eine
Verknüpfung, die zufällig auf einen Teil der Features passt. Eine Spalte, die
40 von 150 Zeilen füllt, sieht in der Attributtabelle genauso aus wie eine
richtige -- der Unterschied steht in der Bilanz daneben. Deshalb rechnet
"Transfer" immer erst, zeigt und schreibt danach.

**Und warum nicht nur gezogen werden kann.** Ziehen ist die Geste, um die es
geht, aber es ist eine Geste ohne Tastatur und ohne Ersatz. Derselbe Weg steht
deshalb als Doppelklick und als Knopf daneben.
"""

from qgis.core import QgsFeatureRequest, QgsProject

from . import settings, transfer
from .attrwrite import max_field_name_len, write_values
from .compat import (
    BUTTONS_CLOSE,
    COPY_ACTION,
    DRAG_DROP,
    DRAG_ONLY,
    EXTENDED_SELECTION,
    HEADER_STRETCH,
    HEADER_TO_CONTENTS,
    ITEM_IS_DROP_ENABLED,
    ITEM_IS_EDITABLE,
    MSG_OK,
    MSG_OK_CANCEL,
    MSG_YES,
    NO_EDIT_TRIGGERS,
    USER_ROLE,
    exec_dialog,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTimer,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    pyqtSignal,
)
from .layersync import NO_GEOMETRY
from .linkform import LinkKeyForm, describe_link
from .linking import LinkIndex, short_name

#: Wie lange nach der letzten Änderung am Schlüssel gewartet wird, bevor die
#: Werteliste neu geholt wird. Ohne diese Frist ginge beim Tippen im
#: Muster-Feld je Zeichen eine Anfrage über die Leitung.
RELOAD_DELAY_MS = 400

#: So viele Zeilen zeigt die Vorschau. Mehr wäre keine Vorschau mehr, sondern
#: eine zweite Attributtabelle -- und die gibt es in QGIS schon.
PREVIEW_ROWS = 25

#: Vorangestellt, wenn es den Wert einer Spalte im Graphen nicht (mehr) gibt.
#: Nur Anzeige -- beim Auslesen der Tabelle wird das Zeichen wieder abgestreift,
#: sonst stünde es später in der gemerkten Zusammenstellung.
ORPHAN_MARK = "⚠ "


class TargetTable(QTreeWidget):
    """Die Zieltabelle -- nimmt entgegen, was aus der linken Liste fällt.

    Das Ablegen wird selbst behandelt, statt es über MIME-Daten laufen zu
    lassen: Beide Listen leben im selben Prozess, ``event.source()`` gibt die
    Herkunft her, und die abgelegten Einträge stehen dort als Auswahl bereit.
    Das erspart ein Kodierformat, das nur zwischen zwei Widgets vermitteln
    müsste, die sich ohnehin kennen.
    """

    #: Kennungen (``transfer.source_key``) der fallengelassenen Werte.
    sources_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["Value from the graph", "Column", "Type"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(EXTENDED_SELECTION)
        # Getippt wird nur im Spaltennamen, und nur auf Doppelklick -- siehe
        # _on_double_click im Dialog. QTreeWidgetItem trägt seine Flags für
        # alle Spalten gemeinsam; ohne diese Bremse ließe sich auch die
        # Herkunft überschreiben, und die ist keine Meinung, sondern ein Fakt.
        self.setEditTriggers(NO_EDIT_TRIGGERS)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(DRAG_DROP)     # Umsortieren *und* Annehmen

        header = self.header()
        header.setSectionResizeMode(0, HEADER_STRETCH)
        header.setSectionResizeMode(1, HEADER_TO_CONTENTS)
        header.setSectionResizeMode(2, HEADER_TO_CONTENTS)

    def _foreign(self, event):
        return event.source() is not self and isinstance(event.source(), QListWidget)

    def _acceptable(self, event):
        return event.source() is self or self._foreign(event)

    def dragEnterEvent(self, event):
        if self._acceptable(event):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._acceptable(event):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self._acceptable(event):
            event.ignore()
            return

        # Immer "Kopieren", auch beim Umsortieren: Meldet der Fallenlasser eine
        # Verschiebung, räumt Qt anschließend in der *Quelle* auf -- in der
        # linken Liste hieße das, ein einmal übernommener Wert stünde für eine
        # zweite Spalte nicht mehr da; beim Umsortieren würde die eben selbst
        # eingefügte Zeile gleich wieder entfernt.
        event.setDropAction(COPY_ACTION)
        event.accept()

        if self._foreign(event):
            keys = [item.data(USER_ROLE) for item in event.source().selectedItems()]
            keys = [key for key in keys if key]
            if keys:
                self.sources_dropped.emit(keys)
            return
        self._reorder(self._drop_row(event))

    def _drop_row(self, event):
        """Vor welche Zeile fallengelassen wurde.

        ``event.pos()`` heißt unter Qt6 ``event.position()`` und gibt
        Fließkomma zurück -- eine der wenigen Stellen, an denen sich die beiden
        nicht über ``qgis.PyQt`` angleichen lassen.
        """
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(pos)
        if item is None:
            return self.topLevelItemCount()
        row = self.indexOfTopLevelItem(item)
        return row + 1 if pos.y() > self.visualItemRect(item).center().y() else row

    def _reorder(self, target):
        """Die ausgewählten Zeilen an ``target`` einfügen.

        Von Hand statt über Qts Standardweg: Der schreibt die Zeile aus
        MIME-Rohdaten neu und verliert dabei die Item-Flags -- unter anderem
        die, mit der die Tabelle flach bleibt. Nach dem ersten Umsortieren
        ließe sich eine Zeile in eine andere hineinziehen, und eine Spalte, die
        zum Unterpunkt geworden ist, zählt bei ``topLevelItemCount`` nicht mehr
        mit: Sie wäre still verschwunden.
        """
        rows = sorted(self.indexOfTopLevelItem(item) for item in self.selectedItems())
        if not rows:
            return
        items = [self.takeTopLevelItem(row) for row in reversed(rows)]
        items.reverse()
        # Die entnommenen Zeilen *vor* dem Ziel verschieben es nach vorn.
        target -= sum(1 for row in rows if row < target)
        for offset, item in enumerate(items):
            self.insertTopLevelItem(target + offset, item)
            item.setSelected(True)


class TransferDialog(QDialog):
    """``describe_fn(spec, cb)`` und ``collect_fn(spec, sources, cb)`` gehen
    über die Brücke in den Browser -- asynchron, deshalb mit Rückruf.

    ``links`` sind die eingerichteten Verknüpfungen, ``plans`` die im Projekt
    gespeicherten Vorlagen. Beide dienen nur als Startpunkt: Was hier
    eingestellt wird, gilt für diese Übernahme -- bis es jemand mit "Save …"
    als Vorlage ablegt.
    """

    def __init__(self, links, plans, graph_info, describe_fn, collect_fn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GraphExplorer — Transfer values to the attribute table")
        self.resize(900, 720)

        self._links = list(links or [])
        self._plans = list(plans or [])
        self._graph_info = graph_info or {"loaded": False, "types": []}
        self._describe_fn = describe_fn
        self._collect_fn = collect_fn

        self._available = {}        # Kennung -> Eintrag von describeValues
        self._loading_rows = False  # unterdrückt itemChanged beim Aufbauen

        self._build_ui()

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_values)

        self._restore_start()

    # -- Aufbau ---------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        if not self._graph_info.get("loaded"):
            hint = QLabel(
                "No graph is loaded yet. Values can only be transferred once a "
                "graph is open in the GraphExplorer."
            )
            hint.setWordWrap(True)
            outer.addWidget(hint)

        outer.addWidget(self._build_key_box())

        split = QSplitter()
        split.addWidget(self._build_source_box())
        split.addWidget(self._build_target_box())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        outer.addWidget(self._build_preview_box(), 1)
        outer.addLayout(self._build_actions())

    def _build_key_box(self):
        box = QGroupBox("1 · Layer and key")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Use settings from"))
        self.cb_preset = QComboBox()
        self.cb_preset.setToolTip(
            "Fills in layer and key below from one of your links — or layer, key "
            "and columns from a saved template. Everything can still be changed "
            "afterwards."
        )
        self.cb_preset.currentIndexChanged.connect(self._on_preset)
        row.addWidget(self.cb_preset, 1)
        self.btn_save_preset = QPushButton("Save …")
        self.btn_save_preset.setToolTip(
            "Save layer, key and columns as a template in this project."
        )
        self.btn_save_preset.clicked.connect(self._save_preset)
        row.addWidget(self.btn_save_preset)
        self.btn_delete_preset = QPushButton("Delete")
        self.btn_delete_preset.setToolTip("Delete the selected template.")
        self.btn_delete_preset.clicked.connect(self._delete_preset)
        row.addWidget(self.btn_delete_preset)
        layout.addLayout(row)

        self.form = LinkKeyForm(self._graph_info)
        self.form.changed.connect(self._on_key_changed)
        layout.addWidget(self.form)

        self.lbl_match = QLabel("")
        self.lbl_match.setWordWrap(True)
        self.lbl_match.setStyleSheet("color: #555;")
        layout.addWidget(self.lbl_match)
        return box

    def _build_source_box(self):
        box = QGroupBox("2 · Values in the graph")
        layout = QVBoxLayout(box)

        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("filter …")
        self.ed_filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.ed_filter)

        self.list_available = QListWidget()
        self.list_available.setSelectionMode(EXTENDED_SELECTION)
        self.list_available.setDragEnabled(True)
        self.list_available.setDragDropMode(DRAG_ONLY)
        self.list_available.setDefaultDropAction(COPY_ACTION)
        self.list_available.itemDoubleClicked.connect(
            lambda item: self._add_sources([item.data(USER_ROLE)])
        )
        layout.addWidget(self.list_available, 1)

        hint = QLabel("Drag to the right — or double-click.")
        hint.setStyleSheet("color: #777; font-size: 11px;")
        layout.addWidget(hint)
        return box

    def _build_target_box(self):
        box = QGroupBox("… and the columns they become")
        layout = QVBoxLayout(box)

        self.table = TargetTable()
        self.table.sources_dropped.connect(self._add_sources)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._sync_kind_combo)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Type"))
        self.cb_kind = QComboBox()
        for key, label in transfer.KIND_LABELS:
            self.cb_kind.addItem(label, key)
        self.cb_kind.setToolTip("Type of the selected column(s) in the attribute table.")
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        row.addWidget(self.cb_kind)
        row.addStretch(1)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._remove_selected)
        row.addWidget(btn_remove)
        layout.addLayout(row)

        hint = QLabel("Rename a column: double-click. Reorder: drag.")
        hint.setStyleSheet("color: #777; font-size: 11px;")
        layout.addWidget(hint)
        return box

    def _build_preview_box(self):
        box = QGroupBox("3 · Preview")
        layout = QVBoxLayout(box)

        self.preview = QTreeWidget()
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setHeaderLabels(["Key"])
        layout.addWidget(self.preview, 1)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        layout.addWidget(self.lbl_summary)
        return box

    def _build_actions(self):
        row = QHBoxLayout()

        self.chk_clear = QCheckBox("Clear features without a node")
        self.chk_clear.setToolTip(
            "Off: existing values stay as they are.\n"
            "On: afterwards the column reflects exactly what the graph knows — "
            "including where it knows nothing."
        )
        row.addWidget(self.chk_clear)

        self.chk_commit = QCheckBox("Save right away")
        self.chk_commit.setToolTip(
            "Off: the layer stays in edit mode. Check first, then save or "
            "discard."
        )
        row.addWidget(self.chk_commit)
        row.addStretch(1)

        self.btn_preview = QPushButton("Preview")
        self.btn_preview.clicked.connect(lambda: self._compute(then_write=False))
        row.addWidget(self.btn_preview)

        self.btn_apply = QPushButton("Transfer")
        self.btn_apply.setDefault(True)
        self.btn_apply.clicked.connect(lambda: self._compute(then_write=True))
        row.addWidget(self.btn_apply)

        buttons = QDialogButtonBox(BUTTONS_CLOSE)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        return row

    # -- Vorlagen -------------------------------------------------------------

    def _restore_start(self):
        # Ältere Projekte haben Vorlagen ohne Namen (damals: je Layer die zuletzt
        # benutzte). Sie bekommen hier einen, damit man sie wiederfindet.
        unnamed = [plan for plan in self._plans if not plan.name]
        for plan in unnamed:
            plan.name = describe_link(plan.link, self.form.names)
        if unnamed:
            # Gleich zurückschreiben: Sonst fände "Delete" sie unter dem neuen
            # Namen nicht, weil im Projekt noch keiner steht.
            settings.write_transfers(self._plans)
        self._fill_presets()

        # Eine Vorlage ist der bessere Startpunkt als eine bloße Verknüpfung:
        # Sie enthält sie schon.
        if self._plans:
            self.cb_preset.setCurrentIndex(1)
        elif self._links:
            self.cb_preset.setCurrentIndex(1)
        else:
            self._reload_values()
        self._sync_preset_buttons()

    def _fill_presets(self, select_name=None):
        """Die Auswahl neu aufbauen; mit ``select_name`` diese Vorlage wählen, ohne
        das Formular neu zu laden -- es zeigt sie ja schon."""
        project = QgsProject.instance()
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        self.cb_preset.addItem("— nothing, set up below —", None)
        for i, plan in enumerate(self._plans):
            text = "Template: %s  (%d columns)" % (plan.name, len(plan.columns))
            if project.mapLayer(plan.link.layer_id) is None:
                text += "  — layer not in project"
            self.cb_preset.addItem(text, ("plan", i))
        for i, link in enumerate(self._links):
            self.cb_preset.addItem("Link: %s" % describe_link(link, self.form.names), ("link", i))
        if select_name is not None:
            for i, plan in enumerate(self._plans):
                if plan.name == select_name:
                    self.cb_preset.setCurrentIndex(1 + i)
        self.cb_preset.blockSignals(False)
        self._sync_preset_buttons()

    def _selected_plan(self):
        data = self.cb_preset.currentData()
        return self._plans[data[1]] if data and data[0] == "plan" else None

    def _sync_preset_buttons(self):
        self.btn_delete_preset.setEnabled(self._selected_plan() is not None)

    def _save_preset(self):
        plan = self.plan()
        if not plan.link.is_complete() or not plan.columns:
            QMessageBox.information(
                self, "Save template",
                "Set up layer, key and at least one column first — a template "
                "without columns would be the same as a link."
            )
            return

        current = self._selected_plan()
        suggestion = current.name if current else describe_link(plan.link, self.form.names)
        name, ok = QInputDialog.getText(self, "Save template", "Name of the template:",
                                        text=suggestion)
        name = (name or "").strip()
        if not ok or not name:
            return

        clash = next((p for p in self._plans if p.name.casefold() == name.casefold()), None)
        if clash is not None and clash is not current:
            answer = QMessageBox.question(
                self, "Save template",
                'A template named "%s" already exists. Replace it?' % clash.name,
            )
            if answer != MSG_YES:
                return

        plan.name = name
        settings.save_transfer(plan)
        self._plans = settings.read_transfers()
        self._fill_presets(select_name=name)

    def _delete_preset(self):
        plan = self._selected_plan()
        if plan is None:
            return
        answer = QMessageBox.question(
            self, "Delete template",
            'Delete the template "%s"?\n\nThe form below stays as it is.' % plan.name,
        )
        if answer != MSG_YES:
            return
        settings.delete_transfer(plan.name)
        self._plans = settings.read_transfers()
        self._fill_presets()
        self.cb_preset.blockSignals(True)
        self.cb_preset.setCurrentIndex(0)
        self.cb_preset.blockSignals(False)
        self._sync_preset_buttons()

    def _on_preset(self, _idx):
        self._sync_preset_buttons()
        data = self.cb_preset.currentData()
        if not data:
            return
        what, i = data
        if what == "plan":
            plan = self._plans[i]
            self.form.load(plan.link)
            self.chk_clear.setChecked(plan.clear_unmatched)
            self._set_columns(plan.columns)
        else:
            self.form.load(self._links[i])
            self._set_columns([])
        self._reload_values()

    # -- Werte aus dem Graphen ------------------------------------------------

    def _on_key_changed(self):
        self.lbl_match.setText("")
        self._reload_timer.start(RELOAD_DELAY_MS)

    def _reload_values(self):
        link = self.form.link()
        if not link.is_complete():
            self.list_available.clear()
            self.lbl_match.setText(
                "The link is not complete yet — layer, field, node type and key "
                "source are required."
            )
            return

        self.lbl_match.setText("Fetching values from the graph …")
        self._describe_fn(link.js_spec(), self._on_values_described)

    def _on_values_described(self, info):
        info = info or {}
        values = info.get("values") or []
        total = info.get("total") or 0

        self._available = {}
        self.list_available.clear()
        for entry in values:
            source = entry.get("source") or {}
            key = transfer.source_key(source)
            self._available[key] = entry
            item = QListWidgetItem(self._entry_label(entry))
            item.setData(USER_ROLE, key)
            item.setToolTip("Example: %s" % (entry.get("sample") or "—"))
            self.list_available.addItem(item)
        self._apply_filter(self.ed_filter.text())

        if not total:
            self.lbl_match.setText(
                "No node of this type carries the chosen key. Does the key "
                "source fit?"
            )
        else:
            self.lbl_match.setText(
                "%d nodes with a key · %d values to choose from" % (total, len(values))
            )
        self._mark_orphans()

    def _mark_orphans(self):
        """Spalten kenntlich machen, deren Wert es im Graphen nicht mehr gibt.

        Kommt vor, sobald jemand am Knotentyp dreht, nachdem die Tabelle steht.
        Ohne diesen Hinweis bliebe die Spalte einfach leer -- und leer sieht aus
        wie "im Graphen steht nichts", nicht wie "hier passt etwas nicht
        zusammen".
        """
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            known = item.data(0, USER_ROLE) in self._available
            item.setToolTip(0, "" if known else
                            "Nodes of this type do not (or no longer) have this "
                            "value. The column would stay empty.")
            text = self._plain_label(item)
            self._loading_rows = True
            try:
                item.setText(0, text if known else ORPHAN_MARK + text)
            finally:
                self._loading_rows = False

    def _plain_label(self, item):
        text = item.text(0)
        return text[len(ORPHAN_MARK):] if text.startswith(ORPHAN_MARK) else text

    def _entry_label(self, entry):
        label = transfer.source_text(entry.get("source"), self.form.names)
        count, of = entry.get("count") or 0, entry.get("of") or 0
        return "%s   (%d/%d)" % (label, count, of) if of else label

    def _apply_filter(self, text):
        needle = (text or "").strip().lower()
        for row in range(self.list_available.count()):
            item = self.list_available.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    # -- Die Zieltabelle ------------------------------------------------------

    def _set_columns(self, columns):
        self._loading_rows = True
        try:
            self.table.clear()
            for column in columns:
                self._append_row(column)
        finally:
            self._loading_rows = False

    def _append_row(self, column):
        item = QTreeWidgetItem([transfer.source_text(column.source, self.form.names),
                                column.field, self._kind_label(column.kind)])
        # Die Kennung ist zugleich die Quelle: ``source_key`` ist ihre
        # JSON-Form, aus der ``source_from_key`` sie zurückholt. Die Zeile hängt
        # damit an nichts anderem -- wechselt der Knotentyp und die linke Liste
        # wird neu geholt, bleibt die Zusammenstellung trotzdem vollständig.
        item.setData(0, USER_ROLE, column.key())
        item.setData(2, USER_ROLE, column.kind)
        # Editierbar ja, aber kein Ablageziel: Sonst würde eine Zeile, die man
        # zum Umsortieren auf eine andere zieht, deren Kind -- und aus einer
        # flachen Tabelle würde stillschweigend ein Baum mit verlorener Spalte.
        item.setFlags((item.flags() | ITEM_IS_EDITABLE) & ~ITEM_IS_DROP_ENABLED)
        self.table.addTopLevelItem(item)

    def _kind_label(self, kind):
        for key, label in transfer.KIND_LABELS:
            if key == kind:
                return label
        return kind

    def _add_sources(self, keys):
        """Aus Kennungen werden Spalten -- der eigentliche Ertrag des Ziehens."""
        taken = [self._row_field(i) for i in range(self.table.topLevelItemCount())]
        added = 0
        for key in keys:
            entry = self._available.get(key)
            if entry is None:
                continue
            source = entry.get("source") or {}
            # Denselben Wert zweimal zu übernehmen ergäbe zwei gleiche Spalten
            # mit verschiedenen Namen. Wer das wirklich will, benennt die eine
            # um -- die Vorgabe ist, dass ein Doppelklick zu viel nichts kaputt
            # macht.
            if any(self.table.topLevelItem(i).data(0, USER_ROLE) == key
                   for i in range(self.table.topLevelItemCount())):
                continue
            field = transfer.sanitize_field_name(
                transfer.default_field_name(source, self.form.names),
                taken=taken, max_len=self._max_field_len(),
            )
            taken.append(field)
            # ValueColumn nimmt nur die bekannten Typen an und fällt sonst auf
            # Text zurück -- der Vorschlag der Brücke darf hier direkt durch.
            column = transfer.ValueColumn(source, field=field,
                                          kind=entry.get("kind") or transfer.KIND_TEXT)
            self._loading_rows = True
            try:
                self._append_row(column)
            finally:
                self._loading_rows = False
            added += 1
        if added:
            self.lbl_summary.setText("")     # die alte Bilanz gilt nicht mehr

    def _max_field_len(self):
        layer = QgsProject.instance().mapLayer(self.form.layer_id())
        return max_field_name_len(layer) if layer is not None else None

    def _row_field(self, row):
        return self.table.topLevelItem(row).text(1)

    def _columns(self):
        out = []
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            out.append(transfer.ValueColumn(
                source=transfer.source_from_key(item.data(0, USER_ROLE)),
                field=item.text(1),
                kind=item.data(2, USER_ROLE) or transfer.KIND_TEXT,
            ))
        return out

    def _on_double_click(self, item, _column):
        # Immer Spalte 1, egal wo geklickt wurde: Herkunft und Typ sind keine
        # Textfelder, und ein Doppelklick auf die Herkunft soll nicht in einem
        # Zustand enden, in dem man versehentlich die Zuordnung überschreibt.
        self.table.editItem(item, 1)

    def _on_item_changed(self, item, column):
        if self._loading_rows or column != 1:
            return
        taken = [self.table.topLevelItem(i).text(1)
                 for i in range(self.table.topLevelItemCount())
                 if self.table.topLevelItem(i) is not item]
        clean = transfer.sanitize_field_name(item.text(1), taken=taken,
                                             max_len=self._max_field_len())
        if clean != item.text(1):
            self._loading_rows = True
            try:
                item.setText(1, clean)
            finally:
                self._loading_rows = False

    def _sync_kind_combo(self):
        items = self.table.selectedItems()
        if not items:
            return
        kind = items[0].data(2, USER_ROLE)
        idx = self.cb_kind.findData(kind)
        if idx >= 0:
            self.cb_kind.blockSignals(True)
            self.cb_kind.setCurrentIndex(idx)
            self.cb_kind.blockSignals(False)

    def _on_kind_changed(self, _idx):
        kind = self.cb_kind.currentData()
        self._loading_rows = True
        try:
            for item in self.table.selectedItems():
                item.setData(2, USER_ROLE, kind)
                item.setText(2, self._kind_label(kind))
        finally:
            self._loading_rows = False

    def _remove_selected(self):
        for item in self.table.selectedItems():
            self.table.takeTopLevelItem(self.table.indexOfTopLevelItem(item))

    # -- Rechnen und Schreiben ------------------------------------------------

    def _compute(self, then_write):
        link = self.form.link()
        columns = self._columns()

        if not link.is_complete():
            QMessageBox.information(self, "Transfer", "The link is not complete yet.")
            return
        if not columns:
            QMessageBox.information(
                self, "Transfer",
                "No columns put together yet. Drag values from the list on the "
                "left to the right."
            )
            return

        layer = QgsProject.instance().mapLayer(link.layer_id)
        if layer is None:
            QMessageBox.warning(self, "Transfer", "The layer is no longer in the project.")
            return
        field_index = layer.fields().lookupField(link.layer_field)
        if field_index < 0:
            QMessageBox.warning(self, "Transfer",
                                'The layer has no field "%s".' % link.layer_field)
            return

        index = LinkIndex(link)
        request = (QgsFeatureRequest()
                   .setSubsetOfAttributes([field_index])
                   .setFlags(NO_GEOMETRY))
        index.set_layer_side(
            (f.id(), f.attribute(field_index)) for f in layer.getFeatures(request)
        )

        self.lbl_summary.setText("Fetching values …")
        self._set_busy(True)

        def done(rows):
            try:
                result = transfer.build_transfer(index, columns, rows or [])
            finally:
                self._set_busy(False)
            self._show_result(result, columns)
            if then_write:
                self._write(layer, columns, result)

        self._collect_fn(link.js_spec(), [c.source for c in columns], done)

    def _set_busy(self, busy):
        for widget in (self.btn_preview, self.btn_apply):
            widget.setEnabled(not busy)

    def _show_result(self, result, columns):
        self.preview.clear()
        self.preview.setHeaderLabels(["Key"] + [c.field for c in columns])
        for _fid, key, values in result.preview(PREVIEW_ROWS):
            texts = [short_name(key)] + ["" if v is None else str(v) for v in values]
            self.preview.addTopLevelItem(QTreeWidgetItem(texts))
        for i in range(self.preview.columnCount()):
            self.preview.resizeColumnToContents(i)

        counts = " · ".join("%s: %d" % (c.field, result.filled(i))
                            for i, c in enumerate(columns))
        self.lbl_summary.setText(result.summary() + ("\n" + counts if counts else ""))
        self.lbl_summary.setToolTip(result.details())

    def _write(self, layer, columns, result):
        if not result.matched:
            QMessageBox.warning(
                self, "Transfer",
                "No feature would get a value — nothing was written.\n\n"
                + result.details()
            )
            return

        clear_fids = result.unmatched if self.chk_clear.isChecked() else ()
        question = ["%d features get values." % result.matched]
        if clear_fids:
            question.append("%d more are cleared." % len(clear_fids))
        if result.conflicts:
            question.append("%d cells stay empty because the value is ambiguous."
                            % len(result.conflicts))
        question.append("\nWrite now?")

        box = QMessageBox(self)
        box.setWindowTitle("Transfer")
        box.setText(" ".join(question))
        box.setDetailedText(result.details())
        box.setStandardButtons(MSG_OK_CANCEL)
        if exec_dialog(box) != MSG_OK:
            return

        written = write_values(layer, columns, result.values,
                               clear_fids=clear_fids,
                               commit=self.chk_commit.isChecked())
        if not written.ok:
            QMessageBox.critical(self, "Transfer", written.summary())
            self.lbl_summary.setText(written.summary())
            return

        self.lbl_summary.setText(written.summary())
        QMessageBox.information(self, "Transferred", written.summary())

    # -- Ergebnis -------------------------------------------------------------

    def plan(self):
        """Die Zusammenstellung, wie sie jetzt dasteht -- zum Speichern als Vorlage."""
        return transfer.TransferPlan(
            link=self.form.link(),
            columns=self._columns(),
            clear_unmatched=self.chk_clear.isChecked(),
        )
