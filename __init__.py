"""GraphExplorer Link for QGIS — QGIS-Plugin.

Verbindet Vektorlayer im Projekt mit den Knoten eines Wissensgraphen und zeigt
den GraphExplorer als Dockfenster daneben. Siehe README.md.
"""


def classFactory(iface):  # noqa: N802 -- von QGIS so verlangt
    from .plugin import GraphExplorerLinkPlugin

    return GraphExplorerLinkPlugin(iface)
