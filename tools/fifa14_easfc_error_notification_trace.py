#!/usr/bin/env python3
"""Passively journal which EASFC failure text CardsDLL selects, and from where.

``powdllzf.xex.dll`` renders the retail "connexion à FIFA 14 Ultimate Team"
failure from a three-entry localization table built at ``0x8978CBC0`` and
indexed by the second argument of the routine at ``0x8978C920``:

```text
0 -> TXT_EASFC_SERVER_ERROR
1 -> TXT_EASFC_PLEASE_SIGN_IN
2 -> TXT_EASFC_RECONNECTING
```

Three call sites reach it.  ``0x89790CB8`` passes 0 from the notification
handler at ``0x89790C58`` when its notification id is 2 or 15 and its status is
1; ``0x89790D14`` passes 1; ``0x8978CE1C`` computes 0, 1 or 2 from live state.
Hooking the shared routine therefore covers every path, and the recorded link
register names which one ran.

This reversible entry hook records the routine's arguments and its caller for
the newest sixteen invocations, executes the displaced retail instruction, and
resumes.  It never changes an argument, a return value, a state flag, an event,
or a frontend route.
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


MODULE_NAME = "powdllzf.xex.dll"
MODULE_BASE = 0x89700000

SITE = 0x8978C920
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

# Free space after the credentials patch at 0x897BF200.
STUB = 0x897BF400
STUB_SIZE = 0x80
JOURNAL = 0x897BF500
RECORD_COUNT = 16
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE

TEXT_BY_KIND = {
    0: "TXT_EASFC_SERVER_ERROR",
    1: "TXT_EASFC_PLEASE_SIGN_IN",
    2: "TXT_EASFC_RECONNECTING",
}

CALLERS = {
    0x8978CE20: "0x8978CE1C, kind computed from live EASFC state",
    0x89790CBC: "0x89790CB8, notification handler 0x89790C58 (id 2 or 15)",
    0x89790D18: "0x89790D14, sign-in path",
}


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub() -> bytes:
    """Build an ABI-preserving ring recorder for the handler's arguments.

    ``mflr r12`` runs first because the following retail instruction stores
    r12.  r10 and r11 are volatile here: the handler takes three arguments and
    its prologue writes r10/r11 before reading them.
    """
    words = [int.from_bytes(ORIGINAL, "big")]
    words.extend(pointer_load(11, JOURNAL))
    words.extend(
        (
            lwz(10, 11, 0),
            addi(10, 10, 1),
            stw(10, 11, 0),
            addi(10, 10, -1),
            rlwinm(10, 10, 0, 28, 31),  # index = count % 16
            rlwinm(10, 10, 4, 24, 27),  # index * RECORD_SIZE
            add(11, 11, 10),
            stw(3, 11, RECORD_SIZE + 0x0),
            stw(4, 11, RECORD_SIZE + 0x4),
            stw(5, 11, RECORD_SIZE + 0x8),
            stw(12, 11, RECORD_SIZE + 0xC),
            0x7C0004AC,  # sync
        )
    )
    tail = STUB + len(words) * 4
    words.append(branch(tail, SITE + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("EASFC error notification stub exceeds its cave")
    return result.ljust(STUB_SIZE, b"\0")


def site_patch() -> bytes:
    return insn(branch(SITE, STUB, False))


def verify_module(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="powdllzf\.xex\.dll"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None or f"base=0x{MODULE_BASE:08x}" not in module.lower():
        raise RuntimeError(f"Unexpected or missing {MODULE_NAME}: {module}")


def hook_state(client: Xbdm) -> str:
    current = client.read(SITE, 4)
    if current == ORIGINAL:
        return "original"
    if current == site_patch() and client.read(STUB, STUB_SIZE) == build_stub():
        return "armed"
    return "unexpected"


def describe_kind(kind: int) -> str:
    return TEXT_BY_KIND.get(kind, f"unmapped kind {kind}")


def describe_caller(link_register: int) -> str:
    return CALLERS.get(link_register, "unknown call site")


def read_journal(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    print(f"EASFC text selections = {count}")
    if not count:
        print("The EASFC failure-text routine was not reached.")
        return
    if count > RECORD_COUNT:
        print(f"WARNING: only the newest {RECORD_COUNT} records remain.")
    first = max(0, count - RECORD_COUNT)
    for sequence in range(first, count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        this = int.from_bytes(record[0:4], "big")
        kind = int.from_bytes(record[4:8], "big")
        extra = int.from_bytes(record[8:12], "big")
        caller = int.from_bytes(record[12:16], "big")
        print(
            f"  {sequence:4d}  this=0x{this:08X} kind={kind} "
            f"r5=0x{extra:08X} lr=0x{caller:08X}"
        )
        print(f"          {describe_kind(kind)} via {describe_caller(caller)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        state = hook_state(client)
        print(f"EASFC error notification trace: {state}")

        if args.action == "status":
            return 0
        if args.action == "read":
            if state != "armed":
                raise RuntimeError("Trace is not armed")
            read_journal(client)
            return 0
        if state == "unexpected":
            raise RuntimeError("Refusing an unexpected EASFC handler entry")

        if args.action == "restore":
            if state == "armed":
                client.write(SITE, ORIGINAL)
                if client.read(SITE, 4) != ORIGINAL:
                    raise RuntimeError("EASFC handler restore failed")
            print("Verified: original EASFC notification handler restored.")
            return 0

        desired = build_stub()
        if state == "armed":
            client.write(SITE, ORIGINAL)
            if client.read(SITE, 4) != ORIGINAL:
                raise RuntimeError("Could not unpublish the previous trace")
        existing = client.read(STUB, STUB_SIZE)
        if existing not in (bytes(STUB_SIZE), desired):
            raise RuntimeError("EASFC notification trace cave is not free")

        write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
        write_chunks(client, STUB, desired)
        if client.read(STUB, STUB_SIZE) != desired:
            raise RuntimeError("EASFC notification stub verification failed")
        client.write(SITE, site_patch())
        if hook_state(client) != "armed":
            raise RuntimeError("EASFC notification hook verification failed")
        print("Verified: passive EASFC error notification trace armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
