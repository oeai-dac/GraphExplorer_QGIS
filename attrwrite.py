"""Spalten anlegen und Werte hineinschreiben -- der Teil, der den Layer anfasst.

Getrennt von ``transfer.py``, weil hier alles steht, was einen echten Layer
braucht, und dort alles, was sich ohne QGIS prüfen lässt. Die Trennung ist
nicht nur formal: Was ein Datenformat an Feldnamen annimmt und was passiert,
wenn dieselbe Spalte schon da ist, entscheidet sich an der Datenquelle -- und
das lässt sich nur am laufenden Objekt herausfinden.

**Geschrieben wird über den Bearbeitungspuffer**, nicht am Layer vorbei über
den Datenprovider. Das ist langsamer und dafür widerrufbar: Der Vorgang landet
als *ein* Eintrag im Rückgängig-Stapel des Layers. Eine Übernahme, die zwei
Minuten Handarbeit überschreibt und sich nicht zurücknehmen lässt, wäre in der
Grabungspraxis nicht zu verantworten -- und ein Missgriff bei der Verknüpfung
ist wahrscheinlicher als jeder andere Fehler in diesem Plugin.
"""

from qgis.core import QgsField

from .compat import field_type
from .transfer import KIND_TEXT, sanitize_field_name

#: Wie lang ein Feldname in einem Shapefile sein darf. Kein Rundungswert,
#: sondern die Grenze des dBase-Headers: Was länger ist, kürzt der Treiber beim
#: Speichern stillschweigend -- und aus "ansprache_kurz" und "ansprache_lang"
#: wird zweimal "ansprache_".
SHAPEFILE_FIELD_LEN = 10

#: Textlänge für Shapefiles. Auch das ist eine Formatgrenze, keine Vorliebe.
SHAPEFILE_TEXT_LEN = 254


def is_shapefile(layer):
    try:
        storage = (layer.dataProvider().storageType() or "").lower()
    except AttributeError:
        storage = ""
    return "shapefile" in storage or storage == "esri shapefile"


def max_field_name_len(layer):
    """``None`` heißt: keine nennenswerte Grenze (GeoPackage, PostGIS)."""
    return SHAPEFILE_FIELD_LEN if is_shapefile(layer) else None


def existing_field_names(layer):
    return [field.name() for field in layer.fields()]


class FieldPlan(object):
    """Welche Spalte in welches Feld geht -- und ob es das schon gibt."""

    def __init__(self, column, name, index=-1, reused=False, kind_clash=""):
        self.column = column
        #: Der tatsächlich benutzte Feldname; kann vom gewünschten abweichen,
        #: wenn das Format kürzt oder der Name schon vergeben ist.
        self.name = name
        self.index = index
        self.reused = reused
        #: Nicht leer, wenn ein vorhandenes Feld einen unpassenden Typ hat.
        self.kind_clash = kind_clash


#: Feldtypen, in die diese Übernahme grundsätzlich nichts schreibt. Ein Datum
#: aus einer Zeichenkette zu erzeugen ist die Sorte Umwandlung, die entweder
#: der Provider still verweigert oder -- schlimmer -- irgendwie hinbekommt.
_OTHER_TYPES = ("date", "time", "bool", "binary", "blob", "uuid", "json")


def _field_kind(field):
    """Der grobe Typ eines vorhandenen Feldes.

    ``"text"``, ``"int"``, ``"double"`` -- oder ``"other"`` für alles, worin
    diese Übernahme nichts zu suchen hat.
    """
    name = (field.typeName() or "").lower()
    if not name:
        # Manche Provider lassen typeName() leer; dann bleibt der Weg über die
        # Qt-Typkennung. isNumeric() unterscheidet nicht zwischen ganz und
        # gebrochen -- für die Verträglichkeitsprüfung reicht das, weil ein
        # Ganzzahlwert in ein Kommazahlfeld passt.
        return "double" if field.isNumeric() else "text"
    # Vor den Zahlen prüfen: "datetime" enthält kein "int", "timestamp" aber
    # sehr wohl ein "stamp" -- und irgendwann steht hier ein Typ, der beides
    # trifft. Die engere Regel gehört nach vorn.
    if any(t in name for t in _OTHER_TYPES):
        return "other"
    if any(t in name for t in ("int", "serial")):
        return "int"
    if any(t in name for t in ("real", "double", "float", "numeric", "decimal")):
        return "double"
    return "text"


def plan_fields(layer, columns):
    """Für jede Spalte das Zielfeld bestimmen, ohne schon etwas zu ändern.

    Ein vorhandenes Feld wird **wiederverwendet**, nicht umbenannt: Wer eine
    Übernahme ein zweites Mal laufen lässt -- weil der Graph gewachsen ist --,
    will dieselbe Spalte aufgefrischt haben und nicht ``ansprache_2`` daneben.
    Genau deshalb muss aber der Typ passen: In ein Ganzzahlfeld eine Ansprache
    zu schreiben endet als leere Zelle, und zwar ohne Fehlermeldung.
    """
    max_len = max_field_name_len(layer)
    existing = existing_field_names(layer)
    lowered = {name.lower(): name for name in existing}
    taken = list(existing)

    plans = []
    for column in columns:
        wanted = sanitize_field_name(column.field, max_len=max_len)
        match = lowered.get(wanted.lower())
        if match is not None:
            field = layer.fields().field(layer.fields().lookupField(match))
            clash = ""
            have, want = _field_kind(field), column.kind
            if have == "other":
                clash = ("%s has a type (%s) this transfer does not write to"
                         % (match, field.typeName()))
            elif have == "text" and want != KIND_TEXT:
                clash = "%s is a text field, but the column holds numbers" % match
            elif have != "text" and want == KIND_TEXT:
                clash = "%s is a numeric field, but the column holds text" % match
            elif have == "int" and want == "double":
                clash = "%s is an integer field, but the column holds decimals" % match
            plans.append(FieldPlan(column, match, reused=True, kind_clash=clash))
            continue

        name = sanitize_field_name(column.field, taken=taken, max_len=max_len)
        taken.append(name)
        plans.append(FieldPlan(column, name))
    return plans


class WriteResult(object):

    def __init__(self):
        self.created = []          # neu angelegte Feldnamen
        self.reused = []           # gefüllte vorhandene Felder
        self.features = 0          # geänderte Features
        self.cells = 0             # geschriebene Zellen
        self.truncated = 0         # auf die Formatgrenze gekürzte Texte
        self.committed = False
        self.error = ""

    @property
    def ok(self):
        return not self.error

    def summary(self):
        if self.error:
            return self.error
        parts = ["%d features written" % self.features]
        if self.created:
            parts.append("new: %s" % ", ".join(self.created))
        if self.reused:
            parts.append("updated: %s" % ", ".join(self.reused))
        if self.truncated:
            parts.append("%d values cut to %d characters (format limit)"
                         % (self.truncated, SHAPEFILE_TEXT_LEN))
        parts.append("saved" if self.committed
                     else "layer is still in edit mode — check, then save or "
                          "discard the edits")
        return " · ".join(parts)


def write_values(layer, columns, values_by_fid, clear_fids=(), commit=False):
    """Der eigentliche Schreibvorgang. Gibt ein ``WriteResult`` zurück.

    ``values_by_fid``: ``{feature_id: [wert je Spalte]}`` aus
    ``transfer.build_transfer``. ``clear_fids`` sind Features, deren Zellen
    geleert werden sollen -- die ohne Knoten, wenn der Benutzer das verlangt.

    ``commit=False`` lässt den Layer im Bearbeitungsmodus stehen. Das ist die
    Vorgabe und keine Nachlässigkeit: Erst dann sieht man in der Attributtabelle
    nach, was angekommen ist, und erst dann entscheidet man sich. War der Layer
    schon vorher in Bearbeitung, wird ohnehin nie gespeichert -- sonst würde die
    Übernahme fremde, halbfertige Änderungen mit festschreiben.
    """
    result = WriteResult()

    plans = plan_fields(layer, columns)
    clashes = [p.kind_clash for p in plans if p.kind_clash]
    if clashes:
        result.error = "Field types do not match: " + " · ".join(clashes)
        return result

    was_editing = layer.isEditable()
    if not was_editing and not layer.startEditing():
        result.error = ("The layer cannot be edited. Read-only source or "
                        "missing permissions?")
        return result

    text_len = SHAPEFILE_TEXT_LEN if is_shapefile(layer) else 0

    layer.beginEditCommand("GraphExplorer: transfer values")
    try:
        for plan in plans:
            if plan.reused:
                result.reused.append(plan.name)
                continue
            kind = plan.column.kind
            length = text_len if kind == KIND_TEXT else 0
            if not layer.addAttribute(QgsField(plan.name, field_type(kind), "", length)):
                raise RuntimeError('Could not create the field "%s".' % plan.name)
            result.created.append(plan.name)

        # Erst nach dem Anlegen nachschlagen: Vorher gibt es den Index nicht,
        # und ein -1 würde stillschweigend ins Leere schreiben.
        layer.updateFields()
        for plan in plans:
            plan.index = layer.fields().lookupField(plan.name)
            if plan.index < 0:
                raise RuntimeError('The field "%s" is missing after it was created.' % plan.name)

        for fid, values in values_by_fid.items():
            touched = False
            for plan, value in zip(plans, values):
                if value is None:
                    continue
                if text_len and isinstance(value, str) and len(value) > text_len:
                    # Selbst kürzen statt den Treiber kürzen zu lassen: Der tut
                    # es beim Speichern stillschweigend, und "; " als Trenner
                    # heißt, dass abgeschnittene Zellen mitten in einem Wert
                    # enden. Wenigstens die Zahl im Bericht macht es sichtbar.
                    value = value[:text_len]
                    result.truncated += 1
                if not layer.changeAttributeValue(fid, plan.index, value):
                    raise RuntimeError("Could not change feature %s." % fid)
                result.cells += 1
                touched = True
            if touched:
                result.features += 1

        for fid in clear_fids or ():
            for plan in plans:
                # None statt QVariant(): QGIS nimmt das als NULL an, und der
                # Umweg über QVariant unterscheidet sich zwischen Qt5 und Qt6.
                if not layer.changeAttributeValue(fid, plan.index, None):
                    raise RuntimeError("Could not clear feature %s." % fid)
            result.features += 1

        layer.endEditCommand()
    except Exception as exc:                      # noqa: BLE001 - siehe unten
        # Bewusst breit: Was hier hochkommt, sind Provider-Fehler aus dem
        # C++-Unterbau, und die tragen keinen gemeinsamen Python-Typ. Wichtiger
        # als die Unterscheidung ist, dass der halb geschriebene Zustand
        # zurückgenommen wird -- eine Tabelle mit drei von acht Spalten wäre
        # schlimmer als gar keine.
        layer.destroyEditCommand()
        if not was_editing:
            layer.rollBack()
        result.error = str(exc) or exc.__class__.__name__
        return result

    if commit and not was_editing:
        if layer.commitChanges():
            result.committed = True
        else:
            errors = "; ".join(layer.commitErrors()[:3])
            result.error = "Saving failed: %s" % (errors or "unknown error")
            return result

    layer.triggerRepaint()
    return result
