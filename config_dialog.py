"""Verknüpfungen einrichten: Layer + Feld gegen Knotentyp + Property.

Die Auswahllisten werden aus dem geladenen Graphen und dem Projekt gefüllt --
niemand soll einen Property-Namen abtippen müssen und sich dann fragen, warum
nichts zusammenfindet. Genau deshalb sitzt auch der Knopf "Check matches"
direkt im Dialog: Ob eine Zuordnung trägt, entscheidet sich an den Daten, nicht
an der Plausibilität der Namen.

Das Formular selbst steht in ``linkform.py``, weil die Werteübernahme dieselben
Felder braucht.
"""

from qgis.core import QgsProject

from .compat import (
    BUTTONS_OK_CANCEL,
    exec_dialog,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from .linkform import LinkKeyForm, describe_link
from .linking import LinkConfig, LinkIndex


class LinkConfigDialog(QDialog):
    """``graph_info`` ist die Antwort von ``BridgeServer.describe_graph``.

    ``check_fn(config, callback)`` holt die Graph-Seite einer Verknüpfung aus
    der Seite -- asynchron, deshalb als Callback und nicht als Rückgabewert.
    """

    def __init__(self, links, graph_info, check_fn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GraphExplorer — Links")
        self.resize(560, 460)

        self._links = [LinkConfig.from_dict(link.to_dict()) for link in links]  # Arbeitskopie
        self._graph_info = graph_info or {"loaded": False, "types": []}
        self._check_fn = check_fn
        self._current = -1
        self._loading = False   # unterdrückt Signalstürme beim Befüllen der Felder

        self._build_ui()
        self._reload_list()
        if self._links:
            self.list.setCurrentRow(0)
        else:
            self._set_form_enabled(False)

    # -- Aufbau ---------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        if not self._graph_info.get("loaded"):
            hint = QLabel(
                "No graph is loaded yet. Node type and property can only be "
                "chosen once a graph is open in the GraphExplorer."
            )
            hint.setWordWrap(True)
            outer.addWidget(hint)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row_changed)
        outer.addWidget(self.list, 1)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._on_add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._on_remove)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        outer.addLayout(row)

        self.form = LinkKeyForm(self._graph_info)
        self.form.changed.connect(self._commit)
        outer.addWidget(self.form)

        self.chk_enabled = QCheckBox("active")
        self.chk_enabled.stateChanged.connect(self._commit)
        outer.addWidget(self.chk_enabled)

        self.btn_check = QPushButton("Check matches")
        self.btn_check.clicked.connect(self._on_check)
        outer.addWidget(self.btn_check)

        buttons = QDialogButtonBox(BUTTONS_OK_CANCEL)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # -- Liste ----------------------------------------------------------------

    def _reload_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for link in self._links:
            mark = "" if link.enabled else "  (inactive)"
            self.list.addItem("%s%s" % (self._describe(link), mark))
        self.list.blockSignals(False)

    def _describe(self, link):
        return describe_link(link, self.form.names)

    def _on_add(self):
        self._links.append(LinkConfig())
        self._reload_list()
        self.list.setCurrentRow(len(self._links) - 1)
        self._set_form_enabled(True)

    def _on_remove(self):
        if 0 <= self._current < len(self._links):
            del self._links[self._current]
            self._current = -1
            self._reload_list()
            if self._links:
                self.list.setCurrentRow(0)
            else:
                self._set_form_enabled(False)

    def _on_row_changed(self, row):
        self._current = row
        if 0 <= row < len(self._links):
            self._set_form_enabled(True)
            self._load_into_form(self._links[row])

    def _set_form_enabled(self, on):
        self.form.set_editable(on)
        for widget in (self.chk_enabled, self.btn_check):
            widget.setEnabled(on)

    # -- Formular -------------------------------------------------------------

    def _load_into_form(self, link):
        self._loading = True
        try:
            self.form.load(link)
            self.chk_enabled.setChecked(link.enabled)
        finally:
            self._loading = False

    def _commit(self, *_args):
        """Formular -> Datenmodell. Läuft bei jeder Änderung, nicht erst bei OK."""
        if self._loading or not (0 <= self._current < len(self._links)):
            return
        link = self._links[self._current]
        self.form.apply_to(link)
        link.enabled = self.chk_enabled.isChecked()
        link.name = self._describe(link)

        row = self._current
        self.list.blockSignals(True)
        self.list.item(row).setText(link.name + ("" if link.enabled else "  (inactive)"))
        self.list.blockSignals(False)

    # -- Probe ----------------------------------------------------------------

    def _on_check(self):
        if not (0 <= self._current < len(self._links)):
            return
        link = self._links[self._current]
        if not link.is_complete():
            QMessageBox.information(self, "Check matches", "The link is not complete yet.")
            return

        layer = QgsProject.instance().mapLayer(link.layer_id)
        if layer is None:
            QMessageBox.warning(self, "Check matches", "The layer is no longer in the project.")
            return

        idx = layer.fields().lookupField(link.layer_field)
        if idx < 0:
            QMessageBox.warning(self, "Check matches",
                                'The layer has no field "%s".' % link.layer_field)
            return

        index = LinkIndex(link)
        index.set_layer_side((f.id(), f.attribute(idx)) for f in layer.getFeatures())

        def done(entries):
            index.set_graph_side(entries or [])
            report = index.report()
            box = QMessageBox(self)
            box.setWindowTitle("Matches — %s" % link.name)
            box.setText(report.summary())
            box.setDetailedText(report.details())
            exec_dialog(box)

        self._check_fn(link.js_spec(), done)

    # -- Ergebnis -------------------------------------------------------------

    def links(self):
        return self._links
