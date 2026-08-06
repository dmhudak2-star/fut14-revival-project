#!/usr/bin/env python3
"""Passively journal calls into CardsDLL's named FUT operations.

``CardsDLLzf.xex.dll`` publishes its FUT surface as a table of 12-byte records
built by the initializer at ``0x89107480``.  Each record holds a handler and the
operation's name, so the whole native API can be addressed by name rather than
by hunting for one site at a time:

```text
LoginToFUT   0x89105D18      CreateClub   0x891061E0
FirstTimeInit 0x89105D50     CreateMatch  0x89106218
GetIdentityData 0x89105EA0   MatchReady   0x89226270
```

``LoginToFUT`` itself is three instructions of glue: it loads the service object
from the global at ``0x892213A0`` and calls its second vtable slot.  Whether the
front-end ever reaches that glue is exactly what separates "the login was
refused" from "the login was never issued".

This reversible entry hook records the newest sixteen invocations of one chosen
operation, executes the displaced retail instruction, and resumes.  It never
changes an argument, a return value, a state flag, an event, or a route.
"""

from __future__ import annotations

import argparse
import re

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    branch,
    insn,
    lwz,
    rlwinm,
    stw,
    write_chunks,
)


MODULE_NAME = "CardsDLLzf.xex.dll"
MODULE_BASE = 0x89000000
MODULE_SIZE = 0x2B0000

ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

# Recovered from the table initializer at 0x89107480.  Only the operations on
# the path to a first match are listed; the table holds 75 in total.
OPERATIONS = {
    "LoginToFUT": 0x89105D18,
    "FirstTimeInit": 0x89105D50,
    "GetIdentityData": 0x89105EA0,
    "GetUserStatsData": 0x89105F48,
    "CardsDownloaded": 0x89105E68,
    "CreateClub": 0x891061E0,
    "CreateMatch": 0x89106218,
    "ServiceQuickMatch": 0x89106130,
    "ServiceCreateSession": 0x89106188,
    "GetRandomOpponent": 0x891063C8,
    "FinalShutdown": 0x89105E30,
}

# The FUT service object LoginToFUT dispatches through.
SERVICE_GLOBAL = 0x892213A0

# Cave kept clear of the dispatch trace's 0x89044000/0x89045000 pair.
STUB = 0x89046000
STUB_SIZE = 0x80
JOURNAL = 0x89046100
RECORD_COUNT = 16
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub(site: int) -> bytes:
    """Build an ABI-preserving ring recorder for one operation handler.

    ``mflr r12`` runs first because every handler's next instruction stores
    r12.  r10 and r11 are volatile: the handlers take at most three arguments
    and write r10/r11 before reading them.
    """
    words = [int.from_bytes(ORIGINAL, "big")]
    words.extend(pointer_load(11, JOURNAL))
    words.extend(
        (
            lwz(10, 11, 0),
            addi(10, 10, 1),
            stw(10, 11, 0),
            addi(10, 10, -1),
            rlwinm(10, 10, 0, 28, 31),
            rlwinm(10, 10, 4, 24, 27),
            add(11, 11, 10),
            stw(3, 11, RECORD_SIZE + 0x0),
            stw(4, 11, RECORD_SIZE + 0x4),
            stw(5, 11, RECORD_SIZE + 0x8),
            stw(12, 11, RECORD_SIZE + 0xC),
            0x7C0004AC,  # sync
        )
    )
    tail = STUB + len(words) * 4
    words.append(branch(tail, site + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("FUT API trace stub exceeds its cave")
    return result.ljust(STUB_SIZE, b"\0")


def site_patch(site: int) -> bytes:
    return insn(branch(site, STUB, False))


def verify_module(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="CardsDLLzf\.xex\.dll"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None or f"base=0x{MODULE_BASE:08x}" not in module.lower():
        raise RuntimeError(f"Unexpected or missing {MODULE_NAME}: {module}")


def hook_state(client: Xbdm, site: int) -> str:
    current = client.read(site, 4)
    if current == ORIGINAL:
        return "original"
    if current == site_patch(site) and client.read(STUB, STUB_SIZE) == build_stub(site):
        return "armed"
    return "unexpected"


def read_journal(client: Xbdm, operation: str) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    print(f"{operation} invocations = {count}")
    if not count:
        print(f"The front-end never called {operation}.")
        return
    if count > RECORD_COUNT:
        print(f"WARNING: only the newest {RECORD_COUNT} records remain.")
    for sequence in range(max(0, count - RECORD_COUNT), count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        print(
            f"  {sequence:4d}  r3=0x{int.from_bytes(record[0:4], 'big'):08X} "
            f"r4=0x{int.from_bytes(record[4:8], 'big'):08X} "
            f"r5=0x{int.from_bytes(record[8:12], 'big'):08X} "
            f"lr=0x{int.from_bytes(record[12:16], 'big'):08X}"
        )
    service = int.from_bytes(client.read(SERVICE_GLOBAL, 4), "big")
    print(f"FUT service object = 0x{service:08X}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    parser.add_argument(
        "--operation", choices=sorted(OPERATIONS), default="LoginToFUT"
    )
    args = parser.parse_args()

    site = OPERATIONS[args.operation]
    client = Xbdm(args.host)
    try:
        verify_module(client)
        state = hook_state(client, site)
        print(f"{args.operation} trace: {state}")

        if args.action == "status":
            return 0
        if args.action == "read":
            if state != "armed":
                raise RuntimeError("Trace is not armed")
            read_journal(client, args.operation)
            return 0
        if state == "unexpected":
            raise RuntimeError(f"Refusing an unexpected {args.operation} entry")

        if args.action == "restore":
            if state == "armed":
                client.write(site, ORIGINAL)
                if client.read(site, 4) != ORIGINAL:
                    raise RuntimeError(f"{args.operation} restore failed")
            print(f"Verified: original {args.operation} handler restored.")
            return 0

        desired = build_stub(site)
        if state == "armed":
            client.write(site, ORIGINAL)
            if client.read(site, 4) != ORIGINAL:
                raise RuntimeError("Could not unpublish the previous trace")
        existing = client.read(STUB, STUB_SIZE)
        if existing not in (bytes(STUB_SIZE), desired):
            raise RuntimeError("FUT API trace cave is not free")

        write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
        write_chunks(client, STUB, desired)
        if client.read(STUB, STUB_SIZE) != desired:
            raise RuntimeError("FUT API trace stub verification failed")
        client.write(site, site_patch(site))
        if hook_state(client, site) != "armed":
            raise RuntimeError("FUT API trace hook verification failed")
        print(f"Verified: passive {args.operation} trace armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
