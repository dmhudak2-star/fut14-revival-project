#!/usr/bin/env python3
"""Suivre en direct ce que le titre demande et n'obtient pas.

    tools/watch_gaps.py                  le journal le plus récent
    tools/watch_gaps.py --component 4    n'écouter qu'un composant
    tools/watch_gaps.py --all            tous les composants sans gestionnaire

C'est l'outil qui a trouvé le matchmaking. Le serveur écrit `unknown_route`
chaque fois que le titre envoie un composant et une commande qu'il ne sait pas
traiter, et il journalise chaque requête **décodée**, tags et valeurs compris.
Entre les deux, le jeu dicte lui-même ce qu'il reste à écrire -- il a suffi
d'entrer dans Face-à-Face pour que la première trame GameManager de l'histoire
du projet apparaisse ici, en clair.

Il vivait dans un dossier temporaire, qui a été effacé entre deux sessions.
D'où sa place ici : ce projet a déjà perdu une clé SSH pour la même raison.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Les composants, par leur numéro, lus dans les constructeurs du client.
# `docs/MATCHMAKING.md` porte la table complète et dit lesquels sont annoncés
# sans exister.
COMPONENTS = {
    1: "Authentication", 4: "GameManager", 5: "Redirector", 6: "Playgroups",
    7: "Stats", 9: "Util", 10: "CensusData", 11: "Clubs", 14: "Mail",
    15: "Messaging", 21: "Rooms", 24: "CommerceInfo", 25: "AssociationLists",
    27: "GpsContentController", 28: "GameReporting", 35: "Authentication2",
    2069: "FifaCups", 2070: "CoopSeason", 2076: "SponsoredEvents",
    2077: "Easfc", 2249: "OSDKSettings", 2252: "OsdkArena",
    2268: "OsdkOnlinePass", 2270: "OSDKDigitalDownloadPreview",
    2271: "OSDKTournaments", 30722: "UserSessions",
}


def newest_journal(pattern: str) -> str | None:
    found = glob.glob(str(REPO / "runtime" / pattern))
    return max(found, key=os.path.getmtime) if found else None


def name(component: int | None) -> str:
    return f"{COMPONENTS.get(component, '?')} ({component})"


def render(record: dict, watched: set[int], everything: bool) -> None:
    kind = record.get("event")
    when = str(record.get("time") or "")[11:19]

    if kind == "unknown_route":
        component = record.get("component")
        if not everything and watched and component not in watched:
            return
        print(f"\n[{when}] SANS GESTIONNAIRE  {name(component)} "
              f"commande {record.get('command')}", flush=True)
        return

    if kind != "frame" or record.get("direction") != "request":
        return
    frame = record.get("frame") or {}
    component = frame.get("component")
    if watched and component not in watched:
        return
    print(f"\n[{when}] {name(component)} commande {frame.get('command')}", flush=True)
    for field in frame.get("fields", []):
        print("        " + json.dumps(field, ensure_ascii=False)[:400], flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--component", type=int, action="append", default=[],
                        help="n'afficher que ce composant (répétable)")
    parser.add_argument("--all", action="store_true",
                        help="tout afficher, y compris les composants gérés")
    parser.add_argument("--pattern", default="live-easw-*.jsonl",
                        help="quels journaux suivre")
    arguments = parser.parse_args(argv)
    # Sans précision, GameManager : tout le reste a un gestionnaire, et une
    # ligne par requête servie noierait celle qui compte.
    watched = set(arguments.component) or (set() if arguments.all else {4})

    path = newest_journal(arguments.pattern)
    while path is None:
        time.sleep(1)
        path = newest_journal(arguments.pattern)
    print(f"journal: {path}\n(en attente)", flush=True)

    stream = open(path, errors="replace")
    stream.seek(0, os.SEEK_END)
    while True:
        line = stream.readline()
        if not line:
            # Un relancement fait tourner le journal ; le suivre plutôt que se
            # taire pour toujours sur un fichier que plus personne n'écrit.
            latest = newest_journal(arguments.pattern)
            if latest and latest != path:
                path = latest
                stream = open(path, errors="replace")
                print(f"\n--- nouveau journal: {path}", flush=True)
                continue
            time.sleep(0.4)
            continue
        try:
            render(json.loads(line), watched, arguments.all)
        except ValueError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
