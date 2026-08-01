#!/usr/bin/env python3
"""Passively trace UserSessions login notifications and identity setup."""

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


JOURNAL = 0x83C8C900
RECORD_SIZE = 0x60


@dataclass(frozen=True)
class Probe:
    name: str
    site: int
    original: bytes
    stub: int
    registers: tuple[int, ...]
    object_register: int
    offsets: tuple[int, ...]


PROBES = (
    Probe(
        "user_added_callback",
        0x82EE5950,
        bytes.fromhex("7D8802A6"),  # mflr r12
        0x83C8C600,
        (3, 4, 5),
        4,
        (0x00, 0x08, 0x60, 0x128, 0x130, 0x134, 0x138, 0x180),
    ),
    Probe(
        "identity_setter",
        0x82EE2C10,
        bytes.fromhex("7D8802A6"),  # mflr r12
        0x83C8C700,
        (3, 4),
        4,
        (0x00, 0x04, 0x08, 0x0C, 0x10, 0x18, 0x20, 0x28),
    ),
    Probe(
        "user_authenticated_dispatch",
        0x82F0BC08,
        bytes.fromhex("4E800421"),  # bctrl through the component delegate
        0x83C8C800,
        (3, 4, 5),
        4,
        (0x00, 0x08, 0x18, 0x28, 0x34, 0x50, 0x68, 0x78),
    ),
)


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def build_stub(index: int, probe: Probe) -> bytes:
    record = index * RECORD_SIZE
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3700),  # JOURNAL = 0x83C8C900
        lwz(11, 12, record + 0x00),
        addi(11, 11, 1),
        stw(11, 12, record + 0x00),
    ]
    for slot, register in enumerate(probe.registers, start=1):
        words.append(stw(register, 12, record + slot * 4))
    for slot, offset in enumerate(probe.offsets):
        words.extend(
            (
                lwz(10, probe.object_register, offset),
                stw(10, 12, record + 0x20 + slot * 4),
            )
        )
    words.append(int.from_bytes(probe.original, "big"))
    words.append(branch(probe.stub + len(words) * 4, probe.site + 4, False))
    image = b"".join(insn(word) for word in words)
    if len(image) > 0x100:
        raise RuntimeError(f"{probe.name} trace exceeds its slot")
    return image.ljust(0x100, b"\0")


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, len(PROBES) * RECORD_SIZE)
    for index, probe in enumerate(PROBES):
        record = raw[index * RECORD_SIZE : (index + 1) * RECORD_SIZE]
        print(f"{probe.name:22} count={u32(record, 0x00)}")
        print(
            "  "
            + " ".join(
                f"r{register}=0x{u32(record, slot * 4):08X}"
                for slot, register in enumerate(probe.registers, start=1)
            )
        )
        print(
            "  object words: "
            + " ".join(
                f"+{offset:X}=0x{u32(record, 0x20 + slot * 4):08X}"
                for slot, offset in enumerate(probe.offsets)
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = []
        for probe in PROBES:
            current = client.read(probe.site, 4)
            states.append(
                "original" if current == probe.original else
                "armed" if current == patch_for(probe) else
                f"unexpected:{current.hex().upper()}"
            )
        print(
            "UserAdded trace: "
            f"{states.count('armed')} armed, "
            f"{states.count('original')} original, "
            f"{sum(state.startswith('unexpected:') for state in states)} unexpected"
        )
        if args.action == "status":
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "apply":
            if any(state.startswith("unexpected:") for state in states):
                raise RuntimeError("At least one UserAdded trace site is unexpected")
            write_chunks(client, JOURNAL, bytes(len(PROBES) * RECORD_SIZE))
            for index, probe in enumerate(PROBES):
                write_chunks(client, probe.stub, build_stub(index, probe))
                client.write(probe.site, patch_for(probe))
            for probe in PROBES:
                if client.read(probe.site, 4) != patch_for(probe):
                    raise RuntimeError(f"Trace verification failed at 0x{probe.site:08X}")
            print("Verified: UserAdded, UserAuthenticated and identity setup are journaled.")
            return 0

        for probe, state in zip(PROBES, states):
            if state == "armed":
                client.write(probe.site, probe.original)
            elif state != "original":
                raise RuntimeError(f"Unexpected instruction at 0x{probe.site:08X}")
        print("Verified: UserAdded trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
