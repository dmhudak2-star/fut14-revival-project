#!/usr/bin/env python3
"""Assemble the console-side package -- what a player downloads.

`package_server.py` builds the half that answers the game. This builds the half
that patches the console, and the two are downloaded by different people: the
server is hosted once by whoever runs the revival, the client is run by every
player, next to their own console.

    tools/package_client.py --output fifa14-revival-client.tgz

The file list is **computed**, not written down. `revival_client` runs four
patchers as subprocesses and those import a couple of dozen modules between
them, several of which exist only to be armed by a flag nobody passes -- but
they are imported at module scope, so a package without them fails on the first
launch rather than on the flag. Walking the imports gets that right; a hand
list gets it right until the next import is added.

Pure standard library, all of it, which is what lets this run under Termux on
a phone. `capstone` in requirements.txt belongs to the disassembly tools and
nothing on this path imports one.

Never in here: game files, club saves, journals, `xbdm.xex`, Dashlaunch, or
anything else that is not ours to hand out. The player brings those.
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"

# Where the walk starts: the client, and the four programs it runs.
ROOTS = (
    "revival_client",
    "fifa14_early_local_server",
    "fifa14_easfc_endpoint_patch",
    "fifa14_tu3_helperfunctions_runtime_patch",
    "xbox360_virtual_input",
)

EXTRA = ("fifa14revival.example.ini", "NOTICE.md")

TOP = "fifa14-revival-client"

README = """# FIFA 14 Ultimate Team -- le client console

Ce paquet lance FIFA 14 et applique les correctifs. Il ne contient **aucun
fichier du jeu** et aucun serveur : le serveur est ailleurs, et son adresse se
met dans `fifa14revival.ini`.

## Ce qu'il te faut

* une Xbox 360 **RGH ou JTAG**, avec **Dashlaunch** et **XBDM chargé en
  plugin**. Concrètement une ligne dans le `launch.ini` que Dashlaunch lit
  vraiment :

      plugin4 = Usb:\\xbdm.xex

  Attention : si tu as un `launch.ini` sur le disque dur **et** un sur la clé
  USB, c'est en général celui de l'USB qui est lu. Éditer l'autre ne fait rien.
  `xbdm.xex` et Dashlaunch ne sont pas fournis ici, ils ne nous appartiennent
  pas.

* FIFA 14, build `default.xex` timestamp `0x534C8977`. Un autre build sera
  refusé plutôt que patché de travers.

* **Python 3.10 ou plus**, sur n'importe quelle machine du même réseau que la
  console. Aucune dépendance à installer : tout est en bibliothèque standard.
  Un PC, un Mac, un Linux -- ou **un téléphone Android sous Termux**, ce qui
  veut dire qu'aucun ordinateur n'est nécessaire.

## Installation

    tar xzf fifa14-revival-client.tgz
    cd fifa14-revival-client
    cp fifa14revival.example.ini fifa14revival.ini

Puis dans `fifa14revival.ini` :

    [server]
    host = <adresse du serveur>
    core_port = 10041
    identity_port = 18080

    [console]
    address = <IP de ta Xbox>
    title = Hdd:\\Games\\FIFA 14

## Jouer

Console allumée, sur le tableau de bord :

    python3 tools/revival_client.py

Il lance le titre, applique les trois étages de correctifs, affiche `PRÊT`, et
**garde le troisième appliqué**. Laisse la fenêtre ouverte : le titre recharge
`helperFunctions` plusieurs fois, et un correctif posé une seule fois se fait
écraser. Entre dans Ultimate Team quand tu veux.

Sous Termux :

    pkg install python
    python3 tools/revival_client.py

## Si ça ne marche pas

* *« configuration illisible »* -- il manque `fifa14revival.ini`.
* *« les correctifs de lancement n'ont pas pris »* -- la console ne répond pas
  sur le port 730 : XBDM n'est pas chargé, ou ce n'est pas le bon `launch.ini`.
* *« connectez-vous à Xbox Live et aux serveurs EA »* dans le jeu -- la console
  n'atteint pas le serveur. Vérifie `host` dans le `.ini`.
* Le jeu démarre mais les cartes sont vides -- le serveur est joignable mais ne
  répond pas ; regarde de son côté.
"""


def closure(roots: tuple[str, ...]) -> set[str]:
    """Every module under tools/ these roots reach, directly or not."""
    available = {path.stem for path in TOOLS.glob("*.py")}
    found: set[str] = set()
    pending = list(roots)
    while pending:
        module = pending.pop()
        if module in found or module not in available:
            continue
        found.add(module)
        tree = ast.parse((TOOLS / f"{module}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    pending.append(node.module.split(".")[0])
    return found


def build(output: Path) -> list[str]:
    modules = sorted(closure(ROOTS))
    members: list[str] = []
    with tarfile.open(output, "w:gz") as archive:
        for module in modules:
            name = f"tools/{module}.py"
            archive.add(REPO / name, arcname=f"{TOP}/{name}")
            members.append(name)
        for name in EXTRA:
            source = REPO / name
            if source.exists():
                archive.add(source, arcname=f"{TOP}/{name}")
                members.append(name)
        readme = README.encode("utf-8")
        info = tarfile.TarInfo(f"{TOP}/README.md")
        info.size = len(readme)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(readme))
        members.append("README.md")
    return members


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=REPO / "fifa14-revival-client.tgz")
    args = parser.parse_args(argv)
    members = build(args.output)
    size = args.output.stat().st_size
    print(f"{len(members)} fichiers, {size/1024:.0f} Ko -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
