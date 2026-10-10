"""Verknüpfungen im QGIS-Projekt ablegen.

Bewusst im Projekt und nicht in den QGIS-Einstellungen: Eine Verknüpfung sagt
"das Feld SE dieses Layers meint denselben Befund wie die Property se dieses
Knotentyps". Das ist eine Aussage über *diese* Grabung, nicht über die
Installation. Wer die .qgz weitergibt, gibt die Verknüpfung mit; wer ein anderes
Projekt öffnet, findet nicht die Zuordnung des vorigen vor.

Der Pfad zur Graph-Datei liegt daneben -- aus demselben Grund, mit demselben
Vorbehalt: Er wird beim Öffnen nur *angeboten*, nicht automatisch geladen.
Ein Projekt, das beim Öffnen ungefragt eine mehrere Megabyte große Datei
einliest, wäre in der Grabungspraxis eine Zumutung.
"""

import json

from qgis.core import QgsProject

from .linking import LinkConfig
from .transfer import TransferPlan

SCOPE = "GraphExplorerLink"
KEY_LINKS = "links"
KEY_GRAPH_PATH = "graphPath"
KEY_TRANSFERS = "transfers"


def read_links(project=None):
    """Liste von ``LinkConfig`` aus dem Projekt."""
    project = project or QgsProject.instance()
    raw, ok = project.readEntry(SCOPE, KEY_LINKS, "")
    if not ok or not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        # Ein beschädigter Eintrag darf das Plugin nicht am Starten hindern --
        # dann eben ohne Verknüpfungen, der Dialog kann sie neu anlegen.
        return []
    return [LinkConfig.from_dict(d) for d in data if isinstance(d, dict)]


def write_links(links, project=None):
    project = project or QgsProject.instance()
    payload = json.dumps([link.to_dict() for link in links], ensure_ascii=False)
    project.writeEntry(SCOPE, KEY_LINKS, payload)


def read_transfers(project=None):
    """Gespeicherte Vorlagen für die Werteübernahme.

    Liegt aus demselben Grund im Projekt wie die Verknüpfungen: Welche Werte
    aus dem Graphen in die Attributtabelle gehören, ist eine Aussage über diese
    Grabung. Und weil der Graph weiterwächst, ist eine Übernahme selten ein
    einmaliger Vorgang -- beim nächsten Mal soll die Tabelle dastehen.
    """
    project = project or QgsProject.instance()
    raw, ok = project.readEntry(SCOPE, KEY_TRANSFERS, "")
    if not ok or not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [TransferPlan.from_dict(d, LinkConfig.from_dict)
            for d in data if isinstance(d, dict)]


def write_transfers(plans, project=None):
    project = project or QgsProject.instance()
    payload = json.dumps([plan.to_dict() for plan in plans], ensure_ascii=False)
    project.writeEntry(SCOPE, KEY_TRANSFERS, payload)


def _same_name(a, b):
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def save_transfer(plan, project=None):
    """Eine Vorlage unter ihrem Namen speichern; gleichnamige wird ersetzt.

    Gespeichert wird nur auf ausdrücklichen Wunsch ("Save …" im Dialog). Das
    frühere stille Merken beim Schließen hat jede Zusammenstellung
    überschrieben, auch wenn nur hineingeschaut wurde -- und Einträge für
    längst gelöschte Layer ließen sich nicht mehr loswerden.
    """
    if not plan or not plan.name:
        return
    plans = [p for p in read_transfers(project) if not _same_name(p.name, plan.name)]
    plans.append(plan)
    plans.sort(key=lambda p: p.name.casefold())
    write_transfers(plans, project)


def delete_transfer(name, project=None):
    plans = [p for p in read_transfers(project) if not _same_name(p.name, name)]
    write_transfers(plans, project)


def read_graph_path(project=None):
    project = project or QgsProject.instance()
    raw, ok = project.readEntry(SCOPE, KEY_GRAPH_PATH, "")
    return raw if ok else ""


def write_graph_path(path, project=None):
    project = project or QgsProject.instance()
    project.writeEntry(SCOPE, KEY_GRAPH_PATH, path or "")
