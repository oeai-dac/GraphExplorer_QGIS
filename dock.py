"""Das Dockfenster: Steuerpult für die Kopplung.

Der Explorer selbst ist der GraphExplorer im Browser -- derselbe, mit dem auch
sonst gearbeitet wird. Das Plugin bringt keinen eigenen mit, sondern hält nur
die Leitung (``wsserver.py``): Die Adresse wird hier kopiert und im
GraphExplorer unten links unter "Connect QGIS / Blender" eingefügt. Damit gibt
es genau einen Explorer, und ein Update des GraphExplorers wirkt sofort auch
für QGIS -- ohne dass das Plugin mitziehen muss.

Das Fenster ist damit klein, und das ist kein Notbehelf. Was in QGIS gebraucht
wird, ist nicht noch eine Ansicht, sondern die Auskunft, ob die Leitung steht
und was zusammengefunden hat.
"""

import os

from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import QgsMessageLog

from . import settings
from .compat import (
    ALL_DOCK_AREAS,
    LOG_INFO,
    LOG_WARNING,
    MSG_YES,
    exec_dialog,
    QApplication,
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from .config_dialog import LinkConfigDialog
from .layersync import LayerSync
from .transfer_dialog import TransferDialog
from .wsserver import BridgeServer

LOG_TAG = "GraphExplorer Link for QGIS"

#: Ab hier wird vor dem Laden gefragt. Der Explorer hält den ganzen Graphen im
#: Speicher des Browsers -- das ist bei 120.000 Knoten in Ordnung, aber es
#: dauert, und ein versehentlich gewählter Datenbestand soll nicht minutenlang
#: durch die Leitung gehen, ohne dass jemand zugestimmt hat.
LARGE_FILE_MB = 40

#: Wo es den GraphExplorer gibt -- für alle, die das Plugin vor dem Explorer
#: gefunden haben.
GRAPHEXPLORER_URL = "https://github.com/oeai-dac/GraphExplorer"

#: Die Kurzanleitung, solange nichts verbunden ist. Sie verschwindet danach --
#: wer verbunden ist, braucht sie nicht mehr, und das Fenster bleibt ruhig.
HOW_TO_CONNECT = (
    "<ol style='margin-left:-20px'>"
    "<li>Open the <a href='%s'>GraphExplorer</a> in your browser.</li>"
    "<li>Click <b>Copy connection address</b> below.</li>"
    "<li>In the GraphExplorer, click <b>Connect QGIS / Blender</b> at the "
    "bottom left, paste the address and click <b>Connect</b>.</li>"
    "</ol>"
) % GRAPHEXPLORER_URL


class GraphExplorerDock(QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__("GraphExplorer", parent)
        self.setObjectName("GraphExplorerLinkDock")
        self.setAllowedAreas(ALL_DOCK_AREAS)
        self.iface = iface

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.lbl_link = QLabel("")
        layout.addWidget(self.lbl_link)

        self.lbl_howto = QLabel(HOW_TO_CONNECT)
        self.lbl_howto.setWordWrap(True)
        self.lbl_howto.setOpenExternalLinks(True)
        layout.addWidget(self.lbl_howto)

        # Die Adresse wird nicht angezeigt, nur kopiert: Sie ist lang, trägt die
        # Zugangsmarke, und gebraucht wird sie ohnehin nur im Explorer.
        self.btn_copy = QPushButton("Copy connection address")
        self.btn_copy.setToolTip(
            "Copies the address of this QGIS session. Paste it in the "
            "GraphExplorer under \"Connect QGIS / Blender\" (bottom left)."
        )
        self.btn_copy.clicked.connect(self.copy_address)
        layout.addWidget(self.btn_copy)

        row = QHBoxLayout()
        self.btn_load = QPushButton("Load graph …")
        self.btn_load.setToolTip("Send a graph JSON file to the connected GraphExplorer.")
        self.btn_load.clicked.connect(self.choose_graph)
        self.btn_links = QPushButton("Links …")
        self.btn_links.setToolTip("Link a layer field to a node type of the graph.")
        self.btn_links.clicked.connect(self.configure_links)
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_links)
        layout.addLayout(row)

        self.btn_transfer = QPushButton("Transfer values …")
        self.btn_transfer.setToolTip(
            "Write values from the graph into the attribute table of a layer."
        )
        self.btn_transfer.clicked.connect(self.transfer_values)
        layout.addWidget(self.btn_transfer)

        self.chk_zoom = QCheckBox("Zoom to selection")
        self.chk_zoom.stateChanged.connect(self._on_zoom_toggled)
        layout.addWidget(self.chk_zoom)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self.status)

        layout.addStretch(1)
        self.setWidget(container)

        # -- Leitung --
        self.server = BridgeServer(self)
        self.ws_port = self.server.start()

        self.sync = LayerSync(self.server, iface, self)
        self.sync.status.connect(self.set_status)
        self.server.ready.connect(self._on_ready)
        self.server.closed.connect(self._on_closed)
        self.server.graph_selection_changed.connect(self.sync.on_graph_selection)
        self.server.graph_changed.connect(self._on_graph_changed)
        self.server.log.connect(lambda m: QgsMessageLog.logMessage(m, LOG_TAG, LOG_INFO))

        self._set_connected(False)
        self.set_status("")

    # -- Zustand ---------------------------------------------------------------

    def _set_connected(self, connected):
        self.lbl_link.setText("Connected to the GraphExplorer" if connected
                              else "Not connected")
        self.lbl_link.setStyleSheet(
            "font-weight: bold; color: %s;" % ("#2d6a4f" if connected else "#9d0208")
        )
        self.lbl_howto.setVisible(not connected)
        for widget in (self.btn_load, self.btn_links, self.btn_transfer):
            widget.setEnabled(connected)

    def _on_ready(self, _api_version):
        self._set_connected(True)
        mismatch = self.server.api_version_mismatch()
        if mismatch:
            self.set_status(mismatch)
            QgsMessageLog.logMessage(mismatch, LOG_TAG, LOG_WARNING)
            return

        links = settings.read_links()
        if links:
            self.sync.set_links(links)
        else:
            self.set_status("Connected. Load a graph, then set up the links.")

        remembered = settings.read_graph_path()
        if remembered and os.path.exists(remembered):
            # Nur anbieten, nicht laden -- siehe settings.py.
            self.set_status('Last used: %s  (open it with "Load graph …")'
                            % os.path.basename(remembered))

    def _on_graph_changed(self):
        """Im Explorer wurde ein anderer Graph geladen.

        Auch dann, wenn das direkt im Browser geschah -- und genau das war die
        Lücke: Ohne diese Meldung blieb die Graph-Seite des Index leer, und die
        Richtung QGIS -> Explorer funktionierte nie, während die Gegenrichtung
        (die nur die Layer-Seite braucht) tadellos lief. Ein Fehlerbild, das
        nach Zufall aussah.
        """
        if not self.sync.runtimes:
            return
        self.set_status("Another graph in the GraphExplorer — updating the matches …")
        self.sync.refresh_graph_side()

    def _on_closed(self):
        # Die Adresse bleibt für die ganze QGIS-Sitzung gültig: Der Server
        # lauscht weiter auf demselben Port mit derselben Marke.
        self._set_connected(False)
        self.set_status("Connection to the GraphExplorer lost. To reconnect, paste "
                        "the same address there again.")

    def _on_zoom_toggled(self, _state):
        self.sync.zoom_to_selection = self.chk_zoom.isChecked()

    # -- Aktionen --------------------------------------------------------------

    def ws_url(self):
        """Der Anschluss für den GraphExplorer.

        Enthält die Marke, weil der Server ohne sie abweist -- die Adresse ist
        damit selbst das Geheimnis und gehört nicht weitergereicht.
        """
        return "ws://127.0.0.1:%d/?token=%s" % (self.ws_port, self.server.token)

    def copy_address(self):
        QApplication.clipboard().setText(self.ws_url())
        if self.server.is_connected():
            self.set_status("Address copied. The GraphExplorer is already connected.")
        else:
            self.set_status("Address copied. Now paste it in the GraphExplorer under "
                            "\"Connect QGIS / Blender\" (bottom left).")

    def choose_graph(self):
        start_dir = os.path.dirname(settings.read_graph_path() or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open graph JSON", start_dir, "Graph JSON (*.json);;All files (*)"
        )
        if not path:
            return

        size_mb = os.path.getsize(path) / 1024.0 / 1024.0
        if size_mb > LARGE_FILE_MB:
            answer = QMessageBox.question(
                self, "Large dataset",
                "%s is %.0f MB. Transferring and building it may take a while. "
                "Continue?" % (os.path.basename(path), size_mb),
            )
            if answer != MSG_YES:
                return

        self.load_graph(path)

    def load_graph(self, path):
        self.set_status("Loading %s …" % os.path.basename(path))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            self.set_status("File not readable: %s" % exc)
            return

        def done(error):
            if error:
                self.set_status("Graph not readable: %s" % error)
                return
            settings.write_graph_path(path)
            self.set_status("Loaded: %s" % os.path.basename(path))
            # Knoten-IDs und Schlüssel sind neu -- der Index muss nachziehen.
            self.sync.refresh_graph_side()

        self.server.load_graph_text(text, done)

    def configure_links(self):
        def with_info(info):
            dialog = LinkConfigDialog(settings.read_links(), info, self.server.collect_keys, self)
            if exec_dialog(dialog):
                links = dialog.links()
                settings.write_links(links)
                self.sync.set_links(links)

        # describe_graph antwortet asynchron; der Dialog wird erst danach gebaut,
        # damit seine Auswahllisten von Anfang an gefüllt sind.
        self.server.describe_graph(with_info)

    def transfer_values(self):
        """Werte aus dem Graphen in die Attributtabelle eines Layers schreiben.

        Der Dialog schreibt selbst -- er hat den Layer und die gerechnete
        Bilanz -- und verwaltet auch die gespeicherten Vorlagen selbst.
        """
        def with_info(info):
            dialog = TransferDialog(
                settings.read_links(), settings.read_transfers(), info,
                self.server.describe_values, self.server.collect_values, self,
            )
            exec_dialog(dialog)
            self.set_status("Value transfer closed.")

        self.server.describe_graph(with_info)

    def set_status(self, text):
        self.status.setText(text or "")

    # -- Abbau ------------------------------------------------------------------

    def unload(self):
        self.sync.unload()
        self.server.stop()
