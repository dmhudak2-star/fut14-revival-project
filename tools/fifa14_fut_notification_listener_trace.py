#!/usr/bin/env python3
"""Passively record whether FirstTimeInit's notification has any listener.

``FirstTimeInit`` submits operation ``0xDF`` and the FUT bootstrap then waits
forever.  Following that submission through ``default.xex`` reaches the
notification bus at ``0x8278F228``:

```text
0x8278F228  cmpwi cr6, r4, 0xA8   ; <- hooked here
...                               ; 0xA8 and 0x46 have dedicated handling
0x8278F274  lwz r11, 0x10(r3)     ; the registered listener slot
0x8278F278  cmpwi cr6, r11, -1
0x8278F27C  beqlr cr6             ; -1 means nobody is listening: return
0x8278F284  lwzx r10, r11, r3     ; otherwise dispatch through the vtable
```

So an operation whose listener slot holds ``-1`` is accepted and silently
dropped, which matches a submission that never completes.

This bus is shared by the whole engine, though: an unfiltered hook here fires
hundreds of thousands of times per session and any ring buffer ends up holding
nothing but the per-frame traffic.  So the recorder keeps only ``TARGET``, the
operation ``FirstTimeInit`` submits.  Filtering needs a comparison, which would
clobber the condition register the displaced instruction is there to set, so
the whole CR is saved on entry and restored before the displaced instruction
runs.

This hook records the bus object, the operation id, the listener slot and the
caller, executes the displaced comparison and resumes.  It changes no argument,
return value, state flag, event or route.
"""

from __future__ import annotations

import argparse
import re
import time

from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    branch,
    cmplwi,
    conditional_branch,
    insn,
    lwz,
    rlwinm,
    stw,
    write_chunks,
)


def mfcr(rt: int) -> int:
    return 0x7C000026 | (rt << 21)


def mtcrf(mask: int, rs: int) -> int:
    return 0x7C000120 | (rs << 21) | (mask << 12)


def mflr(rt: int) -> int:
    return 0x7C0802A6 | (rt << 21)


# "branch if the condition bit is false", and cr0's EQ bit.
BRANCH_IF_FALSE = 4
CR0_EQ = 2


MODULE_NAME = "default.xex"
MODULE_BASE = 0x82000000

SITE = 0x8278F228
ORIGINAL = bytes.fromhex("2F0400A8")  # cmpwi cr6, r4, 0xA8

# The listener slot the bus consults, and the value that means "nobody".
LISTENER_OFFSET = 0x10
NO_LISTENER = 0xFFFFFFFF

# The operation FirstTimeInit submits, and the only one worth recording here.
TARGET = 0xDF

STUB = 0x83C88000
STUB_SIZE = 0x80
JOURNAL = 0x83C88100
RECORD_COUNT = 16
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE

OPERATION_NAMES = {0xDF: "FirstTimeInit's request", 0xA8: "0xA8", 0x46: "0x46"}


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub() -> bytes:
    """Record the bus object, operation, listener slot and caller for TARGET.

    r3 and r4 are the bus arguments, leaving r10 to r12 as scratch here.  r12
    holds the saved condition register across the whole stub so the filtering
    comparison cannot be observed: the CR is put back exactly as it arrived,
    and only then does the displaced ``cmpwi`` set cr6 for the retail code.
    """
    record: list[int] = list(pointer_load(11, JOURNAL))
    record.extend(
        (
            lwz(10, 11, 0),
            addi(10, 10, 1),
            stw(10, 11, 0),
            addi(10, 10, -1),
            rlwinm(10, 10, 0, 28, 31),  # index = count % RECORD_COUNT
            rlwinm(10, 10, 4, 24, 27),  # index * RECORD_SIZE
            add(11, 11, 10),
            stw(3, 11, RECORD_SIZE + 0x0),
            stw(4, 11, RECORD_SIZE + 0x4),
            lwz(10, 3, LISTENER_OFFSET),
            stw(10, 11, RECORD_SIZE + 0x8),
            mflr(10),
            stw(10, 11, RECORD_SIZE + 0xC),
            0x7C0004AC,  # sync
        )
    )

    words: list[int] = [mfcr(12), cmplwi(4, TARGET)]
    skip_site = STUB + len(words) * 4
    resume = skip_site + 4 + len(record) * 4
    words.append(conditional_branch(skip_site, resume, BRANCH_IF_FALSE, CR0_EQ))
    words.extend(record)
    words.extend(
        (
            mtcrf(0xFF, 12),  # CR restored exactly as it arrived
            int.from_bytes(ORIGINAL, "big"),  # displaced cmpwi, sets cr6 last
        )
    )
    tail = STUB + len(words) * 4
    words.append(branch(tail, SITE + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("Notification listener stub exceeds its cave")
    return result.ljust(STUB_SIZE, b"\0")


def site_patch() -> bytes:
    return insn(branch(SITE, STUB, False))


def verify_module(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="default\.xex"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None or f"base=0x{MODULE_BASE:08x}" not in module.lower():
        raise RuntimeError(f"Unexpected or missing {MODULE_NAME}: {module}")


def hook_state(client: Xbdm) -> str:
    current = client.read(SITE, 4)
    if current == ORIGINAL:
        return "original"
    if current != site_patch():
        return "unexpected"
    # The site branches into our own cave, so whatever sits there is a stub we
    # published.  An older revision of it is safe to replace; only a site we
    # do not recognise at all is refused.
    return "armed" if client.read(STUB, STUB_SIZE) == build_stub() else "stale"


def describe(operation: int, listener: int) -> str:
    name = OPERATION_NAMES.get(operation, f"operation 0x{operation:X}")
    if listener == NO_LISTENER:
        return f"{name}: no listener, the bus returns without dispatching"
    return f"{name}: listener slot {listener}"


def read_journal(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    print(f"notifications carrying operation 0x{TARGET:X} = {count}")
    if not count:
        print(
            f"The bus was never reached with operation 0x{TARGET:X}: whatever "
            "stalls the bootstrap happens before this dispatch."
        )
        return
    if count > RECORD_COUNT:
        print(f"WARNING: only the newest {RECORD_COUNT} records remain.")
    for sequence in range(max(0, count - RECORD_COUNT), count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        bus = int.from_bytes(record[0:4], "big")
        operation = int.from_bytes(record[4:8], "big")
        listener = int.from_bytes(record[8:12], "big")
        caller = int.from_bytes(record[12:16], "big")
        print(
            f"  {sequence:4d}  bus=0x{bus:08X} operation=0x{operation:X} "
            f"listener=0x{listener:08X} lr=0x{caller:08X}"
        )
        print(f"          {describe(operation, listener)}")


def apply_trace(client: Xbdm) -> None:
    desired = build_stub()
    state = hook_state(client)
    if state == "unexpected":
        raise RuntimeError("Refusing an unexpected notification bus entry")
    if client.read(SITE, 4) != ORIGINAL:
        # Unpublish before touching the cave: the bus is hot, and rewriting a
        # stub that threads are still branching into would run a mixed image.
        client.write(SITE, ORIGINAL)
        if client.read(SITE, 4) != ORIGINAL:
            raise RuntimeError("Could not unpublish the previous trace")
        time.sleep(0.5)
    existing = client.read(STUB, STUB_SIZE)
    if state == "original" and existing not in (bytes(STUB_SIZE), desired):
        raise RuntimeError("Notification listener cave is not free")
    write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
    write_chunks(client, STUB, desired)
    if client.read(STUB, STUB_SIZE) != desired:
        raise RuntimeError("Notification listener stub verification failed")
    client.write(SITE, site_patch())
    if hook_state(client) != "armed":
        raise RuntimeError("Notification listener hook verification failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        state = hook_state(client)
        print(f"Notification listener trace: {state}")
        if args.action == "status":
            return 0
        if args.action == "read":
            if state != "armed":
                raise RuntimeError("Trace is not armed")
            read_journal(client)
            return 0
        if args.action == "restore":
            if state == "armed":
                client.write(SITE, ORIGINAL)
                if client.read(SITE, 4) != ORIGINAL:
                    raise RuntimeError("Notification listener restore failed")
            print("Verified: original notification bus entry restored.")
            return 0
        apply_trace(client)
        print("Verified: passive notification listener trace armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
