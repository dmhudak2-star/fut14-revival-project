#!/usr/bin/env python3
"""Passively journal the CardsDLL message handler that raises the FUT failure.

``CardsDLLzf.xex.dll`` routes FUT service results through a single handler at
``0x8911A998`` with the signature ``handler(this, message_id, parameter)``.  Its
switch on ``message_id`` sends ``0x65`` — CardHouse ``Login`` (component 2148,
command 101) — straight to the branch that emits the ``FUTT``/``DBUG``/``R4ER``
telemetry record ``0xA9`` and then shows the retail ``ServerFatalError`` /
``Unknown_FCC_Error`` popup at ``0x8909F448``.

Knowing which message ids actually reach this handler, in which order, and with
which parameter distinguishes "the login request was never issued" from "it was
issued and reported a failure".

This reversible entry hook records the newest sixteen invocations, executes the
displaced retail instruction, and resumes.  It never changes an argument, a
return value, a state flag, an event, or a frontend route.
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

SITE = 0x8911A998
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

# Padding between CardsDLL sections; verified free and writable at runtime.
STUB = 0x89044000
STUB_SIZE = 0x80
JOURNAL = 0x89044100
RECORD_COUNT = 16
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE

# Switch labels recovered statically from the handler at 0x8911A998.
MESSAGES = {
    0x15: "0x15",
    0x28: "0x28",
    0x2F: "0x2F",
    0x32: "0x32",
    0x65: "CardHouse Login (2148:101) -> ServerFatalError branch",
    0x6C: "0x6C",
    0x7A: "0x7A",
}

FATAL_MESSAGE = 0x65


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub() -> bytes:
    """Build an ABI-preserving ring recorder for the handler's arguments.

    ``mflr r12`` runs first because the retail prologue immediately consumes
    r12.  r10 and r11 are volatile here: the handler takes three arguments and
    writes r10/r11 before reading them.
    """
    words = [int.from_bytes(ORIGINAL, "big")]
    words.extend(pointer_load(11, JOURNAL))
    words.extend(
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
            stw(5, 11, RECORD_SIZE + 0x8),
            stw(12, 11, RECORD_SIZE + 0xC),
            0x7C0004AC,  # sync
        )
    )
    tail = STUB + len(words) * 4
    words.append(branch(tail, SITE + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("Cards message dispatch stub exceeds its cave")
    return result.ljust(STUB_SIZE, b"\0")


def site_patch() -> bytes:
    return insn(branch(SITE, STUB, False))


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


def hook_state(client: Xbdm) -> str:
    current = client.read(SITE, 4)
    if current == ORIGINAL:
        return "original"
    if current == site_patch() and client.read(STUB, STUB_SIZE) == build_stub():
        return "armed"
    return "unexpected"


def describe_message(identifier: int) -> str:
    return MESSAGES.get(identifier, "not handled by this switch")


def read_journal(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    print(f"handler invocations = {count}")
    if not count:
        print("The CardsDLL message handler was not reached.")
        return
    if count > RECORD_COUNT:
        print(f"WARNING: only the newest {RECORD_COUNT} records remain.")
    for sequence in range(max(0, count - RECORD_COUNT), count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        this = int.from_bytes(record[0:4], "big")
        identifier = int.from_bytes(record[4:8], "big")
        parameter = int.from_bytes(record[8:12], "big")
        caller = int.from_bytes(record[12:16], "big")
        marker = "  <<< fatal" if identifier == FATAL_MESSAGE else ""
        print(
            f"  {sequence:4d}  this=0x{this:08X} message=0x{identifier:X} "
            f"parameter=0x{parameter:08X} lr=0x{caller:08X}{marker}"
        )
        print(f"          {describe_message(identifier)}")


def apply_trace(client: Xbdm, state: str) -> None:
    desired = build_stub()
    if state == "armed":
        client.write(SITE, ORIGINAL)
        if client.read(SITE, 4) != ORIGINAL:
            raise RuntimeError("Could not unpublish the previous trace")
    existing = client.read(STUB, STUB_SIZE)
    if existing not in (bytes(STUB_SIZE), desired):
        raise RuntimeError("Cards message dispatch cave is not free")

    write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
    write_chunks(client, STUB, desired)
    if client.read(STUB, STUB_SIZE) != desired:
        raise RuntimeError("Cards message dispatch stub verification failed")
    client.write(SITE, site_patch())
    if hook_state(client) != "armed":
        raise RuntimeError("Cards message dispatch hook verification failed")


def arm_on_load(host: str, timeout: float) -> int:
    """Arm the trace on the CardsDLL modload notification.

    ``CardsDLLzf.xex.dll`` is mapped only when the title enters FUT, and the
    first login result arrives shortly afterwards.  Subscribing to the module
    notification and stopping the title on it is the only way to observe that
    first attempt instead of the already-failed state a second attempt sees.
    """
    notify = Connection(host)
    control: Connection | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFACardsTrace" user="CodexMac"'
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
            apply_trace(control, hook_state(control))
            print(
                "Verified: passive CardsDLL message dispatch trace armed "
                "at module load.",
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
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    if args.action == "arm-on-load":
        return arm_on_load(args.host, args.timeout)

    client = Xbdm(args.host)
    try:
        verify_module(client)
        state = hook_state(client)
        print(f"Cards message dispatch trace: {state}")

        if args.action == "status":
            return 0
        if args.action == "read":
            if state != "armed":
                raise RuntimeError("Trace is not armed")
            read_journal(client)
            return 0
        if state == "unexpected":
            raise RuntimeError("Refusing an unexpected CardsDLL handler entry")

        if args.action == "restore":
            if state == "armed":
                client.write(SITE, ORIGINAL)
                if client.read(SITE, 4) != ORIGINAL:
                    raise RuntimeError("CardsDLL handler restore failed")
            print("Verified: original CardsDLL message handler restored.")
            return 0

        apply_trace(client, state)
        print("Verified: passive CardsDLL message dispatch trace armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
