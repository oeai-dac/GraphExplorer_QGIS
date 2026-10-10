"""Prüft transfer.py -- die Werteübernahme in die Attributtabelle.

Läuft mit reinem Python, ohne QGIS:

    python GraphExplorer_QGIS/tests/run-transfer-checks.py

Geprüft wird das, worin sich eine Übernahme irren kann, ohne dass es auffällt:
zwei Knoten auf einem Schlüssel, ein Fund ohne Wert, eine Zahl, die keine ist,
und ein Feldname, den das Datenformat so nicht annimmt. Eine falsch gefüllte
Spalte sieht in der Attributtabelle genauso aus wie eine richtige -- deshalb
liegt das Gewicht hier auf den Fällen, die *nicht* geschrieben werden dürfen.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linking import (  # noqa: E402
    NORMALIZE_DIGITS,
    NORMALIZE_LOOSE,
    NORMALIZE_PATTERN,
    SOURCE_ATTR,
    DisplayNames,
    LinkConfig,
    LinkIndex,
)
from transfer import (  # noqa: E402
    KIND_DOUBLE,
    KIND_INT,
    KIND_TEXT,
    SEPARATOR,
    TransferPlan,
    ValueColumn,
    build_transfer,
    default_field_name,
    merge_values,
    parse_number,
    sanitize_field_name,
    source_from_key,
    source_key,
    source_text,
)

failures = []


def check(name, got, want):
    if got != want:
        failures.append("%s\n    erwartet: %r\n    bekommen: %r" % (name, want, got))


def config(normalize=NORMALIZE_LOOSE, pattern=""):
    return LinkConfig(
        layer_id="L1", layer_field="SE", node_type="Befund",
        node_source=SOURCE_ATTR, node_attr="se", normalize=normalize, pattern=pattern,
    )


def indexed(pairs, normalize=NORMALIZE_LOOSE, pattern=""):
    index = LinkIndex(config(normalize, pattern))
    index.set_layer_side(pairs)
    return index


# -- Kennungen ---------------------------------------------------------------
#
# Die Kennung ist zugleich der Rückweg zur Quelle: Daran hängt, dass eine Zeile
# in der Zieltabelle die Liste überlebt, aus der sie gezogen wurde.

ATTR = {"kind": "attr", "attr": "ansprache"}
COUNT = {"kind": "rel", "dir": "out", "rel": "ENTHAELT_FN", "what": "count"}

check("Kennung und Rückweg sind dasselbe", source_from_key(source_key(ATTR)), ATTR)
check("Reihenfolge im dict ändert die Kennung nicht",
      source_key({"kind": "attr", "attr": "se"}),
      source_key({"attr": "se", "kind": "attr"}))
check("kaputte Kennung ergibt keine Quelle", source_from_key("{kein json"), {})

# Beziehungsnamen aus importiertem RDF tragen Doppelpunkte -- der Grund, warum
# Quellen keine zusammengesetzten Strings sind.
rdf = {"kind": "rel", "dir": "out", "rel": "crm:P46_is_composed_of", "what": "label"}
check("Doppelpunkt im Beziehungsnamen übersteht die Kennung",
      source_from_key(source_key(rdf)), rdf)


# -- Feldnamen ---------------------------------------------------------------

check("Vorschlag aus einem Attribut", default_field_name(ATTR), "ansprache")
check("Vorschlag aus einer Anzahl", default_field_name(COUNT), "ENTHAELT_FN_count")
check("Vorschlag aus einem Nachbarattribut",
      default_field_name({"kind": "rel", "dir": "in", "rel": "FUND", "what": "attr",
                          "attr": "material"}),
      "FUND_material")

# URIs aus importiertem RDF: Spaltenname und Anzeige nur mit der Kurzform.
CRM = "http://www.cidoc-crm.org/cidoc-crm/"
check("URI-Attribut wird kurz",
      default_field_name({"kind": "attr", "attr": CRM + "P3_has_note"}), "P3_has_note")
check("URI-Beziehung wird kurz",
      default_field_name({"kind": "rel", "dir": "out", "rel": CRM + "P46_is_composed_of",
                          "what": "count"}), "P46_is_composed_of_count")
check("URI-Fragment wird kurz",
      default_field_name({"kind": "attr", "attr": "http://example.org/ont#se_nr"}), "se_nr")
check("Anzeige einer URI-Beziehung (ohne Namen vom Explorer)",
      source_text({"kind": "rel", "dir": "in", "rel": CRM + "P46_is_composed_of",
                   "what": "attr", "attr": CRM + "P2_has_type"}),
      "← P46 is composed of: P2 has type")
check("Anzeige einer Anzahl", source_text(COUNT), "→ ENTHAELT FN: count")

# Mit den Namen, die der GraphExplorer mitschickt, heißt alles wie dort --
# in der Liste und im Vorschlag für den Spaltennamen.
NAMES = DisplayNames({"labels": {
    "types": {}, "attrs": {"material": "Material"},
    "rels": {"ENTHAELT_FN": "enthält Fund", CRM + "P46_is_composed_of": "is composed of"},
}})
check("Anzeige mit Explorer-Namen", source_text(COUNT, NAMES), "→ enthält Fund: count")
check("Nachbarattribut mit Explorer-Namen",
      source_text({"kind": "rel", "dir": "out", "rel": "ENTHAELT_FN", "what": "attr",
                   "attr": "material"}, NAMES), "→ enthält Fund: Material")
check("Spaltenname aus Explorer-Namen, Umlaut umschrieben",
      default_field_name(COUNT, NAMES), "enthaelt_Fund_count")
check("Spaltenname aus URI-Beziehung mit Explorer-Namen",
      default_field_name({"kind": "rel", "dir": "out", "rel": CRM + "P46_is_composed_of",
                          "what": "label"}, NAMES), "is_composed_of")
check("Akzente werden umschrieben", sanitize_field_name("Façade été"), "Facade_ete")

check("Leerzeichen und Umlaute weg", sanitize_field_name("Anzahl Funde"), "Anzahl_Funde")
check("Sonderzeichen zusammengefasst", sanitize_field_name("crm:P46 (alt)"), "crm_P46_alt")
check("beginnt nicht mit einer Ziffer", sanitize_field_name("2001er"), "_2001er")
check("leer wird nicht leer", sanitize_field_name("  "), "value")

check("belegter Name weicht aus",
      sanitize_field_name("se", taken=["SE"]), "se_2")
check("und weicht weiter aus",
      sanitize_field_name("se", taken=["se", "se_2"]), "se_3")

# Die Shapefile-Grenze: Ein zu langer Name wird vom Treiber gekürzt, und zwei
# gekürzte Namen können gleich sein. Das Ausweichen muss deshalb *innerhalb*
# der Grenze passieren -- sonst ist der Konflikt zurück, nur unsichtbar.
check("Grenze wird eingehalten",
      sanitize_field_name("ansprache_lang", max_len=10), "ansprache")
check("Ausweichen bleibt innerhalb der Grenze",
      sanitize_field_name("ansprache_lang", taken=["ansprache"], max_len=10), "ansprach_2")
check("Ausweichen zählt weiter, ohne zu wachsen",
      sanitize_field_name("ansprache", taken=["ansprache", "ansprach_2"], max_len=10),
      "ansprach_3")


# -- Zahlen ------------------------------------------------------------------

check("Ganzzahl", parse_number("12", KIND_INT), 12)
check("Ganzzahl aus JSON-Float", parse_number("12.0", KIND_INT), 12)
check("keine Ganzzahl", parse_number("12.5", KIND_INT), None)
check("Kommazahl deutsch", parse_number("12,5", KIND_DOUBLE), 12.5)
check("Kommazahl englisch", parse_number("12.5", KIND_DOUBLE), 12.5)
# Zwei Kommas können auch eine Aufzählung sein; wer das als Zahl liest, erfindet
# eine.
check("zwei Kommas bleiben unentschieden", parse_number("1,5,7", KIND_DOUBLE), None)
check("Text ist keine Zahl", parse_number("12a", KIND_DOUBLE), None)


# -- Zusammenfassen ----------------------------------------------------------

check("Text: mehrere Werte werden verbunden",
      merge_values(["Grube", "verfüllt"], KIND_TEXT), ("Grube" + SEPARATOR + "verfüllt", False))
check("Text: gleiche Werte nur einmal",
      merge_values(["Grube", "Grube"], KIND_TEXT), ("Grube", False))
check("Text: nichts bleibt nichts", merge_values([], KIND_TEXT), (None, False))

check("Zahl: ein Wert", merge_values(["12"], KIND_INT), (12, False))
check("Zahl: derselbe Wert doppelt", merge_values(["12", "12"], KIND_INT), (12, False))
# Der Kern der Sache: In einer Zahlenspalte gibt es kein "12; 14". Zu summieren
# oder den ersten zu nehmen wäre geraten -- und in einer Zahlenspalte fällt
# Geratenes niemandem mehr auf.
check("Zahl: zwei Werte sind strittig", merge_values(["12", "14"], KIND_INT), (None, True))
check("Zahl: unlesbar ist strittig", merge_values(["etwa 12"], KIND_INT), (None, True))


# -- Die Übernahme -----------------------------------------------------------

COLUMNS = [
    ValueColumn(ATTR, field="ansprache", kind=KIND_TEXT),
    ValueColumn(COUNT, field="funde", kind=KIND_INT),
]

index = indexed([(1, "SE 2001"), (2, "SE 2002"), (3, "se  2001"), (4, None), (5, "SE 2999")])
rows = [
    {"id": "B_1", "key": "SE 2001", "values": ["Grube", 3]},
    {"id": "B_2", "key": "SE 2002", "values": [None, 0]},
    {"id": "B_9", "key": "SE 3000", "values": ["Mauer", 1]},   # kein Feature
    {"id": "B_0", "key": "", "values": ["übergangen", 1]},
]
result = build_transfer(index, COLUMNS, rows)

check("Feature bekommt seine Werte", result.values[1], ["Grube", 3])
# Feature 3 trägt "se  2001" -- derselbe Schlüssel nach der Normalisierung.
# Dass beide dieselbe Zeile bekommen, ist der Zweck des n:m-Index.
check("zweites Feature mit demselben Schlüssel bekommt dasselbe",
      result.values[3], ["Grube", 3])
check("Null ist ein Wert, kein fehlender", result.values[2], [None, 0])
check("Feature ohne Knoten bleibt außen vor", 5 in result.values, False)
check("Feature ohne Schlüsselwert steht gar nicht im Index", 4 in result.values, False)
check("Schlüssel im Graphen ohne Feature", result.unused_keys, 1)
check("Zeile ohne brauchbaren Schlüssel", result.skipped_rows, 1)
check("Bilanz: getroffen", result.matched, 3)
check("Bilanz: ohne Knoten", sorted(result.unmatched), [5])
check("Bilanz: Features insgesamt", result.features_total, 4)
check("gefüllte Zellen je Spalte", [result.filled(0), result.filled(1)], [2, 3])

# Eine Zeile, zu der es zwar einen Knoten gibt, die aber zu keiner gewählten
# Spalte etwas beiträgt, ist kein Treffer. Sie mitzuzählen würde die Bilanz
# beschönigen -- und die Bilanz ist der einzige Grund, warum man vor dem
# Schreiben hinsieht.
leer = build_transfer(indexed([(1, "SE 2001")]), COLUMNS,
                      [{"id": "B_1", "key": "SE 2001", "values": [None, None]}])
check("Knoten ohne Werte ist kein Treffer", leer.matched, 0)
check("und gilt als nicht getroffen", leer.unmatched, [1])


# -- Mehrere Knoten auf einem Schlüssel --------------------------------------

zwei = build_transfer(
    indexed([(1, "SE 2001")]), COLUMNS,
    [
        {"id": "B_1", "key": "SE 2001", "values": ["Grube", 3]},
        {"id": "B_2", "key": "se 2001", "values": ["Mauer", 5]},
    ],
)
check("Text beider Knoten kommt zusammen",
      zwei.values[1][0], "Grube" + SEPARATOR + "Mauer")
check("die Zahl bleibt leer, weil sie strittig ist", zwei.values[1][1], None)
check("und der Fall steht im Bericht", len(zwei.conflicts), 1)
check("mit Schlüssel und Spaltennamen",
      (zwei.conflicts[0][0], zwei.conflicts[0][1]), ("se 2001", "funde"))


# -- Mehrwertige Quellen -----------------------------------------------------

viele = build_transfer(
    indexed([(1, "SE 2001")]),
    [ValueColumn({"kind": "rel", "dir": "out", "rel": "FUND", "what": "label"},
                 field="funde", kind=KIND_TEXT)],
    [{"id": "B_1", "key": "SE 2001", "values": [["Fund 17", "Fund 18", "Fund 17"]]}],
)
check("Liste wird zu einer Zelle, Doppeltes fällt weg",
      viele.values[1][0], "Fund 17" + SEPARATOR + "Fund 18")


# -- Zahlen kommen als Zahlen an ---------------------------------------------
#
# Aus JSON kommt eine Anzahl als int oder float zurück. In einer *Text*spalte
# darf daraus nicht "3.0" werden -- das stünde dann dauerhaft so in der
# Attributtabelle.
text_zahl = build_transfer(
    indexed([(1, "SE 2001")]),
    [ValueColumn(COUNT, field="funde", kind=KIND_TEXT)],
    [{"id": "B_1", "key": "SE 2001", "values": [3.0]}],
)
check("Zahl in einer Textspalte ohne .0", text_zahl.values[1][0], "3")


# -- Die Normalisierung gilt auf beiden Seiten -------------------------------
#
# Dieselbe Falle wie bei der Auswahl-Kopplung: Bei "Muster" ist die
# Normalisierung nicht wiederholbar. Aus "EDP22-SE2004" wird "2004", und darin
# findet das Muster nichts mehr. Die Layer-Seite ist beim Aufbau des Index schon
# normalisiert; nur die Graph-Seite darf hier noch durch.
muster = build_transfer(
    indexed([(1, "EDP22-SE2004")], NORMALIZE_PATTERN, r"SE[\s_-]*(\d+)"),
    [ValueColumn(ATTR, field="ansprache", kind=KIND_TEXT)],
    [{"id": "B_1", "key": "SE 2004", "values": ["Grube"]}],
    )
check("Muster-Modus findet über beide Seiten zusammen", muster.values[1], ["Grube"])

ziffern = build_transfer(
    indexed([(1, "SE 2001")], NORMALIZE_DIGITS),
    [ValueColumn(ATTR, field="ansprache", kind=KIND_TEXT)],
    [{"id": "B_1", "key": "2001", "values": ["Grube"]}],
)
check("Nur-Ziffern findet über beide Seiten zusammen", ziffern.values[1], ["Grube"])


# -- Ablage im Projekt -------------------------------------------------------

plan = TransferPlan(link=config(), columns=COLUMNS, clear_unmatched=True,
                    name="SE: interpretation")
back = TransferPlan.from_dict(plan.to_dict(), LinkConfig.from_dict)
check("Name der Vorlage übersteht die Ablage", back.name, "SE: interpretation")
check("ältere Ablage ohne Name", TransferPlan.from_dict({"link": {}}, LinkConfig.from_dict).name, "")
check("Zusammenstellung übersteht die Ablage",
      [(c.field, c.kind, c.source) for c in back.columns],
      [(c.field, c.kind, c.source) for c in COLUMNS])
check("und die Verknüpfung dazu", back.link.to_dict(), config().to_dict())
check("und der Schalter fürs Leeren", back.clear_unmatched, True)

# Ältere Projekte haben die Beschriftung der Brücke mit abgelegt (deutsch, mit
# voller URI). Sie wird beim Laden ignoriert und aus der Quelle neu gebaut.
alt = ValueColumn.from_dict({"source": ATTR, "field": "ansprache", "kind": KIND_TEXT,
                             "label": "Beschriftung des Knotens"})
check("alte Beschriftung wird nicht übernommen", alt.label, source_text(ATTR))
check("und nicht wieder gespeichert", "label" in alt.to_dict(), False)


# -- Ergebnis ----------------------------------------------------------------

if failures:
    print("\n%d Prüfung(en) fehlgeschlagen:\n" % len(failures))
    for f in failures:
        print("  - %s\n" % f)
    sys.exit(1)
print("transfer.py: alle Prüfungen bestanden.")
