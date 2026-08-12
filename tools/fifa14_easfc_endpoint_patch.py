#!/usr/bin/env python3
"""Point the EAS FC session and catalogue at the local server.

The header banner reads "EAS FC non connecté", and after a while the main menu
says outright: *Vous avez perdu la connexion avec les serveurs EA. Vous ne
pourrez accéder aux fonctionnalités...*

That is a **second** Blaze connection, from `powdllzf.xex.dll`, to endpoints of
its own:

    0x89706250   pal.gt.easfc.ea.com:8094       the session
    0x897061B0   content.lt.easfc.ea.com:8080   the catalogue

Neither hostname is among the four the launch patch rewrites in `default.xex`,
so the client resolves them for real, reaches nothing, and reports the banner.
Nothing appears in the journal because the traffic never comes near us.

`docs/EASFC_NOT_CONNECTED.md` tried serving `ONLINE/POW_CUSTOMURL` and its four
siblings through `OSDK_CORE` instead, and left it unverified. It is verified
now, and it did not work: both strings were read back from a running title on
12 August still holding their retail values. The module does not take the
configuration, or does not take it in time.

So the hostnames are rewritten in the image, which is what the launch patch
already does for the other four. Both replacements are shorter than what they
replace, so nothing moves and the following string is untouched.

This has to run **before** the POW module connects, which is a few seconds into
the title's life -- hence the polling: `powdllzf` is not mapped at the instant
the title resumes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fifa14_plain_send_hook import Xbdm  # noqa: E402

SESSION = (0x89706250, b"pal.gt.easfc.ea.com:8094")
CATALOGUE = (0x897061B0, b"content.lt.easfc.ea.com:8080")


def patch(host: str, local: str, core_port: int, identity_port: int) -> bool:
    replacements = (
        (SESSION, f"{local}:{core_port}".encode()),
        (CATALOGUE, f"http://{local}:{identity_port}".encode()),
    )
    client = Xbdm(host)
    try:
        written = 0
        for (address, original), replacement in replacements:
            current = client.read(address, len(original) + 1)
            if current[: len(replacement)] == replacement:
                written += 1
                continue
            if current[: len(original)] != original:
                print(
                    f"0x{address:08X}: unexpected content "
                    f"{current[: len(original)]!r}; nothing written"
                )
                return False
            if len(replacement) > len(original):
                print(f"0x{address:08X}: replacement is longer; nothing written")
                return False
            client.write(
                address,
                replacement + b"\x00" * (len(original) + 1 - len(replacement)),
            )
            if client.read(address, len(replacement)) != replacement:
                print(f"0x{address:08X}: verification failed")
                return False
            written += 1
        return written == len(replacements)
    finally:
        try:
            client.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--core-port", type=int, default=10041)
    parser.add_argument("--identity-port", type=int, default=18080)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            if patch(args.host, args.local_ip, args.core_port, args.identity_port):
                print(
                    "Verified: EAS FC session and catalogue point at "
                    f"{args.local_ip}"
                )
                return 0
        except Exception:
            pass          # powdllzf is not mapped yet
        time.sleep(2)
    print("Error: powdllzf was not mapped before timeout")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
