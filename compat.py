"""Qt-Importe an einer Stelle gebündelt.

Zwei Dinge unterscheiden sich zwischen QGIS-Installationen, und beide würden
sonst über den halben Plugin-Code verstreut abgefragt:

1. **Qt5 gegen Qt6.** QGIS 3.x läuft auf PyQt5, QGIS 4 auf PyQt6. Für die
   normalen Widgets nimmt uns ``qgis.PyQt`` das ab -- dafür ist der Shim da.
   Was er *nicht* abnimmt, sind die Enum-Mitglieder: PyQt5 legt sie direkt an
   der Klasse ab (``Qt.RightDockWidgetArea``), PyQt6 nur noch im zugehörigen
   Enum-Typ (``Qt.DockWidgetArea.RightDockWidgetArea``). Das betrifft auch die
   Enumerationen von QGIS selbst -- ``Qgis.Info`` gibt es unter PyQt6 nicht
   mehr. Dafür ist ``enum_member`` da.

2. **Nicht jedes Modul ist überall da.** Der Shim führt eine feste Liste;
   ``QtWebSockets`` steht nicht darauf, obwohl das Modul unter ``PyQt5``
   vorhanden ist. Ein Import über ``qgis.PyQt`` schlägt dort also fehl, wo er
   nicht müsste -- und die Fehlermeldung zeigt dann auf das falsche Modul.
   Deshalb wird direkt importiert und das Ergebnis in ``WEBSOCKETS_AVAILABLE``
   festgehalten, statt einen rohen Traceback beim Laden des Plugins zu riskieren.
"""

from qgis.PyQt.QtCore import (
    QObject,
    Qt,
    QTimer,
    pyqtSignal,
)
try:
    # Qt5: QAction liegt bei den Widgets. Qt6 hat sie nach QtGui verschoben --
    # der qgis.PyQt-Shim reicht sie je nach Build noch durch, verlässlich ist
    # das aber nicht.
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from qgis.PyQt.QtGui import QAction

from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QHostAddress
from qgis.core import Qgis


def enum_member(holder, member, *scopes):
    """Ein Qt-Enum-Mitglied holen, egal ob PyQt5 oder PyQt6.

    PyQt5 legt Enum-Mitglieder direkt an der Klasse ab (``Qt.RightDockWidgetArea``),
    PyQt6 nur noch im zugehörigen Enum-Typ (``Qt.DockWidgetArea.RightDockWidgetArea``).
    Erst gescopet suchen, dann ungescopet -- so funktioniert dieselbe Zeile in
    beiden Welten, ohne dass irgendwo eine Versionsabfrage steht.
    """
    for scope in scopes:
        holder_scope = getattr(holder, scope, None)
        if holder_scope is not None and hasattr(holder_scope, member):
            return getattr(holder_scope, member)
    return getattr(holder, member)


DOCK_AREA_RIGHT = enum_member(Qt, "RightDockWidgetArea", "DockWidgetArea")
ALL_DOCK_AREAS = enum_member(Qt, "AllDockWidgetAreas", "DockWidgetArea")

# Für die Zusammenstellung per Ziehen und Fallenlassen (transfer_dialog.py).
USER_ROLE = enum_member(Qt, "UserRole", "ItemDataRole")
TOOLTIP_ROLE = enum_member(Qt, "ToolTipRole", "ItemDataRole")
ITEM_IS_EDITABLE = enum_member(Qt, "ItemIsEditable", "ItemFlag")
ITEM_IS_DROP_ENABLED = enum_member(Qt, "ItemIsDropEnabled", "ItemFlag")
COPY_ACTION = enum_member(Qt, "CopyAction", "DropAction")
DRAG_ONLY = enum_member(QAbstractItemView, "DragOnly", "DragDropMode")
DRAG_DROP = enum_member(QAbstractItemView, "DragDrop", "DragDropMode")
NO_EDIT_TRIGGERS = enum_member(QAbstractItemView, "NoEditTriggers", "EditTrigger")
EXTENDED_SELECTION = enum_member(QAbstractItemView, "ExtendedSelection", "SelectionMode")
HEADER_TO_CONTENTS = enum_member(QHeaderView, "ResizeToContents", "ResizeMode")
HEADER_STRETCH = enum_member(QHeaderView, "Stretch", "ResizeMode")


def field_type(kind):
    """Typkennung für ``QgsField``: ``"text"``, ``"int"`` oder ``"double"``.

    Die dritte Baustelle nach Enum-Scopes und Modulverfügbarkeit, und die
    unangenehmste: ``QVariant.String`` gibt es unter PyQt6 nicht mehr -- Qt6 hat
    ``QVariant::Type`` zugunsten von ``QMetaType`` abgeschafft. QGIS 3.x nimmt
    das Alte, QGIS 4 das Neue. Erst das eine versuchen, dann das andere; die
    Zahlenwerte sind für die Grundtypen ohnehin dieselben.
    """
    from qgis.PyQt.QtCore import QVariant

    names = {"text": ("String", "QString"), "int": ("Int", "Int"),
             "double": ("Double", "Double")}
    old, new = names.get(kind, names["text"])

    value = getattr(QVariant, old, None)
    if value is not None:
        return value

    from qgis.PyQt.QtCore import QMetaType
    return getattr(QMetaType.Type, new)

# Auch QGIS' eigene Enumerationen sind betroffen, nicht nur die von Qt: unter
# PyQt6 gibt es Qgis.Info nicht mehr, nur noch Qgis.MessageLevel.Info.
LOG_INFO = enum_member(Qgis, "Info", "MessageLevel")
LOG_WARNING = enum_member(Qgis, "Warning", "MessageLevel")
LOG_CRITICAL = enum_member(Qgis, "Critical", "MessageLevel")

__all__ = [
    "QObject", "Qt", "QTimer", "pyqtSignal",
    "QAction", "QApplication", "QCheckBox", "QComboBox", "QDialog", "QDialogButtonBox",
    "QDockWidget", "QFormLayout", "QHBoxLayout", "QInputDialog", "QLabel", "QMessageBox",
    "QPushButton", "QVBoxLayout", "QWidget", "QIcon",
    "QLineEdit", "QListWidget", "QListWidgetItem", "QSplitter",
    "QAbstractItemView", "QGroupBox", "QHeaderView", "QTreeWidget", "QTreeWidgetItem",
    "QHostAddress",
    "QWebSocketServer", "WEBSOCKETS_AVAILABLE", "WEBSOCKETS_ERROR",
    "NON_SECURE_MODE",
    "enum_member", "exec_dialog", "field_type",
    "DOCK_AREA_RIGHT", "ALL_DOCK_AREAS",
    "MSG_YES", "MSG_OK", "MSG_OK_CANCEL",
    "USER_ROLE", "TOOLTIP_ROLE", "ITEM_IS_EDITABLE", "ITEM_IS_DROP_ENABLED",
    "COPY_ACTION", "DRAG_ONLY", "DRAG_DROP",
    "NO_EDIT_TRIGGERS", "EXTENDED_SELECTION", "HEADER_TO_CONTENTS", "HEADER_STRETCH",
    "LOG_INFO", "LOG_WARNING", "LOG_CRITICAL",
    "BUTTONS_OK_CANCEL", "BUTTONS_CLOSE",
]

# QtWebSockets statt QtWebEngine.
#
# Der Explorer in eine QWebEngineView zu setzen scheitert daran, dass QGIS unter
# Windows kein QtWebEngine ausliefert, und QtWebKit ist für dessen Stile zu alt.
# Was in jeder Installation da ist: QtWebSockets. Der Explorer läuft im
# Browser, das Plugin hält nur die Leitung.
QWebSocketServer = None
NON_SECURE_MODE = None
WEBSOCKETS_AVAILABLE = False
WEBSOCKETS_ERROR = ""

try:
    # Nicht über qgis.PyQt: Der Shim führt QtWebEngineWidgets und
    # QtWebEngineCore, aber weder QtWebChannel noch QtWebSockets -- obwohl die
    # Module unter PyQt5 vorhanden sind. Über den Shim zu gehen ergab beim
    # ersten Versuch ein irreführendes "No module named
    # 'qgis.PyQt.QtWebChannel'", das nach einem fehlenden Modul aussah,
    # während in Wahrheit etwas ganz anderes fehlte.
    try:
        from PyQt5.QtWebSockets import QWebSocketServer  # noqa: F811
    except ImportError:
        from PyQt6.QtWebSockets import QWebSocketServer  # noqa: F811

    NON_SECURE_MODE = enum_member(QWebSocketServer, "NonSecureMode", "SslMode")
    WEBSOCKETS_AVAILABLE = True
except Exception as exc:
    WEBSOCKETS_ERROR = str(exc) or exc.__class__.__name__

BUTTONS_OK_CANCEL = (
    enum_member(QDialogButtonBox, "Ok", "StandardButton")
    | enum_member(QDialogButtonBox, "Cancel", "StandardButton")
)
BUTTONS_CLOSE = enum_member(QDialogButtonBox, "Close", "StandardButton")
MSG_YES = enum_member(QMessageBox, "Yes", "StandardButton")
MSG_OK = enum_member(QMessageBox, "Ok", "StandardButton")
MSG_OK_CANCEL = MSG_OK | enum_member(QMessageBox, "Cancel", "StandardButton")


def exec_dialog(widget):
    """``exec()`` heißt in PyQt5 ``exec_()`` -- in Python 2 war ``exec`` ein Schlüsselwort."""
    runner = getattr(widget, "exec", None) or getattr(widget, "exec_")
    return runner()

