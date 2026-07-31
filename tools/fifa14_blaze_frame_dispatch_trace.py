#!/usr/bin/env python3
"""Journal FIFA 14's Blaze frame completion and response callback dispatch."""

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
)
from fifa14_plain_recv_hook import cmpwi, conditional_branch


JOURNAL = 0x83C8C400
JOURNAL_SIZE = 0x1D0


@dataclass(frozen=True)
class Probe:
    name: str
    site: int
    original: bytes
    stub: int
    journal_offset: int
    registers: tuple[int, ...]


PROBES = (
    Probe(
        "header_complete",
        0x82EB0E60,
        bytes.fromhex("817E0004"),  # lwz r11,4(r30)
        0x83C8C000,
        0x00,
        (31, 30, 10, 26),
    ),
    Probe(
        "frame_complete",
        0x82EB105C,
        bytes.fromhex("817F0CA0"),  # lwz r11,0xCA0(r31)
        0x83C8C080,
        0x40,
        (31, 30, 10, 26),
    ),
    Probe(
        "direct_callback",
        0x82EB109C,
        bytes.fromhex("7D6903A6"),  # mtctr r11
        0x83C8C100,
        0x80,
        (11, 3, 4, 5),
    ),
    Probe(
        "fallback_callback",
        0x82EB10B8,
        bytes.fromhex("7D6903A6"),  # mtctr r11
        0x83C8C180,
        0xC0,
        (11, 3, 4, 5),
    ),
    Probe(
        "callback_return",
        0x82EB10C4,
        bytes.fromhex("817F0014"),  # lwz r11,0x14(r31)
        0x83C8C200,
        0x100,
        (3, 31, 30, 10),
    ),
    Probe(
        "request_lookup",
        0x82EB063C,
        bytes.fromhex("7C7F1B79"),  # or. r31,r3,r3
        0x83C8C240,
        0x120,
        (3, 30, 28, 27),
    ),
    Probe(
        "parser_call",
        0x82EAF490,
        bytes.fromhex("389F0034"),  # addi r4,r31,0x34
        0x83C8C280,
        0x138,
        (11, 31, 30, 29),
    ),
    Probe(
        "parser_return",
        0x82EAF4A0,
        bytes.fromhex("7C651B79"),  # or. r5,r3,r3
        0x83C8C2C0,
        0x150,
        (3, 31, 30, 29),
    ),
    Probe(
        "completion_call",
        0x82EAF4C4,
        bytes.fromhex("7D6903A6"),  # mtctr r11
        0x83C8C300,
        0x168,
        (11, 31, 3, 5),
    ),
    Probe(
        "user_callback",
        0x82EAE254,
        bytes.fromhex("7D6903A6"),  # mtctr r11
        0x83C8C340,
        0x180,
        (11, 3, 4, 5),
    ),
)


def build_stub(probe: Probe) -> bytes:
    if probe.name == "user_callback":
        return build_qos_user_callback_stub(probe)
    return build_generic_stub(probe)


def build_generic_stub(probe: Probe) -> bytes:
    displaced = int.from_bytes(probe.original, "big")
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3C00),  # JOURNAL
        lwz(9, 12, probe.journal_offset),
        addi(9, 9, 1),
        stw(9, 12, probe.journal_offset),
    ]
    for index, register in enumerate(probe.registers):
        words.append(
            stw(register, 12, probe.journal_offset + 4 + index * 4)
        )
    words.extend((displaced, 0))
    words[-1] = branch(
        probe.stub + (len(words) - 1) * 4,
        probe.site + 4,
        False,
    )
    return b"".join(insn(word) for word in words)


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_qos_user_callback_stub(probe: Probe) -> bytes:
    """Only retain the UserSessions/updateNetworkInfo completion."""
    displaced = int.from_bytes(probe.original, "big")
    words = [
        lhz(10, 3, 0x28),           # request component
        cmpwi(10, 0x7802),
        0,                           # bne finish
        lhz(10, 3, 0x2A),           # request command
        cmpwi(10, 20),
        0,                           # bne finish
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3C00),      # JOURNAL
        lwz(9, 12, probe.journal_offset),
        addi(9, 9, 1),
        stw(9, 12, probe.journal_offset),
        stw(11, 12, probe.journal_offset + 0x04),
        stw(3, 12, probe.journal_offset + 0x08),
        stw(4, 12, probe.journal_offset + 0x0C),
        stw(5, 12, probe.journal_offset + 0x10),
        stw(6, 12, probe.journal_offset + 0x14),
    ]
    for index, request_offset in enumerate(
        (0x00, 0x2C, 0x30, 0x38, 0x3C, 0x44, 0x48, 0x4C, 0x50)
    ):
        words.append(lwz(10, 3, request_offset))
        words.append(
            stw(10, 12, probe.journal_offset + 0x18 + index * 4)
        )
    finish = len(words)
    words.extend((displaced, 0))

    def address(index: int) -> int:
        return probe.stub + index * 4

    for index in (2, 5):
        words[index] = conditional_branch(
            address(index), address(finish), 4, 2
        )
    words[-1] = branch(
        address(len(words) - 1), probe.site + 4, False
    )
    return b"".join(insn(word) for word in words)


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    for probe in PROBES:
        offset = probe.journal_offset
        values = [u32(raw, offset + 4 + index * 4) for index in range(4)]
        print(
            f"{probe.name:18} count={u32(raw, offset):4d} "
            + " ".join(f"r{reg}=0x{value:08X}" for reg, value in zip(probe.registers, values))
        )
        if probe.name == "user_callback" and u32(raw, offset):
            names = (
                "r6",
                "vtable",
                "error",
                "response",
                "error_response",
                "connection",
                "delegate_0",
                "delegate_4",
                "delegate_8",
                "delegate_C",
            )
            for index, name in enumerate(names):
                value = u32(raw, offset + 0x14 + index * 4)
                print(f"  {name:16} = 0x{value:08X}")
    frame_connection = u32(raw, 0x44)
    if frame_connection:
        state = client.read(frame_connection + 0xC60, 0x60)
        print(f"connection          = 0x{frame_connection:08X}")
        print(f"state_C60           = 0x{u32(state, 0):08X}")
        print(f"callback_C70        = 0x{u32(state, 0x10):08X}")
        print(f"callback_CA0        = 0x{u32(state, 0x40):08X}")
        print(f"callback_CA8        = 0x{u32(state, 0x48):08X}")
        print(f"frame_state_CB8     = 0x{u32(state, 0x58):08X}")
    callback_descriptor = u32(raw, 0x88)
    if callback_descriptor:
        descriptor = client.read(callback_descriptor, 0x10)
        callback_offset = u32(descriptor, 0x04)
        callback_thunk = u32(descriptor, 0x08)
        callback_base = u32(descriptor, 0x0C)
        print(f"callback_descriptor = 0x{callback_descriptor:08X}")
        print(f"callback_offset     = 0x{callback_offset:08X}")
        print(f"callback_thunk      = 0x{callback_thunk:08X}")
        print(f"callback_base       = 0x{callback_base:08X}")
        print(
            "callback_object     = "
            f"0x{(callback_base + callback_offset) & 0xFFFFFFFF:08X}"
        )
    frame_descriptor = u32(raw, 0x8C)
    if frame_descriptor:
        frame = client.read(frame_descriptor, 0x20)
        print(f"frame_descriptor    = 0x{frame_descriptor:08X}")
        print(f"frame_words         = {frame.hex().upper()}")
        frame_data = u32(frame, 0)
        if frame_data:
            header = client.read(frame_data, 0x10)
            print(f"frame_data          = 0x{frame_data:08X}")
            print(f"frame_header        = {header.hex().upper()}")
            print(
                "frame_route         = "
                f"{header[2:6].hex().upper()} "
                f"error={int.from_bytes(header[6:8], 'big')} "
                f"type={header[8] >> 4} "
                f"txn={((header[9] & 0x0F) << 16) | int.from_bytes(header[10:12], 'big')}"
            )
    request = u32(raw, 0x124)
    if request:
        request_data = client.read(request, 0x50)
        print(f"request_entry       = 0x{request:08X}")
        print(f"request_words       = {request_data.hex().upper()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states: list[tuple[Probe, bytes, bytes, bytes]] = []
        for probe in PROBES:
            stub = build_stub(probe)
            patch = insn(branch(probe.site, probe.stub, False))
            states.append((probe, stub, patch, client.read(probe.site, 4)))
        print(
            "Blaze dispatch trace: "
            + " ".join(
                f"{probe.name}={current.hex().upper()}"
                for probe, _, _, current in states
            )
        )
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            for probe, stub, patch, current in states:
                if current not in (probe.original, patch):
                    raise RuntimeError(
                        f"Unexpected instruction at 0x{probe.site:08X}: "
                        f"{current.hex().upper()}"
                    )
                cave = client.read(probe.stub, len(stub))
                allowed = (bytes(len(stub)), stub)
                if probe.name == "user_callback":
                    legacy = build_generic_stub(probe).ljust(len(stub), b"\0")
                    allowed += (legacy,)
                if cave not in allowed:
                    raise RuntimeError(
                        f"Code cave 0x{probe.stub:08X} is not free"
                    )
            write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
            for probe, stub, patch, _ in states:
                write_chunks(client, probe.stub, stub)
                client.write(probe.site, patch)
            print("Verified: Blaze frame-dispatch trace armed.")
            return 0
        for probe, _, patch, current in states:
            if current == patch:
                client.write(probe.site, probe.original)
            elif current != probe.original:
                raise RuntimeError(
                    f"Unexpected instruction at 0x{probe.site:08X}"
                )
        print("Verified: Blaze frame-dispatch trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
