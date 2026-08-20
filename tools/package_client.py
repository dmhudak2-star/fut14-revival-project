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

# A ready-to-run config, written into the package when --server is given, so a
# player edits one line (their console) instead of three. The example file
# ships beside it either way: it is the one with all the comments in it.
READY_INI = """; Ready to run. One line to change: console.address.
;
; The server is already filled in -- it is a hosted revival server, and there
; is nothing to install on that side. To host your own instead, replace `host`
; with its address (see deploy/DEPLOY.md in the repository).

[server]
host = {server}
core_port = {core_port}
identity_port = {identity_port}

[console]
; YOUR Xbox's IP, on your own network. This is the only thing to fill in.
address = 192.168.1.25
title = Hdd:\\Games\\FIFA 14
"""

TOP = "fifa14-revival-client"

README = """# FIFA 14 Ultimate Team -- the console client

This launches FIFA 14 on a modded Xbox 360 and applies the patches. It holds
**no game files** and no server: the server is elsewhere, and its address lives
in `fifa14revival.ini`.

## What you need

* an Xbox 360 **RGH or JTAG**, with **Dashlaunch** and **XBDM loaded as a
  plugin**. In practice, one line in the `launch.ini` Dashlaunch actually
  reads:

      plugin4 = Usb:\\xbdm.xex

  Careful: if you have a `launch.ini` on the hard drive **and** one on the USB
  stick, the USB one usually wins. Editing the other does nothing at all.
  `xbdm.xex` and Dashlaunch are not included here -- they are not ours to hand
  out.

* FIFA 14, `default.xex` timestamp `0x534C8977`. Any other build is refused
  rather than patched wrongly.

* **Python 3.10 or newer**, on any machine on the same network as the console.
  Nothing to install: it is all standard library. A PC, a Mac, a Linux box --
  or **an Android phone running Termux**, which means no computer is needed at
  all.

## Setup

    tar xzf fifa14-revival-client.tgz
    cd fifa14-revival-client

If `fifa14revival.ini` is already there, the server is filled in and **one
line** is left -- `address`, under `[console]`, your Xbox's IP:

    [console]
    address = <your Xbox's IP>
    title = Hdd:\\Games\\FIFA 14

Otherwise start from `fifa14revival.example.ini`, which is commented in detail.

## Playing

Console on, sitting at the dashboard:

    python3 tools/revival_client.py

It launches the title, applies the three stages of patches, prints `PRÊT`, and
then **keeps the third one applied**. Leave the window open: the title reloads
`helperFunctions` more than once, and a patch applied a single time gets
overwritten. Go into Ultimate Team whenever you like.

On Termux:

    pkg install python
    python3 tools/revival_client.py

## If it does not work

* *"configuration illisible"* -- `fifa14revival.ini` is missing.
* *"les correctifs de lancement n'ont pas pris"* -- the console is not
  answering on port 730: XBDM is not loaded, or it is the wrong `launch.ini`.
* *"connect to Xbox Live and the EA servers"* in the game -- the console is not
  reaching the server. Check `host` in the `.ini`.
* The game starts but the cards are empty -- the server is reachable but not
  answering; look at its side.
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


def build(output: Path, server: str | None = None,
          core_port: int = 10041, identity_port: int = 18080) -> list[str]:
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
        if server:
            ready = READY_INI.format(
                server=server, core_port=core_port, identity_port=identity_port
            ).encode("utf-8")
            info = tarfile.TarInfo(f"{TOP}/fifa14revival.ini")
            info.size = len(ready)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(ready))
            members.append("fifa14revival.ini")
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
    parser.add_argument("--server", default=None,
                        help="write a ready fifa14revival.ini naming this server")
    parser.add_argument("--core-port", type=int, default=10041)
    parser.add_argument("--identity-port", type=int, default=18080)
    args = parser.parse_args(argv)
    members = build(args.output, args.server, args.core_port, args.identity_port)
    size = args.output.stat().st_size
    print(f"{len(members)} fichiers, {size/1024:.0f} Ko -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
