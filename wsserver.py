"""Die Leitung zwischen QGIS und dem GraphExplorer im Browser.

Das Plugin bringt keinen eigenen Explorer mit. Es lauscht hier auf einem
lokalen Port; der GraphExplorer -- gleich, ob aus ``START-Graph-Explorer.bat``,
als Standalone-Datei oder von einem Webserver -- baut die Verbindung auf,
sobald jemand die Adresse unten links unter "Connect application" einfügt.
Ein WebSocket geht immer vom Browser aus; die Anwendungen sind die Server. So
können QGIS und Blender gleichzeitig an *einem* Explorer hängen, ohne dass eine
Anwendung von der anderen abhängt.

Das Protokoll ist bewusst schmal -- JSON in beide Richtungen, drei Formen:

    QGIS  -> Browser   {"id": 7, "method": "collectKeys", "args": [...]}
    Browser -> QGIS    {"id": 7, "result": ...}
    Browser -> QGIS    {"event": "selection", "nodeId": "...", "keys": [...]}

Die ``id`` gibt es, weil manche Aufrufe eine Antwort brauchen (welche Schlüssel
tragen die Knoten dieses Typs?) und ein WebSocket im Gegensatz zu einem
Funktionsaufruf keine mitliefert. Die Zuordnung von Antwort zu Rückruf läuft
über diese Nummer.

**Zugangsmarke.** Der Server lauscht auf 127.0.0.1, aber "lokal" heißt nicht
"nur wir": Jede andere Anwendung auf dem Rechner -- und jede beliebige Webseite
in einem offenen Browser-Tab -- könnte sich sonst verbinden und den geladenen
Graphen auslesen. Der Explorer ist ausdrücklich dafür gebaut, dass die Daten den
Rechner nicht verlassen; ein offener Port wäre ein Widerspruch dazu. Deshalb
bekommt jede Sitzung eine Zufallsmarke. Sie steht in der Adresse, die das
Dockfenster in die Zwischenablage legt (``?token=…``), und der Browser muss sie
als erste Nachricht vorzeigen. Nebeneffekt: Ein liegengebliebener Tab der
letzten QGIS-Sitzung kommt nicht wieder herein.
"""

import json
import secrets

from .compat import (
    NON_SECURE_MODE,
    QHostAddress,
    QObject,
    QWebSocketServer,
    pyqtSignal,
)

#: Vertragsversion, die der GraphExplorer (``src/main.jsx``, ``API_VERSION``)
#: mindestens melden muss.
#:
#: Geprüft wird auf *mindestens*, nicht auf *genau*: Die Brücke bekommt
#: Methoden hinzu, verliert aber keine. Ein neuerer Build ist für ein älteres
#: Plugin also unschädlich -- und weil beide Wirte (QGIS, Blender) an derselben
#: Seite hängen, wäre eine Gleichheitsprüfung sogar schädlich: Jede Erweiterung
#: für den einen würde den anderen lahmlegen, obwohl ihm nichts fehlt.
#:
#: 2: describeValues/collectValues für die Werteübernahme.
#: 3: Anzeigenamen in describeGraph (``labels``). Nicht verlangt -- ohne sie
#:    kürzt ``linking.DisplayNames`` die Schlüssel selbst.
EXPECTED_API_VERSION = 2

#: In wie großen Stücken ein Graph übertragen wird. Über einen WebSocket wäre
#: mehr am Stück möglich, aber Qt puffert eingehende Nachrichten vollständig im
#: Speicher, bevor es sie ausliefert -- und ein Grabungsbestand kann zweistellig
#: viele Megabyte haben.
CHUNK_CHARS = 2 * 1024 * 1024


class BridgeServer(QObject):
    """Nimmt genau eine Browser-Verbindung an und spricht mit ihr."""

    #: Der Browser hat sich mit gültiger Marke gemeldet. Argument: api_version.
    ready = pyqtSignal(int)

    #: Die Verbindung ist weg (Tab geschlossen, Browser beendet).
    closed = pyqtSignal()

    #: Im Explorer wurde etwas ausgewählt: node_id, keys.
    graph_selection_changed = pyqtSignal(str, list)

    #: Im Explorer wurde ein anderer Graph geladen -- gleichgültig, ob von hier
    #: aus oder direkt im Browser. Der Index muss dann neu
    #: aufgebaut werden.
    graph_changed = pyqtSignal()

    #: Meldung aus der Brücke fürs QGIS-Protokoll.
    log = pyqtSignal(str)

    #: Beschriftung in der Verbindungsleiste der Seite. Seit dort mehrere
    #: Anwendungen gleichzeitig hängen können, ist ein Name aussagekräftiger
    #: als eine Portnummer.
    HOST_NAME = "QGIS"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.token = secrets.token_urlsafe(16)
        self._server = QWebSocketServer("GraphExplorer Link", NON_SECURE_MODE, self)
        self._client = None
        self._authenticated = False
        self._next_id = 1
        self._pending = {}     # id -> callback
        self._api_version = None

    # -- Auf- und Abbau --------------------------------------------------------

    def start(self):
        """Lauscht auf einem freien Port auf 127.0.0.1. Gibt den Port zurück.

        Port 0 heißt "such dir einen": ein fester Port wäre eine unnötige
        Kollisionsquelle, und die Nummer muss ohnehin in die URL.
        """
        if not self._server.listen(QHostAddress.LocalHost, 0):
            raise RuntimeError(
                "Could not open a local port: %s" % self._server.errorString()
            )
        self._server.newConnection.connect(self._on_new_connection)
        return int(self._server.serverPort())

    def stop(self):
        self._drop_client()
        if self._server.isListening():
            self._server.close()

    def is_connected(self):
        return self._client is not None and self._authenticated

    # -- Verbindungen ----------------------------------------------------------

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is None:
            return

        if self._client is not None:
            # Nur eine Sitzung gleichzeitig. Ein zweiter Tab würde sonst
            # stillschweigend die Auswahl des ersten überschreiben, und niemand
            # könnte sich erklären, warum die Karte springt.
            socket.close()
            socket.deleteLater()
            self.log.emit("Further connection refused — a GraphExplorer is already connected.")
            return

        self._client = socket
        self._authenticated = False
        socket.textMessageReceived.connect(self._on_message)
        socket.disconnected.connect(self._on_disconnected)

    def _on_disconnected(self):
        self._drop_client()
        self.closed.emit()

    def _drop_client(self):
        if self._client is not None:
            try:
                self._client.close()
                self._client.deleteLater()
            except RuntimeError:
                pass  # C++-Objekt schon abgeräumt
        self._client = None
        self._authenticated = False
        self._api_version = None
        # Offene Rückrufe würden sonst ewig auf eine Antwort warten, die nicht
        # mehr kommt. Sie einmal mit None zu bedienen, hält die aufrufende
        # Seite schlicht.
        pending, self._pending = self._pending, {}
        for callback in pending.values():
            if callback:
                callback(None)

    # -- Eingehend -------------------------------------------------------------

    def _on_message(self, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return

        if not self._authenticated:
            # Bis die Marke stimmt, wird nichts anderes angesehen.
            if msg.get("event") == "hello" and secrets.compare_digest(
                str(msg.get("token") or ""), self.token
            ):
                self._authenticated = True
                self._api_version = int(msg.get("apiVersion") or 0)
                self._send({"event": "host", "name": self.HOST_NAME})
                self.ready.emit(self._api_version)
            else:
                self.log.emit("Connection without a valid token refused.")
                self._drop_client()
            return

        if "id" in msg:
            callback = self._pending.pop(msg["id"], None)
            if callback:
                callback(msg.get("result"))
            return

        event = msg.get("event")
        if event == "selection":
            keys = [str(k) for k in (msg.get("keys") or [])]
            self.graph_selection_changed.emit(str(msg.get("nodeId") or ""), keys)
        elif event == "graph":
            self.graph_changed.emit()
        elif event == "log":
            self.log.emit(str(msg.get("message") or ""))

    # -- Ausgehend -------------------------------------------------------------

    def _send(self, payload):
        if self._client is None or not self._authenticated:
            return False
        self._client.sendTextMessage(json.dumps(payload, ensure_ascii=False))
        return True

    def call(self, method, args=None, callback=None):
        """Eine Methode im Browser aufrufen, optional mit Rückruf für das Ergebnis."""
        message = {"method": method, "args": list(args or [])}
        if callback is not None:
            message["id"] = self._next_id
            self._pending[self._next_id] = callback
            self._next_id += 1
        if not self._send(message) and callback is not None:
            # Nicht verbunden: den Rückruf sofort bedienen, statt ihn liegen zu
            # lassen. Die aufrufende Seite muss so nur einen Fall behandeln.
            self._pending.pop(message["id"], None)
            callback(None)

    # -- Was das Plugin braucht ------------------------------------------------

    def set_link_specs(self, specs):
        self.call("setSpecs", [specs])

    def describe_graph(self, callback):
        self.call("describeGraph", [], lambda result: callback(result or {"loaded": False, "types": []}))

    def collect_keys(self, spec, callback):
        self.call("collectKeys", [spec], lambda result: callback(result or []))

    def describe_values(self, spec, callback):
        """Welche Werte die Knoten dieser Verknüpfung beisteuern könnten."""
        self.call("describeValues", [spec],
                  lambda result: callback(result or {"total": 0, "values": []}))

    def collect_values(self, spec, sources, callback):
        """Die Werte selbst -- ``[{"id", "key", "values": [...]}]``."""
        self.call("collectValues", [spec, list(sources)],
                  lambda result: callback(result or []))

    def select_nodes(self, node_ids):
        self.call("selectNodes", [list(node_ids)])

    def load_graph_text(self, text, callback=None):
        """Graph-JSON stückweise übertragen; der letzte Teil löst das Parsen aus."""
        self.call("abortGraphLoad")
        pieces = _split_chunks(text, CHUNK_CHARS)
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            self.call("loadGraphChunk", [piece, last], callback if last else None)

    def api_version_mismatch(self):
        if self._api_version is None or self._api_version >= EXPECTED_API_VERSION:
            return None
        return (
            "The GraphExplorer is older than this plugin (it reports interface "
            "version %s, at least %s is needed). Please update the GraphExplorer."
            % (self._api_version, EXPECTED_API_VERSION)
        )


def _split_chunks(text, size):
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]
