#!/usr/bin/env python3
"""Breakpoint-free journal for every LoginStateLogin event callback.

The trace is intentionally passive: each entry snapshots the callback inputs
and current login state, executes the displaced instruction, then returns to
the retail function.  It replaces the older single-event stack trace cave but
does not overlap the Login-Fail source probes at 0x83C8EB00 and above.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    lwz,
    stw,
    verify_module,
    write_chunks,
)


STUB_BASE = 0x83C8DE00
STUB_STRIDE = 0x40
JOURNAL = 0x83C8E000
RECORD_SIZE = 0x20


@dataclass(frozen=True)
class Callback:
    site: int
    original_hex: str
    state_displacement: int
    label: str

    @property
    def original(self) -> bytes:
        return bytes.fromhex(self.original_hex)


CALLBACKS = (
    Callback(0x8255CEF8, "7D8802A6", 0x138, "listener-0"),
    Callback(0x8255D030, "7D8802A6", 0x160, "listener-1"),
    Callback(0x8255D108, "7D8802A6", 0x138, "listener-2"),
    Callback(0x8255D4F8, "7D8802A6", 0x138, "listener-3"),
    Callback(0x8255D5E0, "7D8802A6", 0x138, "listener-4"),
    Callback(0x8255D808, "2F040000", 0x138, "listener-5"),
)

# The previous exact-event trace patched 0x8255D5E0 to 0x83C8DE00.
OLD_EVENT_PATCH = insn(branch(0x8255D5E0, 0x83C8DE00, False))


def stub_address(index: int) -> int:
    return STUB_BASE + index * STUB_STRIDE


def patch_for(index: int, callback: Callback) -> bytes:
    return insn(branch(callback.site, stub_address(index), False))


def build_stub(index: int, callback: Callback) -> bytes:
    address = stub_address(index)
    record = index * RECORD_SIZE
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2000),       # 0x83C8E000
        lwz(10, 12, record + 0x00),
        addi(10, 10, 1),
        stw(10, 12, record + 0x00),  # invocation count
        stw(3, 12, record + 0x04),
        stw(4, 12, record + 0x08),
        stw(5, 12, record + 0x0C),
        lwz(10, 3, callback.state_displacement),
        stw(10, 12, record + 0x10),  # LoginStateLogin state before callback
        0x7D4802A6,                  # mflr r10
        stw(10, 12, record + 0x14),
        int.from_bytes(callback.original, "big"),
        branch(address + 13 * 4, callback.site + 4, False),
    ]
    return b"".join(insn(word) for word in words).ljust(STUB_STRIDE, b"\0")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, len(CALLBACKS) * RECORD_SIZE)
    total = 0
    for index, callback in enumerate(CALLBACKS):
        record = raw[index * RECORD_SIZE : (index + 1) * RECORD_SIZE]
        count = int.from_bytes(record[0x00:0x04], "big")
        if not count:
            continue
        total += count
        r3 = int.from_bytes(record[0x04:0x08], "big")
        event = int.from_bytes(record[0x08:0x0C], "big")
        data = int.from_bytes(record[0x0C:0x10], "big")
        state = int.from_bytes(record[0x10:0x14], "big")
        lr = int.from_bytes(record[0x14:0x18], "big")
        print(
            f"0x{callback.site:08X} {callback.label}: count={count} "
            f"state={state} event=0x{event:08X} data=0x{data:08X} "
            f"r3=0x{r3:08X} caller=0x{(lr - 4) & 0xFFFFFFFF:08X}"
        )
    if not total:
        print("No LoginStateLogin callback was captured.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states: list[str] = []
        for index, callback in enumerate(CALLBACKS):
            current = client.read(callback.site, 4)
            if current == callback.original:
                states.append("original")
            elif current == patch_for(index, callback):
                states.append("armed")
            elif callback.site == 0x8255D5E0 and current == OLD_EVENT_PATCH:
                states.append("old-event-trace")
            else:
                states.append(f"unexpected:{current.hex().upper()}")
        print(
            "Login callback trace: "
            f"{states.count('armed')} armed, "
            f"{states.count('original')} original, "
            f"{states.count('old-event-trace')} old-event-trace, "
            f"{sum(s.startswith('unexpected:') for s in states)} unexpected"
        )

        if args.action == "status":
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "apply":
            if any(state.startswith("unexpected:") for state in states):
                raise RuntimeError("At least one callback entry is unexpected")
            if "old-event-trace" in states:
                client.write(0x8255D5E0, CALLBACKS[4].original)
            write_chunks(client, JOURNAL, bytes(len(CALLBACKS) * RECORD_SIZE))
            for index, callback in enumerate(CALLBACKS):
                write_chunks(client, stub_address(index), build_stub(index, callback))
                client.write(callback.site, patch_for(index, callback))
            for index, callback in enumerate(CALLBACKS):
                if client.read(callback.site, 4) != patch_for(index, callback):
                    raise RuntimeError(
                        f"Trace verification failed at 0x{callback.site:08X}"
                    )
            print("Verified: all LoginStateLogin callbacks are journaled.")
            return 0

        for index, (callback, state) in enumerate(zip(CALLBACKS, states)):
            if state == "armed":
                client.write(callback.site, callback.original)
            elif state == "old-event-trace":
                client.write(callback.site, callback.original)
            elif state != "original":
                raise RuntimeError(
                    f"Unexpected instruction at 0x{callback.site:08X}: {state}"
                )
        print("Verified: LoginStateLogin callback trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
