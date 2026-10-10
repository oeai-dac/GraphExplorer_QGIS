"""Die QGIS-Seite der Kopplung: Layer-Auswahl lesen und setzen.

Der Kern dieser Datei ist die Rückkopplungssperre. Ohne sie geht Folgendes los,
und zwar sofort und endlos:

    QGIS-Auswahl ändert sich
      -> Knoten im Explorer auswählen
      -> Explorer meldet neue Auswahl
      -> Features in QGIS auswählen
      -> QGIS-Auswahl ändert sich
      -> ...

**Wie die Sperre funktioniert -- und wie sie es zuerst nicht tat.** Der erste
Entwurf verglich die eingehende Auswahl mit der zuletzt gesetzten: Wer bekommt,
was er selbst ausgelöst hat, tut nichts. Das beendet die Schleife zwar, hat
aber ein Gedächtnis, das veraltet. Wählte jemand *in QGIS* etwas aus, blieb der
Merker auf dem Stand der letzten **Explorer**-Auswahl stehen; ging der Benutzer
später auf genau dieses Feature zurück, galt es als "haben wir selbst gesetzt"
und wurde verschluckt. Das sah nach Zufall aus: Was eben noch ging, ging
plötzlich nicht mehr und später wieder.

Verlässlich ist stattdessen ein Flag um das Setzen herum. ``selectByIds``
sendet ``selectionChanged`` **synchron** -- der Handler läuft noch innerhalb des
Aufrufs, das Flag steht also garantiert. Und selbst wenn eine QGIS-Version das
einmal verzögert zustellte, wäre der Schaden ein einzelnes Echo: Der Explorer
bekäme den Knoten, den er ohnehin schon zeigt, seine Auswahl änderte sich nicht,
und nichts liefe weiter. Die Schleife ist also auch ohne Sperre gedämpft; die
Sperre spart den überflüssigen Umlauf.
"""

from qgis.core import QgsFeatureRequest, QgsProject

from .compat import QObject, enum_member, pyqtSignal
from .linking import LinkIndex, short_names

# QgsFeatureRequest.NoGeometry wandert in neueren QGIS-Versionen in einen
# eigenen Flag-Typ. Einmal hier auflösen statt an der Aufrufstelle raten.
NO_GEOMETRY = enum_member(QgsFeatureRequest, "NoGeometry", "Flag")


class LinkRuntime(QObject):
    """Eine aktive Verknüpfung: Konfiguration, Index und der Draht zum Layer."""

    def __init__(self, config, layer, parent=None):
        super().__init__(parent)
        self.config = config
        self.layer = layer
        self.index = LinkIndex(config)
        self.report = None

    def field_index(self):
        return self.layer.fields().lookupField(self.config.layer_field)

    def rebuild_layer_side(self):
        """Feld auslesen und indizieren.

        Nur das eine Attribut und ohne Geometrie: Bei einem Layer mit
        zehntausenden Polygonen ist der Unterschied zwischen "alle Attribute
        plus Geometrie" und "eine Spalte" der zwischen spürbar und unmerklich.
        """
        idx = self.field_index()
        if idx < 0:
            self.index.set_layer_side([])
            return False
        request = QgsFeatureRequest().setSubsetOfAttributes([idx]).setFlags(NO_GEOMETRY)
        self.index.set_layer_side((f.id(), f.attribute(idx)) for f in self.layer.getFeatures(request))
        return True


class LayerSync(QObject):
    """Hält alle Verknüpfungen und vermittelt zwischen Layern und Explorer."""

    #: Sammelmeldung für die Statuszeile des Dockfensters.
    status = pyqtSignal(str)

    def __init__(self, bridge, iface, parent=None):
        super().__init__(parent)
        self.bridge = bridge      # wsserver.BridgeServer
        self.iface = iface
        self.runtimes = []
        self.zoom_to_selection = False

        # Die Sperre aus dem Modulkopf: steht auf True, solange eine Auswahl
        # aus dem Explorer in die Layer geschrieben wird. Kein Gedächtnis,
        # das veralten kann.
        self._applying = False

    # -- Auf- und Abbau -------------------------------------------------------

    def set_links(self, configs):
        """Verknüpfungen neu setzen: alte Verdrahtung lösen, neue herstellen."""
        self._disconnect_all()
        self.runtimes = []
        project = QgsProject.instance()

        problems = []
        for config in configs:
            if not (config.enabled and config.is_complete()):
                continue
            layer = project.mapLayer(config.layer_id)
            if layer is None:
                problems.append("%s: layer is not in the project" % config.name)
                continue
            runtime = LinkRuntime(config, layer, self)
            if not runtime.rebuild_layer_side():
                problems.append("%s: the layer has no field \"%s\""
                                % (config.name, config.layer_field))
                continue
            layer.selectionChanged.connect(self._on_layer_selection)
            self.runtimes.append(runtime)

        if problems:
            self.status.emit(" · ".join(problems))

        # Die Graph-Seite kommt asynchron aus dem Browser zurück.
        self.bridge.set_link_specs([r.config.js_spec() for r in self.runtimes])
        self.refresh_graph_side()

    def _disconnect_all(self):
        for runtime in self.runtimes:
            try:
                runtime.layer.selectionChanged.disconnect(self._on_layer_selection)
            except (TypeError, RuntimeError):
                # TypeError: war nicht verbunden. RuntimeError: das darunter
                # liegende C++-Objekt ist schon weg (Layer aus dem Projekt
                # entfernt). Beides ist hier kein Fehler, sondern der Normalfall
                # beim Aufräumen.
                pass

    def refresh_graph_side(self):
        """Für jede Verknüpfung die Schlüssel aus dem Graphen nachladen."""
        for runtime in self.runtimes:
            self._collect_for(runtime)

    def _collect_for(self, runtime):
        def done(entries):
            runtime.index.set_graph_side(entries or [])
            runtime.report = runtime.index.report()
            self._emit_summary()

        self.bridge.collect_keys(runtime.config.js_spec(), done)

    def _emit_summary(self):
        parts = [
            "%s: %s" % (r.config.name, r.report.summary())
            for r in self.runtimes if r.report is not None
        ]
        if parts:
            self.status.emit(" · ".join(parts))

    # -- QGIS -> Explorer -----------------------------------------------------

    def _on_layer_selection(self, *_args):
        """Ein Layer meldet eine geänderte Auswahl.

        Das Signal trägt selected/deselected/clearAndSelect; welche das im
        Einzelnen sind, spielt hier keine Rolle -- maßgeblich ist immer der
        Endzustand der Auswahl. Deshalb werden die Argumente verworfen und
        stattdessen der Layer gefragt.
        """
        if self._applying:
            return  # das schreiben wir gerade selbst hinein

        for runtime in self.runtimes:
            fids = set(runtime.layer.selectedFeatureIds())
            if not fids:
                continue

            keys = runtime.index.keys_for_feature_ids(fids)
            # ...for_normalized_keys: keys_for_feature_ids liefert schon
            # normalisierte Schlüssel, ein zweiter Durchlauf würde sie
            # verfälschen (siehe linking.py).
            node_ids = runtime.index.node_ids_for_normalized_keys(keys)
            if not node_ids:
                # Der häufigste Grund ist nicht "diese SE fehlt im Graphen",
                # sondern eine leere Graph-Seite -- etwa weil der Graph erst
                # nach dem Verbinden im Browser geladen wurde. Das steht sonst
                # nirgends, und das Fehlerbild ("eine Richtung geht, die andere
                # nie") führt in die Irre.
                if runtime.report is not None and not runtime.report.graph_keys:
                    self.status.emit(
                        "%s: the graph side is empty — open \"Links …\" and "
                        "check the matches." % runtime.config.name
                    )
                else:
                    self.status.emit(
                        "%s: no node for %s"
                        % (runtime.config.name,
                           ", ".join(short_names(keys[:3])) or "this selection")
                    )
                continue

            self.bridge.select_nodes(node_ids)
            if len(node_ids) > 1:
                self.status.emit("%s: %d nodes match, showing the first."
                                 % (runtime.config.name, len(node_ids)))
            return  # die erste Verknüpfung mit Treffer gewinnt

    # -- Explorer -> QGIS -----------------------------------------------------

    def on_graph_selection(self, _node_id, keys):
        # selectByIds sendet selectionChanged synchron -- das Flag muss also den
        # ganzen Durchlauf über stehen, nicht nur um den einzelnen Aufruf herum.
        self._applying = True
        try:
            for runtime in self.runtimes:
                fids = runtime.index.feature_ids_for_keys(keys)
                runtime.layer.selectByIds(fids)
                if fids and self.zoom_to_selection:
                    self.iface.mapCanvas().zoomToSelected(runtime.layer)
        finally:
            self._applying = False

    # -- Abbau ----------------------------------------------------------------

    def unload(self):
        self._disconnect_all()
        self.runtimes = []
