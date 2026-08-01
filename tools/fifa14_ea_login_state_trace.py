#!/usr/bin/env python3
"""Passively journal FIFA 14's native EA-login outcome and status queries.

The probes do not alter a return value or publish an event.  Entry probes
snapshot the ABI arguments and execute the displaced retail instruction.
Selected boolean helpers also replace their final ``blr`` with a tiny logger
that records r3 before returning normally to the caller.
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


JOURNAL = 0x83C8AA00
RECORD_SIZE = 0x30
ENTRY_STUB_BASE = 0x83C8AC00
ENTRY_STUB_STRIDE = 0x50
RETURN_STUB_BASE = 0x83C8AE80
RETURN_STUB_STRIDE = 0x20


@dataclass(frozen=True)
class Probe:
    name: str
    entry: int
    entry_original_hex: str
    return_site: int | None = None
    snapshot_register: int | None = None
    snapshot_offset: int = 0

    @property
    def entry_original(self) -> bytes:
        return bytes.fromhex(self.entry_original_hex)


PROBES = (
    Probe("online_app_init", 0x82E598D8, "7D8802A6"),
    Probe(
        "login_success_publisher",
        0x83A59B48,
        "7D8802A6",
        snapshot_register=5,
        snapshot_offset=8,
    ),
    Probe(
        "login_failure_publisher",
        0x83A78F00,
        "7D8802A6",
        snapshot_register=5,
        snapshot_offset=8,
    ),
    Probe("login_query_invt_v44", 0x83A34090, "7D8802A6", 0x83A340DC),
    Probe("login_query_invt_v48", 0x83A340E0, "7D8802A6", 0x83A3412C),
    Probe("login_query_any_ea", 0x83A3C950, "7D8802A6", 0x83A3C9A0),
    Probe("login_query_manager_v28", 0x83A3CA90, "7D8802A6", 0x83A3CACC),
    Probe("login_query_manager_v2c", 0x83A3CAD0, "7D8802A6", 0x83A3CB0C),
)

RETURN_ORIGINAL = bytes.fromhex("4E800020")  # blr


def entry_stub_address(index: int) -> int:
    return ENTRY_STUB_BASE + index * ENTRY_STUB_STRIDE


def return_stub_address(index: int) -> int:
    return RETURN_STUB_BASE + index * RETURN_STUB_STRIDE


def entry_patch(index: int, probe: Probe) -> bytes:
    return insn(branch(probe.entry, entry_stub_address(index), False))


def return_patch(index: int, probe: Probe) -> bytes:
    if probe.return_site is None:
        raise ValueError(f"{probe.name} has no return probe")
    return insn(branch(probe.return_site, return_stub_address(index), False))


def build_entry_stub(index: int, probe: Probe) -> bytes:
    stub = entry_stub_address(index)
    record = index * RECORD_SIZE
    words = [
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x5600),       # JOURNAL = 0x83C8AA00
        lwz(10, 11, record + 0x00),
        addi(10, 10, 1),
        stw(10, 11, record + 0x00),
        stw(3, 11, record + 0x04),
        stw(4, 11, record + 0x08),
        stw(5, 11, record + 0x0C),
        stw(6, 11, record + 0x10),
        0x7D4802A6,                  # mflr r10
        stw(10, 11, record + 0x14),
    ]
    if probe.snapshot_register is not None:
        words.extend(
            (
                lwz(10, probe.snapshot_register, probe.snapshot_offset),
                stw(10, 11, record + 0x20),
            )
        )
    branch_site = stub + (len(words) + 1) * 4
    words.extend(
        (
            int.from_bytes(probe.entry_original, "big"),
            branch(branch_site, probe.entry + 4, False),
        )
    )
    image = b"".join(insn(word) for word in words)
    if len(image) > ENTRY_STUB_STRIDE:
        raise AssertionError(f"{probe.name} entry trace exceeds its slot")
    return image.ljust(ENTRY_STUB_STRIDE, b"\0")


def build_return_stub(index: int) -> bytes:
    record = index * RECORD_SIZE
    words = (
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x5600),       # JOURNAL = 0x83C8AA00
        lwz(10, 11, record + 0x18),
        addi(10, 10, 1),
        stw(10, 11, record + 0x18),
        stw(3, 11, record + 0x1C),
        int.from_bytes(RETURN_ORIGINAL, "big"),
    )
    image = b"".join(insn(word) for word in words)
    if len(image) > RETURN_STUB_STRIDE:
        raise AssertionError("EA-login return trace exceeds its slot")
    return image.ljust(RETURN_STUB_STRIDE, b"\0")


def probe_states(client: Xbdm) -> list[tuple[str, str]]:
    states: list[tuple[str, str]] = []
    for index, probe in enumerate(PROBES):
        current = client.read(probe.entry, 4)
        entry_state = (
            "original" if current == probe.entry_original else
            "armed" if current == entry_patch(index, probe) else
            f"unexpected:{current.hex().upper()}"
        )
        return_state = "none"
        if probe.return_site is not None:
            current = client.read(probe.return_site, 4)
            return_state = (
                "original" if current == RETURN_ORIGINAL else
                "armed" if current == return_patch(index, probe) else
                f"unexpected:{current.hex().upper()}"
            )
        states.append((entry_state, return_state))
    return states


def arm(client: Xbdm) -> None:
    states = probe_states(client)
    if any(
        state.startswith("unexpected:")
        for pair in states
        for state in pair
    ):
        raise RuntimeError("At least one EA-login trace site is unexpected")
    write_chunks(client, JOURNAL, bytes(len(PROBES) * RECORD_SIZE))
    for index, probe in enumerate(PROBES):
        write_chunks(client, entry_stub_address(index), build_entry_stub(index, probe))
        client.write(probe.entry, entry_patch(index, probe))
        if probe.return_site is not None:
            write_chunks(client, return_stub_address(index), build_return_stub(index))
            client.write(probe.return_site, return_patch(index, probe))
    verified = probe_states(client)
    if any(state != "armed" for pair in verified for state in pair if state != "none"):
        raise RuntimeError("EA-login trace verification failed")


def restore(client: Xbdm) -> None:
    states = probe_states(client)
    for index, (probe, pair) in enumerate(zip(PROBES, states)):
        entry_state, return_state = pair
        if entry_state == "armed":
            client.write(probe.entry, probe.entry_original)
        elif entry_state != "original":
            raise RuntimeError(f"Unexpected entry at 0x{probe.entry:08X}")
        if probe.return_site is None:
            continue
        if return_state == "armed":
            client.write(probe.return_site, RETURN_ORIGINAL)
        elif return_state != "original":
            raise RuntimeError(f"Unexpected return at 0x{probe.return_site:08X}")


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, len(PROBES) * RECORD_SIZE)
    for index, probe in enumerate(PROBES):
        record = raw[index * RECORD_SIZE : (index + 1) * RECORD_SIZE]
        entry_count = u32(record, 0x00)
        return_count = u32(record, 0x18)
        if not entry_count and not return_count:
            continue
        line = (
            f"{probe.name:28} entry={entry_count} "
            f"r3=0x{u32(record, 0x04):08X} "
            f"r4=0x{u32(record, 0x08):08X} "
            f"r5=0x{u32(record, 0x0C):08X} "
            f"r6=0x{u32(record, 0x10):08X} "
            f"caller=0x{(u32(record, 0x14) - 4) & 0xFFFFFFFF:08X}"
        )
        if probe.snapshot_register is not None:
            line += f" state={u32(record, 0x20)}"
        if probe.return_site is not None:
            line += f" return={return_count} result={u32(record, 0x1C) & 0xFF}"
        print(line)
    if not any(raw):
        print("No native EA-login state probe was hit.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = probe_states(client)
        print(
            "EA-login state trace: "
            f"{sum(state == 'armed' for pair in states for state in pair)} armed, "
            f"{sum(state == 'original' for pair in states for state in pair)} original, "
            f"{sum(state.startswith('unexpected:') for pair in states for state in pair)} unexpected"
        )
        if args.action == "status":
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "apply":
            arm(client)
            print("Verified: native EA-login outcome and status queries are journaled.")
            return 0
        restore(client)
        print("Verified: native EA-login state trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
