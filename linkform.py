"""Das Formular für einen Join-Key: Layer + Feld gegen Knotentyp + Property.

Steht für sich, weil es an zwei Stellen gebraucht wird: beim Einrichten der
Verknüpfungen (``config_dialog.py``) und beim Übernehmen von Werten
(``transfer_dialog.py``). Zweimal dieselben sieben Auswahllisten zu bauen wäre
nicht nur doppelte Arbeit -- es wären zwei Formulare, die auseinanderlaufen,
sobald eine Vergleichsstufe dazukommt. Der Join-Key ist der wunde Punkt der
ganzen Kopplung; er soll überall gleich aussehen und gleich heißen.

Das Formular kennt seinen Zweck nicht: Es füllt eine ``LinkConfig`` und meldet
Änderungen. Ob daraus eine dauerhafte Verknüpfung wird oder eine einmalige
Übernahme, entscheidet der Dialog darum herum.
"""

from qgis.core import QgsProject, QgsVectorLayer

from .compat import (
    TOOLTIP_ROLE,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QWidget,
    pyqtSignal,
)
from .linking import (
    DEFAULT_PATTERN,
    NORMALIZE_MODES,
    NORMALIZE_PATTERN,
    SOURCE_ATTR,
    SOURCE_LABELS,
    DisplayNames,
    LinkConfig,
)


def vector_layers():
    # isinstance statt layer.type(): Die Enumeration dafür heißt je nach
    # QGIS-Version QgsMapLayerType oder Qgis.LayerType, die Klasse
    # QgsVectorLayer heißt seit jeher so.
    out = [
        layer for layer in QgsProject.instance().mapLayers().values()
        if isinstance(layer, QgsVectorLayer)
    ]
    out.sort(key=lambda lyr: lyr.name().lower())
    return out


def describe_link(link, names=None):
    """``"SE.se_nr  ⇄  Befund.se"`` -- die Kurzform einer Verknüpfung.

    Steht hier und nicht im Dialog, weil beide Dialoge dieselbe Zeile zeigen:
    Wer im einen eine Verknüpfung angelegt hat, soll sie im anderen wiederfinden
    und nicht erst übersetzen müssen. Typ und Property heißen wie im
    GraphExplorer (``names``).
    """
    names = names or DisplayNames()
    layer_name = "?"
    layer = QgsProject.instance().mapLayer(link.layer_id)
    if layer is not None:
        layer_name = layer.name()
    if link.node_source == SOURCE_ATTR:
        right = names.attr(link.node_attr) if link.node_attr else ""
    else:
        right = SOURCE_LABELS.get(link.node_source, link.node_source).lower()
    node_type = names.type(link.node_type) if link.node_type else ""
    return "%s.%s  ⇄  %s.%s" % (layer_name, link.layer_field or "?",
                                node_type or "?", right or "?")


def select_data(combo, value, by_text=False):
    """Einen Eintrag anhand seiner Daten (oder seines Textes) auswählen."""
    if not value:
        return
    idx = combo.findText(value) if by_text else combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)


class LinkKeyForm(QWidget):
    """Die sieben Felder eines Join-Keys, mit einem Signal darüber.

    ``graph_info`` ist die Antwort von ``describeGraph``; daraus kommen
    Knotentypen und Properties. Niemand soll einen Property-Namen abtippen
    müssen und sich dann fragen, warum nichts zusammenfindet.
    """

    #: Ein Feld wurde geändert -- vom Benutzer, nicht durch ``load()``.
    changed = pyqtSignal()

    def __init__(self, graph_info=None, parent=None):
        super().__init__(parent)
        self._graph_info = graph_info or {"loaded": False, "types": []}
        self.names = DisplayNames(self._graph_info)
        self._loading = False       # unterdrückt Signalstürme beim Befüllen

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        self.cb_layer = QComboBox()
        for layer in vector_layers():
            self.cb_layer.addItem(layer.name(), layer.id())
        self.cb_layer.currentIndexChanged.connect(self._on_layer_changed)
        form.addRow("Layer", self.cb_layer)

        self.cb_field = QComboBox()
        self.cb_field.currentIndexChanged.connect(self._emit)
        form.addRow("Layer field", self.cb_field)

        self.cb_type = QComboBox()
        self.cb_type.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Node type", self.cb_type)

        self.cb_source = QComboBox()
        for key, label in SOURCE_LABELS.items():
            self.cb_source.addItem(label, key)
        self.cb_source.currentIndexChanged.connect(self._on_source_changed)
        form.addRow("Key in the graph", self.cb_source)

        self.cb_attr = QComboBox()
        self.cb_attr.currentIndexChanged.connect(self._emit)
        form.addRow("Property", self.cb_attr)

        self.cb_norm = QComboBox()
        for key, label in NORMALIZE_MODES.items():
            self.cb_norm.addItem(label, key)
        self.cb_norm.currentIndexChanged.connect(self._on_normalize_changed)
        form.addRow("Comparison", self.cb_norm)

        # Nur für die Stufe "Muster". Sichtbar bleibt es trotzdem immer -- ein
        # Feld, das erscheint und verschwindet, lässt das Formular springen.
        self.ed_pattern = QLineEdit()
        self.ed_pattern.setPlaceholderText(DEFAULT_PATTERN)
        self.ed_pattern.setToolTip(
            "Regular expression; the first capture group is the key. "
            "Empty means: %s" % DEFAULT_PATTERN
        )
        self.ed_pattern.textChanged.connect(self._emit)
        form.addRow("Pattern", self.ed_pattern)

        self._refill_types()
        self._refill_fields()
        self._refill_attrs()

    # -- Von außen -------------------------------------------------------------

    def set_graph_info(self, graph_info):
        """Knotentypen nachtragen, wenn der Graph erst später geladen wurde."""
        self._graph_info = graph_info or {"loaded": False, "types": []}
        self.names = DisplayNames(self._graph_info)
        keep_type = self.cb_type.currentData()
        self._loading = True
        try:
            self._refill_types()
            select_data(self.cb_type, keep_type)
            self._refill_attrs()
        finally:
            self._loading = False

    def load(self, link):
        """``LinkConfig`` -> Formular. Löst ``changed`` nicht aus."""
        self._loading = True
        try:
            select_data(self.cb_layer, link.layer_id)
            self._refill_fields()
            select_data(self.cb_field, link.layer_field, by_text=True)
            select_data(self.cb_type, link.node_type)
            select_data(self.cb_source, link.node_source)
            self._refill_attrs()
            select_data(self.cb_attr, link.node_attr)
            select_data(self.cb_norm, link.normalize)
            self.ed_pattern.setText(link.pattern or "")
            self.cb_attr.setEnabled(link.node_source == SOURCE_ATTR)
            self.ed_pattern.setEnabled(link.normalize == NORMALIZE_PATTERN)
        finally:
            self._loading = False

    def apply_to(self, link):
        """Formular -> ``LinkConfig``. Gibt dieselbe Konfiguration zurück."""
        link.layer_id = self.cb_layer.currentData() or ""
        link.layer_field = self.cb_field.currentText()
        link.node_type = self.cb_type.currentData() or ""
        link.node_source = self.cb_source.currentData() or SOURCE_ATTR
        # Daten, nicht Text: Angezeigt wird die Kurzform, gemeint ist der volle Name.
        link.node_attr = self.cb_attr.currentData() or ""
        link.normalize = self.cb_norm.currentData()
        link.pattern = self.ed_pattern.text()
        return link

    def link(self):
        """Eine frische ``LinkConfig`` aus dem, was gerade eingestellt ist."""
        return self.apply_to(LinkConfig())

    def layer_id(self):
        return self.cb_layer.currentData() or ""

    def set_editable(self, on):
        for widget in (self.cb_layer, self.cb_field, self.cb_type, self.cb_source,
                       self.cb_attr, self.cb_norm, self.ed_pattern):
            widget.setEnabled(on)
        if on:
            # Die beiden hängen an anderen Feldern und dürfen nicht pauschal
            # aufgehen -- sonst steht ein Muster-Feld offen, das nichts tut.
            self.cb_attr.setEnabled(self.cb_source.currentData() == SOURCE_ATTR)
            self.ed_pattern.setEnabled(self.cb_norm.currentData() == NORMALIZE_PATTERN)

    # -- Innenleben ------------------------------------------------------------

    def _refill_types(self):
        self.cb_type.blockSignals(True)
        self.cb_type.clear()
        for entry in self._graph_info.get("types", []):
            # Der Name, unter dem der GraphExplorer den Typ zeigt. Der volle
            # Schlüssel steht im Tooltip.
            shown = self.names.type(entry["type"])
            self.cb_type.addItem("%s  (%d)" % (shown, entry["count"]), entry["type"])
            self.cb_type.setItemData(self.cb_type.count() - 1, entry["type"], TOOLTIP_ROLE)
        self.cb_type.blockSignals(False)

    def _refill_fields(self):
        self.cb_field.blockSignals(True)
        self.cb_field.clear()
        layer_id = self.cb_layer.currentData()
        layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        if layer is not None:
            for field in layer.fields():
                self.cb_field.addItem(field.name())
        self.cb_field.blockSignals(False)

    def _refill_attrs(self):
        self.cb_attr.blockSignals(True)
        self.cb_attr.clear()
        wanted = self.cb_type.currentData()
        for entry in self._graph_info.get("types", []):
            if entry["type"] == wanted:
                for attr in entry.get("attrs", []):
                    self.cb_attr.addItem(self.names.attr(attr), attr)
                    self.cb_attr.setItemData(self.cb_attr.count() - 1, attr, TOOLTIP_ROLE)
                break
        self.cb_attr.blockSignals(False)

    def _emit(self, *_args):
        if not self._loading:
            self.changed.emit()

    def _on_layer_changed(self, _idx):
        if self._loading:
            return
        self._refill_fields()
        self._emit()

    def _on_type_changed(self, _idx):
        if self._loading:
            return
        self._refill_attrs()
        self._emit()

    def _on_source_changed(self, _idx):
        if self._loading:
            return
        self.cb_attr.setEnabled(self.cb_source.currentData() == SOURCE_ATTR)
        self._emit()

    def _on_normalize_changed(self, _idx):
        if self._loading:
            return
        self.ed_pattern.setEnabled(self.cb_norm.currentData() == NORMALIZE_PATTERN)
        self._emit()
