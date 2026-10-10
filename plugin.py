"""Einbindung in QGIS: Menüeintrag, Werkzeugleiste, Lebensdauer des Dockfensters."""

import os

from qgis.core import QgsMessageLog

from .compat import (
    DOCK_AREA_RIGHT,
    LOG_CRITICAL,
    WEBSOCKETS_AVAILABLE,
    WEBSOCKETS_ERROR,
    QAction,
    QIcon,
    QMessageBox,
)

MENU = "&GraphExplorer"
LOG_TAG = "GraphExplorer Link for QGIS"
ICON = os.path.join(os.path.dirname(__file__), "icon.png")


class GraphExplorerLinkPlugin(object):

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    # -- Lebenszyklus ----------------------------------------------------------

    def initGui(self):
        self.action = QAction(QIcon(ICON), "GraphExplorer Link for QGIS", self.iface.mainWindow())
        self.action.setToolTip("GraphExplorer Link for QGIS — connect layers to a knowledge graph")
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle)
        self.iface.addPluginToMenu(MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.dock is not None:
            self.dock.unload()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    # -- Aktion ----------------------------------------------------------------

    def toggle(self, checked):
        if not checked:
            if self.dock is not None:
                self.dock.hide()
            return

        if not WEBSOCKETS_AVAILABLE:
            self._websockets_missing()
            self.action.setChecked(False)
            return

        if self.dock is None:
            # Erst hier importieren: dock.py zieht wsserver.py und damit
            # QtWebSockets nach. Läge der Import oben, könnte das Plugin auf
            # einer Installation ohne dieses Modul gar nicht erst geladen
            # werden -- und die Meldung unten käme nie bei jemandem an.
            from .dock import GraphExplorerDock

            try:
                self.dock = GraphExplorerDock(self.iface, self.iface.mainWindow())
            except RuntimeError as exc:
                QMessageBox.warning(self.iface.mainWindow(), "GraphExplorer Link for QGIS", str(exc))
                QgsMessageLog.logMessage(str(exc), LOG_TAG, LOG_CRITICAL)
                self.action.setChecked(False)
                return

            self.iface.addDockWidget(DOCK_AREA_RIGHT, self.dock)
            self.dock.visibilityChanged.connect(self._on_visibility)

        self.dock.show()
        self.dock.raise_()

    def _on_visibility(self, visible):
        # Hält den Haken am Menüeintrag mit dem tatsächlichen Zustand im
        # Gleichklang, auch wenn das Fenster über sein eigenes ✕ zugeht.
        if self.action is not None:
            self.action.blockSignals(True)
            self.action.setChecked(visible)
            self.action.blockSignals(False)

    def _websockets_missing(self):
        QMessageBox.warning(
            self.iface.mainWindow(),
            "GraphExplorer Link for QGIS",
            "This plugin needs QtWebSockets, which is not available in this "
            "QGIS installation.\n\n"
            "This is unusual — the module is part of the standard installation.\n\n"
            "Message: %s" % (WEBSOCKETS_ERROR or "unknown"),
        )
        QgsMessageLog.logMessage(
            "QtWebSockets not available: %s" % WEBSOCKETS_ERROR, LOG_TAG, LOG_CRITICAL
        )
