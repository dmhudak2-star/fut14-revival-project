#!/usr/bin/env python3
"""Recoverably restore FIFA 14's clean data1.big on the supported console.

No file is deleted or overwritten.  The active experimental archive is moved
to a distinct backup name before the known clean backup becomes data1.big.
"""

from __future__ import annotations

import argparse
import re
import socket


ROOT = "Hdd1:\\Games\\FIFA 14\\"
ACTIVE = "data1.big"
CLEAN = "data1.clean.original.big"
EXPERIMENT = "data1.server-era-patched.big"
HEADER = "data1.bh"
EXPECTED_BIG_SIZE = 336_771_570
EXPECTED_BH_SIZE = 348_996


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=8)
        self.sock.settimeout(20)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith("201-"):
            raise RuntimeError("Unexpected XBDM greeting")

    def line(self) -> str:
        raw = self.reader.readline()
        if not raw:
            raise EOFError("XBDM closed the connection")
        return raw.decode("ascii", "replace").rstrip("\r\n")

    def multiline(self, command: str) -> list[str]:
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        status = self.line()
        if not status.startswith("202-"):
            raise RuntimeError(f"{command}: {status}")
        result: list[str] = []
        while True:
            item = self.line()
            if item == ".":
                return result
            result.append(item)

    def rename(self, source: str, destination: str) -> None:
        command = (
            f'rename name="{ROOT}{source}" '
            f'newname="{ROOT}{destination}"'
        )
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        status = self.line()
        if not status.startswith("200-"):
            raise RuntimeError(f"{command}: {status}")

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def parameter(line: str, name: str) -> str | None:
    match = re.search(
        rf'(?:^|\s){re.escape(name)}=(?:"([^"]*)"|([^\s]+))',
        line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def entries(client: Xbdm) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for line in client.multiline(f'dirlist name="{ROOT}"'):
        name = parameter(line, "name")
        size = parameter(line, "sizelo")
        if name is None or size is None:
            continue
        result[name.casefold()] = (int(size, 0), line)
    return result


def require_size(items: dict[str, tuple[int, str]], name: str, size: int) -> None:
    item = items.get(name.casefold())
    if item is None:
        raise RuntimeError(f"Missing {ROOT}{name}")
    if item[0] != size:
        raise RuntimeError(
            f"Unexpected size for {name}: {item[0]}, expected {size}"
        )


def fifa_is_loaded(client: Xbdm) -> bool:
    for line in client.multiline("modules"):
        if 'name="default.xex"' not in line.casefold():
            continue
        lowered = line.casefold()
        if (
            "timestamp=0x534c8977" in lowered
            or "pdata=0x82329200" in lowered
            or "osize=0x023ec400" in lowered
        ):
            return True
    return False


def show(items: dict[str, tuple[int, str]]) -> None:
    for name in (ACTIVE, CLEAN, EXPERIMENT, HEADER):
        item = items.get(name.casefold())
        detail = "absent" if item is None else f"present ({item[0]} bytes)"
        print(f"{name:<32} {detail}")


def apply(client: Xbdm) -> None:
    if fifa_is_loaded(client):
        raise RuntimeError("FIFA 14 is still loaded; refusing archive rename")

    before = entries(client)
    require_size(before, ACTIVE, EXPECTED_BIG_SIZE)
    require_size(before, CLEAN, EXPECTED_BIG_SIZE)
    require_size(before, HEADER, EXPECTED_BH_SIZE)
    if EXPERIMENT.casefold() in before:
        raise RuntimeError(f"Destination already exists: {EXPERIMENT}")

    active_line = before[ACTIVE.casefold()][1].casefold()
    clean_line = before[CLEAN.casefold()][1].casefold()
    if "createhi=0x01dd1efe" not in active_line:
        raise RuntimeError("Active data1.big is not the known LZX experiment")
    if "createhi=0x01cea63e" not in clean_line:
        raise RuntimeError("Clean backup does not have the retail timestamp")

    moved = False
    try:
        client.rename(ACTIVE, EXPERIMENT)
        moved = True
        middle = entries(client)
        if ACTIVE.casefold() in middle or EXPERIMENT.casefold() not in middle:
            raise RuntimeError("Experimental archive backup did not verify")

        client.rename(CLEAN, ACTIVE)
        after = entries(client)
        require_size(after, ACTIVE, EXPECTED_BIG_SIZE)
        require_size(after, EXPERIMENT, EXPECTED_BIG_SIZE)
        require_size(after, HEADER, EXPECTED_BH_SIZE)
        if CLEAN.casefold() in after:
            raise RuntimeError("Clean backup rename did not complete")
        if "createhi=0x01cea63e" not in after[ACTIVE.casefold()][1].casefold():
            raise RuntimeError("Active data1.big does not have the retail timestamp")
    except Exception:
        if moved:
            recovery = entries(client)
            if (
                ACTIVE.casefold() not in recovery
                and EXPERIMENT.casefold() in recovery
                and CLEAN.casefold() in recovery
            ):
                client.rename(EXPERIMENT, ACTIVE)
        raise

    print("Verified: clean retail data1.big is active.")
    print(f"Experimental archive preserved as {EXPERIMENT}.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        if args.action == "status":
            show(entries(client))
        else:
            apply(client)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
