"""Werte aus dem Graphen in die Attributtabelle -- die Rechnung dazu.

Wie ``linking.py`` bewusst **ohne QGIS- und Qt-Import**. Das Schreiben selbst
braucht einen Layer (``attrwrite.py``), aber *was* geschrieben wird, entscheidet
sich hier: aus welchem Knotenwert welche Spalte wird, was passiert, wenn zwei
Knoten auf denselben Schlüssel fallen, und was aus einer Zelle wird, für die es
keinen Wert gibt. Das ist der Teil, an dem sich echte Daten reiben, und der
soll sich ohne laufendes QGIS prüfen lassen -- ``tests/run-transfer-checks.py``
fährt ihn mit reinem Python durch.

**Die Übernahme ist an drei Stellen mehrdeutig**, und jede davon wird hier
entschieden, nicht geraten:

1. *Ein Knoten, mehrere Werte.* Eine SE enthält zwölf Funde; "Beschriftungen
   der Funde" ist keine Zelle, sondern zwölf. Die Brücke liefert deshalb eine
   Liste, und zusammengefasst wird erst hier.
2. *Mehrere Knoten, ein Schlüssel.* Zwei Knoten mit derselben SE-Nummer sind
   selten, aber es gibt sie (der Abgleichsbericht weist sie als „mehrere Knoten
   je Schlüssel" aus). Ihre Werte werden zusammengeführt wie unter 1.
3. *Mehrere Zahlen in einer Zelle.* Bei Text ist "12; 14" eine brauchbare
   Antwort, bei einer Zahlenspalte nicht. Dort gilt der Wert als **strittig**:
   Die Zelle bleibt leer und der Fall steht im Bericht. Zu summieren oder den
   ersten zu nehmen wäre geraten -- und in einer Zahlenspalte fällt Geratenes
   niemandem mehr auf.
"""

import json
import re
import unicodedata

try:
    from .linking import DisplayNames, normalize_key, short_name, short_names
except ImportError:  # pragma: no cover - als Einzelmodul geladen (tests/)
    # Dieselbe Ausnahme wie bei den Prüfungen zu linking.py: Die Tests laden das
    # Modul ohne Paket-Kontext, damit sie ohne QGIS laufen.
    from linking import DisplayNames, normalize_key, short_name, short_names


# ---------------------------------------------------------------------------
# Spaltentypen
# ---------------------------------------------------------------------------

KIND_TEXT = "text"
KIND_INT = "int"
KIND_DOUBLE = "double"

#: Reihenfolge ist die der Auswahlliste im Dialog. Text zuerst: Er nimmt alles
#: an und ist deshalb die Wahl, die nie Daten verliert.
KIND_LABELS = [
    (KIND_TEXT, "Text"),
    (KIND_INT, "Integer"),
    (KIND_DOUBLE, "Decimal"),
]

NUMERIC_KINDS = (KIND_INT, KIND_DOUBLE)

#: Womit mehrere Werte in einer Textzelle verbunden werden. Ein Semikolon,
#: weil Kommas in Ansprachen und Beschriftungen selbst vorkommen -- "Grube,
#: verfüllt" soll nicht wie zwei Werte aussehen.
SEPARATOR = "; "

_FIELD_BAD = re.compile(r"[^0-9A-Za-z_]+")
_TRAILING_NUM = re.compile(r"_(\d+)$")


# ---------------------------------------------------------------------------
# Wertquellen
# ---------------------------------------------------------------------------
#
# Eine Quelle ist ein dict, kein zusammengesetzter String (siehe die Begründung
# in explorer-bridge.js: Beziehungsnamen aus importiertem RDF tragen selbst
# Doppelpunkte). Hier interessieren nur zwei Dinge daran: eine stabile Kennung
# zum Wiedererkennen und ein brauchbarer Vorschlag für den Spaltennamen.


def source_key(source):
    """Stabile Kennung einer Quelle -- gleich für gleiche Quellen.

    ``sort_keys`` ist der Grund, warum das nicht einfach ``str(source)`` ist:
    Ohne feste Reihenfolge wären ``{"kind":"attr","attr":"se"}`` und
    ``{"attr":"se","kind":"attr"}`` zwei verschiedene Kennungen für dieselbe
    Sache -- und die Liste im Dialog hätte denselben Eintrag doppelt.
    """
    return json.dumps(source or {}, sort_keys=True, ensure_ascii=False)


def source_from_key(key):
    """Der Rückweg von ``source_key`` -- aus der Kennung wieder die Quelle.

    Damit ist die Kennung nicht nur ein Etikett, sondern die Quelle selbst in
    anderer Form. Eine Zeile in der Zieltabelle kommt deshalb ohne Verweis auf
    die Liste aus, aus der sie gezogen wurde: Wird die neu geholt, bleibt sie
    trotzdem vollständig.
    """
    try:
        source = json.loads(key or "{}")
    except (TypeError, ValueError):
        return {}
    return source if isinstance(source, dict) else {}


def source_text(source, names=None):
    """Menschenlesbare Beschreibung einer Quelle.

    Wird immer hier gebaut und nicht von der Brücke übernommen: Die Namen
    kommen aus ``names`` (``linking.DisplayNames``) und lauten damit wie im
    GraphExplorer -- "is composed of" statt der ganzen URI --, und die Liste
    spricht dieselbe Sprache wie der Rest des Plugins.
    """
    names = names or DisplayNames()
    source = source or {}
    kind = source.get("kind")
    if kind == "id":
        return "Node ID"
    if kind == "label":
        return "Node label"
    if kind == "attr":
        return names.attr(source.get("attr")) or "Property"
    if kind == "rel":
        arrow = "→ " if source.get("dir") != "in" else "← "
        what = source.get("what")
        tail = {"count": "count", "label": "labels"}.get(
            what, names.attr(source.get("attr")) or "")
        return "%s%s: %s" % (arrow, names.rel(source.get("rel")) or "?", tail)
    return "unknown source"


def default_field_name(source, names=None):
    """Vorschlag für den Spaltennamen in der Attributtabelle.

    Bewusst nah am Graphen: Wer die Property ``ansprache`` übernimmt, sucht in
    der Attributtabelle nach ``ansprache`` und nicht nach ``spalte_1``. Für
    Beziehungen wird der Name zusammengesetzt, weil derselbe Attributname an
    zwei Beziehungen hängen kann -- ``FUND_material`` und ``PROBE_material``
    sind zwei Spalten, kein Wiederholungsfall.

    Ausgangspunkt ist der Name, unter dem der GraphExplorer die Sache zeigt
    (``names``) -- sonst hieße die Spalte nach einer URI
    ``http_www_cidoc_crm_org_cidoc_crm_P3_has_note``.
    """
    names = names or DisplayNames()
    source = source or {}
    kind = source.get("kind")
    if kind == "id":
        return "node_id"
    if kind == "label":
        return "label"
    if kind == "attr":
        return sanitize_field_name(names.attr(source.get("attr")) or "value")
    if kind == "rel":
        rel = names.rel(source.get("rel")) or "rel"
        what = source.get("what")
        if what == "count":
            return sanitize_field_name(rel + "_count")
        if what == "label":
            return sanitize_field_name(rel)
        return sanitize_field_name(rel + "_" + (names.attr(source.get("attr")) or "value"))
    return "value"


_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}


def _transliterate(text):
    """``"enthält Fund"`` -> ``"enthaelt Fund"``, ``"Façade"`` -> ``"Facade"``.

    Seit die Spaltennamen aus den Anzeigenamen des Explorers entstehen, kommen
    Umlaute und Akzente vor. Sie einfach durch ``_`` zu ersetzen ergäbe
    ``enth_lt_Fund``; besser lesbar ist die übliche Umschrift.
    """
    text = "".join(_UMLAUTS.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def sanitize_field_name(name, taken=(), max_len=None):
    """Einen Spaltennamen auf das bringen, was ein Datenformat annimmt.

    Was hier abgefangen wird, sind keine Schönheitsfragen: Ein Feldname mit
    Leerzeichen oder Umlaut geht in ein GeoPackage noch durch, in ein Shapefile
    nicht, und in einem SQL-Ausdruck später ohnehin nicht ohne Anführungszeichen.
    Der Name wird deshalb an der engsten Stelle ausgerichtet.

    ``taken`` wird ohne Rücksicht auf Groß-/Kleinschreibung verglichen: Für
    OGR sind ``se`` und ``SE`` dasselbe Feld, und ein zweites anzulegen scheitert
    erst beim Speichern.
    """
    text = _transliterate(str(name or "").strip())
    text = _FIELD_BAD.sub("_", text)
    text = re.sub(r"_{2,}", "_", text).strip("_")
    if not text:
        text = "value"
    if text[0].isdigit():
        text = "_" + text            # ein Feldname darf nicht mit einer Ziffer beginnen
    if max_len:
        # Nach dem Kürzen noch einmal die Ränder: Aus "ansprache_lang" wird
        # sonst "ansprache_", und ein Feldname, der auf einem Unterstrich endet,
        # sieht nach einem Fehler aus, den niemand gemacht hat.
        text = text[:max_len].strip("_") or "value"

    lowered = set(str(t).lower() for t in (taken or ()))
    if text.lower() not in lowered:
        return text

    # Beim Anhängen von _2, _3 … darf die Obergrenze nicht gesprengt werden --
    # sonst kürzt der Treiber wieder auf denselben Namen wie vorher, und der
    # Konflikt ist zurück, nur unsichtbar.
    stem = _TRAILING_NUM.sub("", text)
    for n in range(2, 1000):
        suffix = "_%d" % n
        base = stem[:max_len - len(suffix)] if max_len else stem
        candidate = base + suffix
        if candidate.lower() not in lowered:
            return candidate
    return text


# ---------------------------------------------------------------------------
# Die Zieltabelle
# ---------------------------------------------------------------------------


class ValueColumn(object):
    """Eine Spalte der Zieltabelle: diese Quelle, dieser Feldname, dieser Typ."""

    def __init__(self, source, field="", kind=KIND_TEXT):
        self.source = source or {}
        self.field = field or default_field_name(self.source)
        self.kind = kind if kind in (KIND_TEXT, KIND_INT, KIND_DOUBLE) else KIND_TEXT

    @property
    def label(self):
        """Nur zur Anzeige -- immer aus der Quelle gebaut, nie gespeichert.

        Frühere Versionen haben die Beschriftung der Brücke mit abgelegt. Ein
        gespeicherter Text veraltet aber (Sprache, Kurzform); die Quelle nicht.
        """
        return source_text(self.source)

    def key(self):
        return source_key(self.source)

    def to_dict(self):
        return {"source": self.source, "field": self.field, "kind": self.kind}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(source=d.get("source") or {}, field=d.get("field", ""),
                   kind=d.get("kind", KIND_TEXT))


class TransferPlan(object):
    """Eine fertig zusammengestellte Übernahme: Verknüpfung plus Spalten.

    Wird im QGIS-Projekt abgelegt (siehe ``settings.py``), aus demselben Grund
    wie die Verknüpfungen selbst: Welche Werte aus dem Graphen in die
    Attributtabelle gehören, ist eine Aussage über *diese* Grabung. Und weil
    der Graph weiterwächst, ist eine Übernahme selten ein einmaliger Vorgang --
    beim nächsten Mal soll die Zusammenstellung dastehen und nicht neu
    zusammengezogen werden müssen.
    """

    def __init__(self, link, columns=None, clear_unmatched=False, name=""):
        #: Unter diesem Namen steht die Vorlage in der Auswahl. Ältere Projekte
        #: haben keinen; dann vergibt der Dialog einen aus der Verknüpfung.
        self.name = name or ""
        self.link = link
        self.columns = list(columns or [])
        #: Features ohne Knoten leeren statt in Ruhe lassen. Aus: Was jemand von
        #: Hand eingetragen hat, überlebt eine Übernahme. An: Die Spalte gibt
        #: hinterher genau den Stand des Graphen wieder, auch wo er nichts weiß.
        self.clear_unmatched = bool(clear_unmatched)

    def sources(self):
        return [c.source for c in self.columns]

    def to_dict(self):
        return {
            "name": self.name,
            "link": self.link.to_dict() if self.link else {},
            "columns": [c.to_dict() for c in self.columns],
            "clear_unmatched": self.clear_unmatched,
        }

    @classmethod
    def from_dict(cls, d, link_factory):
        d = d or {}
        return cls(
            link=link_factory(d.get("link") or {}),
            columns=[ValueColumn.from_dict(c) for c in (d.get("columns") or [])],
            clear_unmatched=bool(d.get("clear_unmatched")),
            name=str(d.get("name") or ""),
        )


# ---------------------------------------------------------------------------
# Zusammenfassen
# ---------------------------------------------------------------------------


def _flatten(value, out):
    """Einen Wert von der Brücke in die Sammelliste legen.

    Listen kommen von mehrwertigen Quellen (die Funde einer SE); alles andere
    ist ein einzelner Wert. ``None`` und Leerstring fallen weg -- eine leere
    Beschriftung ist kein Wert, sondern ein fehlender.
    """
    if value is None:
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _flatten(item, out)
        return
    text = value if isinstance(value, str) else _number_text(value)
    if text.strip() == "":
        return
    out.append(text)


def _number_text(value):
    """Zahlen ohne das ``.0``, das Python an jeden float hängt.

    Sonst stünde in der Spalte "Anzahl Funde" später ``12.0``, und in einer
    Textspalte wäre das dauerhaft so.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_number(text, kind):
    """``"12"`` -> 12, ``"12,5"`` -> 12.5, ``"12a"`` -> None.

    Das Komma als Dezimaltrennzeichen anzunehmen ist kein Entgegenkommen,
    sondern der Normalfall: Werte, die aus einer Aufnahme in einer Tabelle
    stammen, kommen deutsch geschrieben an. Zwei Kommas oder Komma *und* Punkt
    bleiben unentschieden -- das kann auch eine Aufzählung sein.
    """
    s = str(text).strip()
    if not s:
        return None
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")
    try:
        if kind == KIND_INT:
            # Über float, damit "12.0" durchgeht: Aus JSON kommt eine Anzahl
            # gelegentlich als 12.0 zurück, und die ist eine ganze Zahl.
            number = float(s)
            if number != int(number):
                return None
            return int(number)
        return float(s)
    except (TypeError, ValueError):
        return None


def merge_values(values, kind):
    """Mehrere Rohwerte zu einer Zelle. Gibt ``(wert, strittig)`` zurück.

    ``strittig`` heißt: Es gäbe mehr als eine Antwort, und der Spaltentyp lässt
    nur eine zu. Der Aufrufer schreibt dann nichts und nennt den Fall im
    Bericht -- siehe Punkt 3 im Modulkopf.
    """
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)          # Reihenfolge des ersten Auftretens
    if not seen:
        return None, False

    if kind == KIND_TEXT:
        return SEPARATOR.join(seen), False

    numbers = []
    for text in seen:
        number = parse_number(text, kind)
        if number is None:
            # Ein unlesbarer Wert in einer Zahlenspalte ist derselbe Fall wie
            # zwei verschiedene: Es gibt keine Zahl, die hier stehen dürfte.
            return None, True
        if number not in numbers:
            numbers.append(number)
    if len(numbers) > 1:
        return None, True
    return numbers[0], False


# ---------------------------------------------------------------------------
# Die Übernahme
# ---------------------------------------------------------------------------


class TransferResult(object):
    """Was geschrieben würde -- und was nicht, und warum nicht.

    Der zweite Teil ist der wichtigere. Eine Übernahme, die 40 von 150 Zeilen
    füllt, ist entweder genau richtig oder ein Missverständnis über die Daten,
    und diesen Unterschied sieht man nicht an der Spalte, sondern an den
    Zahlen daneben.
    """

    #: Wie viele strittige Fälle namentlich genannt werden.
    SAMPLE = 6

    def __init__(self, columns):
        self.columns = list(columns)
        #: {feature_id: [wert je Spalte]} -- der Auftrag ans Schreiben.
        self.values = {}
        #: {feature_id: schlüssel} -- nur für die Vorschau.
        self.keys = {}
        #: Features mit Schlüssel, zu denen es keinen Knoten gibt.
        self.unmatched = []
        #: Schlüssel im Graphen, zu denen es kein Feature gibt.
        self.unused_keys = 0
        #: Zeilen der Brücke ohne brauchbaren Schlüssel.
        self.skipped_rows = 0
        #: [(schlüssel, spaltenname, [werte])]
        self.conflicts = []
        self.features_total = 0

    def filled(self, column_index):
        n = 0
        for row in self.values.values():
            if row[column_index] is not None:
                n += 1
        return n

    @property
    def matched(self):
        return len(self.values)

    def preview(self, limit=25):
        """``[(fid, schlüssel, [werte])]`` -- die ersten Zeilen, wie sie würden."""
        fids = sorted(self.values)[:limit]
        return [(fid, self.keys.get(fid, ""), self.values[fid]) for fid in fids]

    def summary(self):
        if not self.columns:
            return "No columns put together yet."
        if not self.matched:
            return "No feature gets a value — layer field and node key do not fit together."
        parts = ["%d of %d features get values" % (self.matched, self.features_total)]
        if self.unmatched:
            parts.append("%d without a node" % len(self.unmatched))
        if self.conflicts:
            parts.append("%d ambiguous" % len(self.conflicts))
        return " · ".join(parts)

    def details(self):
        lines = [self.summary(), ""]
        for i, column in enumerate(self.columns):
            lines.append("%-20s %d values" % (column.field, self.filled(i)))
        if self.unused_keys:
            lines += ["", "%d keys in the graph without a feature in the layer." % self.unused_keys]
        if self.skipped_rows:
            lines.append("%d nodes without a usable key skipped." % self.skipped_rows)
        if self.conflicts:
            lines += ["", "Ambiguous (cell stays empty):"]
            for key, field, values in self.conflicts[: self.SAMPLE]:
                lines.append("  %s · %s: %s" % (short_name(key), field,
                                                ", ".join(short_names(values[:4]))))
            rest = len(self.conflicts) - self.SAMPLE
            if rest > 0:
                lines.append("  … (+%d more)" % rest)
        return "\n".join(lines)


def build_transfer(index, columns, rows):
    """Aus Layer-Index und Graph-Zeilen den Schreibauftrag rechnen.

    ``index`` ist ein aufgebauter ``LinkIndex`` (Layer-Seite gesetzt), ``rows``
    ist die Antwort von ``collectValues``:
    ``[{"id": …, "key": rohwert, "values": [...]}]``.

    Normalisiert wird hier nur die Graph-Seite -- die Layer-Seite hat der Index
    schon beim Aufbau normalisiert. Beide durchlaufen dieselbe Stufe, sonst
    träfe "SE 2001" sein eigenes Feature nicht.
    """
    config = index.config
    result = TransferResult(columns)

    # Erst je Schlüssel sammeln, dann zusammenfassen: Zwei Knoten mit derselben
    # SE-Nummer sollen eine Zelle ergeben und nicht zwei, die sich gegenseitig
    # überschreiben -- wer gewinnt, hinge sonst an der Reihenfolge im Graphen.
    buckets = {}
    for row in rows or []:
        key = normalize_key(row.get("key"), config.normalize, config.pattern)
        if key is None:
            result.skipped_rows += 1
            continue
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = [[] for _ in columns]
        values = row.get("values") or []
        for i in range(len(columns)):
            _flatten(values[i] if i < len(values) else None, bucket[i])

    all_fids = index.feature_ids()
    result.features_total = len(all_fids)
    covered = set()

    for key in sorted(buckets):
        fids = index.feature_ids_for_normalized_keys([key])
        if not fids:
            result.unused_keys += 1
            continue

        cells = []
        for i, column in enumerate(columns):
            value, disputed = merge_values(buckets[key][i], column.kind)
            if disputed:
                result.conflicts.append((key, column.field, list(dict.fromkeys(buckets[key][i]))))
            cells.append(value)

        # Eine Zeile ganz ohne Wert ist kein Treffer: Das Feature hätte einen
        # Knoten, der aber zu keiner der gewählten Spalten etwas beiträgt. Sie
        # trotzdem mitzuzählen würde die Bilanz beschönigen.
        if all(cell is None for cell in cells):
            continue
        for fid in fids:
            covered.add(fid)
            result.values[fid] = list(cells)
            result.keys[fid] = key

    result.unmatched = [fid for fid in all_fids if fid not in covered]
    return result
