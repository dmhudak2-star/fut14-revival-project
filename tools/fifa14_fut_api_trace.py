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

Each operation gets its own stub and journal, so the whole path can be armed in
one pass.  That matters because every FUT entry unloads CardsDLL: measuring one
operation per run would cost a full navigation cycle each time.

These reversible entry hooks record the newest sixteen invocations of each armed
operation, execute the displaced retail instruction, and resume.  They never
change an argument, a return value, a state flag, an event, or a route.
"""

from __future__ import annotations

import argparse
import re
import select
import time

from fifa14_early_redirector_patch import Connection
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

# Every operation handler opens with the same prologue, so one displaced
# instruction covers them all.  A probe that is not a function entry needs its
# own, and the only requirement is that the instruction be position
# independent -- these hooks move it into a cave and run it there.
DISPLACED = {
    # FirstTimeInitNotify's epilogue: addi r1, r1, 0x1b0, the stack teardown
    # immediately before its tail call to the register-restore helper.
    "FirstTimeInitReturn": bytes.fromhex("382101B0"),
    # The notification handler's tail reaches this only when an interface is
    # bound: lwz r11, 0(r28), guarded by a branch that skips it when r28 is
    # null.
    "NotifyInterfaceCall": bytes.fromhex("817C0000"),
}


def displaced_for(operation: str) -> bytes:
    return DISPLACED.get(operation, ORIGINAL)

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
    # Not an operation entry: the case the notification handler runs for
    # FirstTimeInit's own 0xDF.  The handler at 0x8910A798 switches on the
    # notification id and 0xDF lands at 0x8910A9EC, which calls this.  It is
    # the first thing known to run on that path and to stop before
    # GetIdentityData, so whether it is entered at all is the question.
    "FirstTimeInitNotify": 0x8909FC40,
    # Its epilogue.  Entry alone cannot tell "ran and returned" from "entered
    # and never came back", and those point in opposite directions.
    "FirstTimeInitReturn": 0x890A06D0,
    # Whether the handler notifies anything after publishing. This sits on the
    # tail every notification id shares, so its count is not specific to 0xDF
    # -- it says the tail notified something, not that it notified for this
    # operation. Read it alongside FirstTimeInitNotify, never alone.
    "NotifyInterfaceCall": 0x8910AAF8,
}

# The FUT service object LoginToFUT dispatches through.
SERVICE_GLOBAL = 0x892213A0

# Fixed slot order so a journal keeps its meaning across invocations.
ORDER = (
    "LoginToFUT",
    "FirstTimeInit",
    "GetIdentityData",
    "GetUserStatsData",
    "CardsDownloaded",
    "CreateClub",
    "CreateMatch",
    "ServiceQuickMatch",
    "ServiceCreateSession",
    "GetRandomOpponent",
    "FinalShutdown",
    "FirstTimeInitNotify",
    "FirstTimeInitReturn",
    "NotifyInterfaceCall",
)

# Every operation gets its own stub and journal so the whole path can be armed
# in one pass: each FUT entry unloads CardsDLL, so measuring one operation per
# run would cost a full navigation cycle each time.
SLOT_BASE = 0x89046000  # clear of the dispatch trace's 0x89044000/0x89045000
SLOT_STRIDE = 0x200
STUB_SIZE = 0x80
RECORD_COUNT = 16
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE


def slot(operation: str) -> tuple[int, int]:
    """Return the (stub, journal) pair reserved for ``operation``."""
    base = SLOT_BASE + ORDER.index(operation) * SLOT_STRIDE
    return base, base + STUB_SIZE


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub(site: int, stub: int, journal: int, operation: str) -> bytes:
    """Build an ABI-preserving ring recorder for one operation handler.

    ``mflr r12`` runs first because every handler's next instruction stores
    r12.  r10 and r11 are volatile: the handlers take at most three arguments
    and write r10/r11 before reading them.
    """
    words = [int.from_bytes(displaced_for(operation), "big")]
    words.extend(pointer_load(11, journal))
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
    tail = stub + len(words) * 4
    words.append(branch(tail, site + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("FUT API trace stub exceeds its cave")
    return result.ljust(STUB_SIZE, b"\0")


def site_patch(site: int, stub: int) -> bytes:
    return insn(branch(site, stub, False))


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


def hook_state(client: Xbdm, operation: str) -> str:
    site = OPERATIONS[operation]
    stub, journal = slot(operation)
    current = client.read(site, 4)
    if current == displaced_for(operation):
        return "original"
    if current == site_patch(site, stub) and client.read(
        stub, STUB_SIZE
    ) == build_stub(site, stub, journal, operation):
        return "armed"
    return "unexpected"


def read_journal(client: Xbdm, operation: str) -> int:
    _, journal = slot(operation)
    raw = client.read(journal, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    if not count:
        print(f"{operation:22s} never called")
        return 0
    print(f"{operation:22s} {count} call(s)")
    if count > RECORD_COUNT:
        print(f"  WARNING: only the newest {RECORD_COUNT} records remain.")
    for sequence in range(max(0, count - RECORD_COUNT), count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        print(
            f"  {sequence:4d}  r3=0x{int.from_bytes(record[0:4], 'big'):08X} "
            f"r4=0x{int.from_bytes(record[4:8], 'big'):08X} "
            f"r5=0x{int.from_bytes(record[8:12], 'big'):08X} "
            f"lr=0x{int.from_bytes(record[12:16], 'big'):08X}"
        )
    return count


def apply_trace(client: Xbdm, operation: str) -> None:
    site = OPERATIONS[operation]
    stub, journal = slot(operation)
    desired = build_stub(site, stub, journal, operation)
    if hook_state(client, operation) == "unexpected":
        raise RuntimeError(f"Refusing an unexpected {operation} entry")
    if client.read(site, 4) != displaced_for(operation):
        client.write(site, displaced_for(operation))
        if client.read(site, 4) != displaced_for(operation):
            raise RuntimeError("Could not unpublish the previous trace")
    existing = client.read(stub, STUB_SIZE)
    if existing not in (bytes(STUB_SIZE), desired):
        raise RuntimeError(f"{operation} trace cave is not free")

    write_chunks(client, journal, bytes(JOURNAL_SIZE))
    write_chunks(client, stub, desired)
    if client.read(stub, STUB_SIZE) != desired:
        raise RuntimeError(f"{operation} stub verification failed")
    client.write(site, site_patch(site, stub))
    if hook_state(client, operation) != "armed":
        raise RuntimeError(f"{operation} hook verification failed")


def apply_all(client: Xbdm, operations: list[str]) -> None:
    for operation in operations:
        apply_trace(client, operation)


def arm_on_load(host: str, operations: list[str], timeout: float) -> int:
    """Arm the trace on the CardsDLL modload notification.

    CardsDLL is mapped only when the title enters FUT, and the first
    ``LoginToFUT`` call follows within moments.  Stopping the title on the
    module notification is the only way to observe that first call rather than
    the state a second attempt sees.
    """
    notify = Connection(host)
    control: Connection | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFAFutApiTrace" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(host)
        print(f"Waiting for {MODULE_NAME}. Select FUT now.", flush=True)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            lowered = event.lower()
            if "modload" not in lowered or "cardsdllzf.xex.dll" not in lowered:
                continue
            if f"base=0x{MODULE_BASE:08x}" not in lowered:
                raise RuntimeError(f"Unexpected {MODULE_NAME} base: {event}")

            print(f"Module event: {event}", flush=True)
            control.command("stop")
            stopped = True
            apply_all(control, operations)
            print(
                "Verified: passive traces armed at module load: "
                + ", ".join(operations),
                flush=True,
            )
            control.command("go")
            stopped = False
            return 0
        raise TimeoutError(f"{MODULE_NAME} modload was not observed")
    finally:
        if stopped and control is not None:
            control.command("go")
        if control is not None:
            control.sock.close()
        notify.sock.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument(
        "action",
        choices=("status", "apply", "restore", "read", "arm-on-load"),
    )
    parser.add_argument(
        "--operation",
        action="append",
        choices=sorted(OPERATIONS),
        help="repeat to trace several operations at once (default: all)",
    )
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    operations = args.operation or list(ORDER)
    if args.action == "arm-on-load":
        return arm_on_load(args.host, operations, args.timeout)

    client = Xbdm(args.host)
    try:
        verify_module(client)

        if args.action == "status":
            for operation in operations:
                print(f"{operation:22s} {hook_state(client, operation)}")
            return 0
        if args.action == "read":
            total = 0
            for operation in operations:
                if hook_state(client, operation) != "armed":
                    print(f"{operation:22s} not armed")
                    continue
                total += read_journal(client, operation)
            service = int.from_bytes(client.read(SERVICE_GLOBAL, 4), "big")
            print(f"FUT service object = 0x{service:08X}")
            print(f"total recorded calls = {total}")
            return 0
        if args.action == "restore":
            for operation in operations:
                site = OPERATIONS[operation]
                if hook_state(client, operation) == "armed":
                    client.write(site, displaced_for(operation))
                    if client.read(site, 4) != displaced_for(operation):
                        raise RuntimeError(f"{operation} restore failed")
            print("Verified: original FUT operation handlers restored.")
            return 0

        apply_all(client, operations)
        print("Verified: passive traces armed: " + ", ".join(operations))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
