#!/usr/bin/env python3
"""Passively resolve the request FirstTimeInit submits, and where it goes.

Tracing CardsDLL's FUT API showed the front-end calls exactly two operations
before the retail error dialog: ``LoginToFUT``, which is purely local, and
``FirstTimeInit``, which submits one request and never sees a reply.

``FirstTimeInit`` (``0x8908D3D0``) resolves its target at runtime:

```text
0x8908D3EC  bl 0x8908CA10        ; interface 0x0EBDBBE4 from the manager
0x8908D3F0  li r4, 0xDF          ; operation id 223
0x8908D3FC  lwz r11, 0(r31)      ; vtable
0x8908D400  lwz r11, 0x4C(r11)   ; submit method
0x8908D404  mtctr r11            ; <- hooked here
0x8908D408  bctrl
```

So neither the request object nor the submit method has a static address.  This
hook sits on the ``mtctr`` and records both, plus the operation id still live in
r4, then performs the displaced ``mtctr`` and resumes.  It never changes an
argument, a return value, a state flag, an event, or a route.

The recorded method address is what a following pass can hook to find where the
submission stops before it reaches any transport.
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

SITE = 0x8908D404
ORIGINAL = bytes.fromhex("7D6903A6")  # mtctr r11

# The interface FirstTimeInit asks the manager for, and the operation it then
# submits.  Both are recovered statically and are only used to describe output.
INTERFACE_ID = 0x0EBDBBE4
OPERATION_ID = 0xDF

# Clear of the API tracer's slots, which start at 0x89046000.
STUB = 0x89048000
STUB_SIZE = 0x80
JOURNAL = 0x89048100
RECORD_COUNT = 8
RECORD_SIZE = 0x10
JOURNAL_SIZE = RECORD_SIZE + RECORD_COUNT * RECORD_SIZE


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub() -> bytes:
    """Record the object, the resolved method and the operation id.

    r11 carries the method the displaced ``mtctr`` consumes and r3/r4 are the
    call's own arguments, so the recorder may only use r10 and r12 -- r12 is
    dead here, the retail prologue saved it long before.
    """
    words: list[int] = []
    words.extend(pointer_load(12, JOURNAL))
    words.extend(
        (
            lwz(10, 12, 0),
            addi(10, 10, 1),
            stw(10, 12, 0),
            addi(10, 10, -1),
            rlwinm(10, 10, 0, 29, 31),  # index = count % RECORD_COUNT
            rlwinm(10, 10, 4, 24, 27),  # index * RECORD_SIZE
            add(12, 12, 10),
            stw(3, 12, RECORD_SIZE + 0x0),
            stw(4, 12, RECORD_SIZE + 0x4),
            stw(11, 12, RECORD_SIZE + 0x8),
            0x7C0004AC,  # sync
            int.from_bytes(ORIGINAL, "big"),  # displaced mtctr r11
        )
    )
    tail = STUB + len(words) * 4
    words.append(branch(tail, SITE + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("FirstTimeInit submit stub exceeds its cave")
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


def apply_trace(client: Xbdm) -> None:
    desired = build_stub()
    if hook_state(client) == "unexpected":
        raise RuntimeError("Refusing an unexpected FirstTimeInit call site")
    if client.read(SITE, 4) != ORIGINAL:
        client.write(SITE, ORIGINAL)
        if client.read(SITE, 4) != ORIGINAL:
            raise RuntimeError("Could not unpublish the previous trace")
    existing = client.read(STUB, STUB_SIZE)
    if existing not in (bytes(STUB_SIZE), desired):
        raise RuntimeError("FirstTimeInit submit cave is not free")
    write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
    write_chunks(client, STUB, desired)
    if client.read(STUB, STUB_SIZE) != desired:
        raise RuntimeError("FirstTimeInit submit stub verification failed")
    client.write(SITE, site_patch())
    if hook_state(client) != "armed":
        raise RuntimeError("FirstTimeInit submit hook verification failed")


def read_journal(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    count = int.from_bytes(raw[0:4], "big")
    print(f"submissions = {count}")
    if not count:
        print("FirstTimeInit never reached its submit call.")
        return
    for sequence in range(max(0, count - RECORD_COUNT), count):
        offset = RECORD_SIZE + (sequence % RECORD_COUNT) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        request = int.from_bytes(record[0:4], "big")
        operation = int.from_bytes(record[4:8], "big")
        method = int.from_bytes(record[8:12], "big")
        print(
            f"  {sequence:4d}  request=0x{request:08X} "
            f"operation=0x{operation:X} submit=0x{method:08X}"
        )
        if operation != OPERATION_ID:
            print(f"          note: expected operation 0x{OPERATION_ID:X}")
        if request:
            vtable = int.from_bytes(client.read(request, 4), "big")
            print(f"          request vtable = 0x{vtable:08X}")


def arm_on_load(host: str, timeout: float) -> int:
    notify = Connection(host)
    control: Connection | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFASubmitTrace" user="CodexMac"'
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
            apply_trace(control)
            print(
                "Verified: passive FirstTimeInit submit trace armed at "
                "module load.",
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
        "action", choices=("status", "apply", "restore", "read", "arm-on-load")
    )
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    if args.action == "arm-on-load":
        return arm_on_load(args.host, args.timeout)

    client = Xbdm(args.host)
    try:
        verify_module(client)
        state = hook_state(client)
        print(f"FirstTimeInit submit trace: {state}")
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
                    raise RuntimeError("FirstTimeInit submit restore failed")
            print("Verified: original FirstTimeInit call site restored.")
            return 0
        apply_trace(client)
        print("Verified: passive FirstTimeInit submit trace armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
