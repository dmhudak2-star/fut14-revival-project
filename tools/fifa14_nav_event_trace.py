#!/usr/bin/env python3
"""Passively record every navigation event the title sends.

Dispatching ``iceBreaker`` into the live flow was accepted and changed
nothing, which says only that the flow was not in ``futLogIn1`` -- an event a
state does not declare is discarded silently.  Nothing in the flow object
names its current state, so the cheap way to find out is to watch which events
the title itself emits and where they come from.

``SendNavEvent`` (``0x82805C10``) takes the navigation interface in r3 and a
NUL-terminated event name in r4.  This hook records both, plus the caller, then
performs the displaced instruction and resumes.  It publishes nothing, selects
no route, and changes no argument or result.

Event names are read back from the host, so the stub stays a few stores long
and never dereferences a pointer the title might be rewriting.
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
    insn,
    lwz,
    rlwinm,
    stw,
    write_chunks,
)


MODULE_NAME = "default.xex"
MODULE_BASE = 0x82000000

SITE = 0x82805C10
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

STUB = 0x83C89000
STUB_SIZE = 0x80
JOURNAL = 0x83C89100
RECORD_COUNT = 32
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub() -> bytes:
    """Record the interface, the event-name pointer and the caller.

    r10 and r11 are free at this function entry; r3 and r4 are the arguments
    and are left untouched, and r12 is deliberately avoided because that is
    what the displaced instruction writes.  It runs last so the function still
    saves the correct return address.
    """
    words: list[int] = list(pointer_load(11, JOURNAL))
    words.extend(
        (
            lwz(10, 11, 0),
            addi(10, 10, 1),
            stw(10, 11, 0),
            addi(10, 10, -1),
            rlwinm(10, 10, 0, 27, 31),  # index = count % RECORD_COUNT
            rlwinm(10, 10, 4, 23, 27),  # index * RECORD_SIZE
            add(11, 11, 10),
            stw(3, 11, RECORD_SIZE + 0x0),
            stw(4, 11, RECORD_SIZE + 0x4),
            0x7D4802A6,  # mflr r10, the caller
            stw(10, 11, RECORD_SIZE + 0x8),
            0x7C0004AC,  # sync
            int.from_bytes(ORIGINAL, "big"),  # displaced mflr r0
        )
    )
    tail = STUB + len(words) * 4
    words.append(branch(tail, SITE + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("Navigation event stub exceeds its cave")
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
    return "armed" if client.read(STUB, STUB_SIZE) == build_stub() else "stale"


def event_name(client: Xbdm, pointer: int) -> str:
    # Events the title sends point into its own image or heap; ones injected
    # through JRPC2 point at a temporary buffer well below the title's base,
    # so the readable range has to start under it or every injected event
    # reads back as unnamed.
    if not 0x10000000 <= pointer < 0xE0000000:
        return f"<0x{pointer:08X} is not a string pointer>"
    try:
        raw = client.read(pointer, 40).split(b"\0", 1)[0]
    except Exception:
        return "<unreadable>"
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return f"<{raw.hex()}>"


def read_journal(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    print(f"navigation events = {count}")
    if not count:
        print("The title sent no navigation event while the trace was armed.")
        return
    if count > RECORD_COUNT:
        print(f"WARNING: only the newest {RECORD_COUNT} records remain.")
    for sequence in range(max(0, count - RECORD_COUNT), count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        interface = int.from_bytes(record[0:4], "big")
        pointer = int.from_bytes(record[4:8], "big")
        caller = int.from_bytes(record[8:12], "big")
        print(
            f"  {sequence:4d}  {event_name(client, pointer)!r:28} "
            f"interface=0x{interface:08X} lr=0x{caller:08X}"
        )


def apply_trace(client: Xbdm) -> None:
    desired = build_stub()
    state = hook_state(client)
    if state == "unexpected":
        raise RuntimeError("Refusing an unexpected SendNavEvent entry")
    if client.read(SITE, 4) != ORIGINAL:
        client.write(SITE, ORIGINAL)
        if client.read(SITE, 4) != ORIGINAL:
            raise RuntimeError("Could not unpublish the previous trace")
        time.sleep(0.5)
    existing = client.read(STUB, STUB_SIZE)
    if state == "original" and existing not in (bytes(STUB_SIZE), desired):
        raise RuntimeError("Navigation event cave is not free")
    write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
    write_chunks(client, STUB, desired)
    if client.read(STUB, STUB_SIZE) != desired:
        raise RuntimeError("Navigation event stub verification failed")
    client.write(SITE, site_patch())
    if hook_state(client) != "armed":
        raise RuntimeError("Navigation event hook verification failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        state = hook_state(client)
        print(f"Navigation event trace: {state}")
        if args.action == "status":
            return 0
        if args.action == "read":
            if state != "armed":
                raise RuntimeError("Trace is not armed")
            read_journal(client)
            return 0
        if args.action == "restore":
            if state in ("armed", "stale"):
                client.write(SITE, ORIGINAL)
                if client.read(SITE, 4) != ORIGINAL:
                    raise RuntimeError("Navigation event restore failed")
            print("Verified: original SendNavEvent entry restored.")
            return 0
        apply_trace(client)
        print("Verified: passive navigation event trace armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
