#!/usr/bin/env python3
"""Move old data1 experiments out of FIFA 14's active game directory.

The operation is recoverable: files are renamed into a dedicated subdirectory,
never deleted or overwritten.  The active retail data1.big/data1.bh pair is
explicitly excluded.
"""

from __future__ import annotations

import argparse
import re
import socket


ROOT = "Hdd1:\\Games\\FIFA 14\\"
QUARANTINE = ROOT + "codex_archive_backups\\"
EXPERIMENTS = (
    "data1.chunkunc.failed.bh",
    "data1.chunkunc.failed.big",
    "data1.server-era-patched.big",
    "data1.lzx.v2.big",
    "data1.lzx.v3.big",
    "data1.lzx.v4.bh",
    "data1.lzx.v4.big",
    "data1.lzx.v5.bh",
    "data1.lzx.v5.big",
    "data1.agent.failed.bh",
    "data1.agent.failed.big",
)
RETAIL = {
    "data1.big": 336_771_570,
    "data1.bh": 348_996,
}


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

    def command(self, command: str) -> str:
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        return self.line()

    def multiline(self, command: str) -> list[str]:
        status = self.command(command)
        if not status.startswith("202-"):
            raise RuntimeError(f"{command}: {status}")
        result: list[str] = []
        while True:
            item = self.line()
            if item == ".":
                return result
            result.append(item)

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


def entries(client: Xbdm, path: str) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for line in client.multiline(f'dirlist name="{path}"'):
        name = parameter(line, "name")
        size = parameter(line, "sizelo")
        if name is not None and size is not None:
            result[name.casefold()] = (int(size, 0), line)
    return result


def fifa_is_loaded(client: Xbdm) -> bool:
    for line in client.multiline("modules"):
        lowered = line.casefold()
        if 'name="default.xex"' in lowered and (
            "pdata=0x82329200" in lowered
            or "osize=0x023ec400" in lowered
            or "timestamp=0x534c8977" in lowered
        ):
            return True
    return False


def ensure_quarantine(client: Xbdm) -> None:
    try:
        entries(client, QUARANTINE)
        return
    except RuntimeError:
        status = client.command(f'mkdir name="{QUARANTINE.rstrip(chr(92))}"')
        if not status.startswith("200-"):
            raise RuntimeError(f"Cannot create quarantine directory: {status}")
        entries(client, QUARANTINE)


def apply(client: Xbdm) -> None:
    if fifa_is_loaded(client):
        raise RuntimeError("FIFA 14 is still loaded; refusing to move archives")

    active = entries(client, ROOT)
    for name, expected_size in RETAIL.items():
        item = active.get(name.casefold())
        if item is None or item[0] != expected_size:
            raise RuntimeError(f"Retail {name} is missing or has an unexpected size")

    ensure_quarantine(client)
    quarantined = entries(client, QUARANTINE)
    selected = [name for name in EXPERIMENTS if name.casefold() in active]
    conflicts = [name for name in selected if name.casefold() in quarantined]
    if conflicts:
        raise RuntimeError("Quarantine destination already exists: " + ", ".join(conflicts))

    moved: list[str] = []
    for name in selected:
        status = client.command(
            f'rename name="{ROOT}{name}" newname="{QUARANTINE}{name}"'
        )
        if not status.startswith("200-"):
            raise RuntimeError(f"Failed after moving {len(moved)} file(s): {name}: {status}")
        moved.append(name)

    after = entries(client, ROOT)
    backup_after = entries(client, QUARANTINE)
    for name, expected_size in RETAIL.items():
        item = after.get(name.casefold())
        if item is None or item[0] != expected_size:
            raise RuntimeError(f"Retail {name} failed post-move verification")
    for name in moved:
        if name.casefold() in after or name.casefold() not in backup_after:
            raise RuntimeError(f"Move verification failed for {name}")

    print("Verified: active directory contains the retail data1.big/data1.bh pair.")
    print(f"Quarantined {len(moved)} experimental file(s) in:")
    print(QUARANTINE)
    for name in moved:
        print(f"  {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()
    client = Xbdm(args.host)
    try:
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
