#!/usr/bin/env python3
"""Journal the receive-path branches taken by a synthetic Blaze reply."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    stw,
    verify_module,
)


JOURNAL = 0x83C8D800
JOURNAL_SIZE = 0x100


@dataclass(frozen=True)
class Probe:
    name: str
    site: int
    original: bytes
    stub: int
    marker_offset: int
    registers: tuple[int, ...]


PROBES = (
    Probe("header_ready", 0x83AC8508, bytes.fromhex("815FFAD4"), 0x83C8D300, 0x00, (31, 10, 11, 28)),
    Probe("connection_loop", 0x83AC85F4, bytes.fromhex("839FFAE0"), 0x83C8D380, 0x20, (30, 28, 21, 19)),
    Probe("frame_available", 0x83AC8664, bytes.fromhex("817FFAD4"), 0x83C8D400, 0x40, (30, 28, 21, 29)),
    Probe("route_state", 0x83AC86D0, bytes.fromhex("817E00A0"), 0x83C8D480, 0x60, (30, 28, 29, 21)),
    Probe("payload_type", 0x83AC8750, bytes.fromhex("2B1C0001"), 0x83C8D500, 0x80, (30, 28, 29, 23)),
    Probe("dispatch_state", 0x83AC8770, bytes.fromhex("817E00A0"), 0x83C8D580, 0xA0, (30, 28, 29, 21)),
    Probe("application_dispatch", 0x83AC88B0, bytes.fromhex("917E0100"), 0x83C8D600, 0xC0, (30, 28, 29, 23)),
    Probe("control_dispatch", 0x83AC88E0, bytes.fromhex("389FFAD4"), 0x83C8D680, 0xE0, (30, 28, 29, 23)),
)


def build_stub(probe: Probe) -> bytes:
    displaced = int.from_bytes(probe.original, "big")
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x2800),      # r12 = JOURNAL
        stw(31, 12, probe.marker_offset),
    ]
    for index, register in enumerate(probe.registers):
        words.append(
            stw(register, 12, probe.marker_offset + 4 + index * 4)
        )
    words.extend((displaced, 0))
    words[-1] = branch(
        probe.stub + (len(words) - 1) * 4, probe.site + 4, False
    )
    return b"".join(insn(word) for word in words)


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def read_journal(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    for probe in PROBES:
        start = probe.marker_offset
        values = [
            int.from_bytes(raw[start + offset : start + offset + 4], "big")
            for offset in range(0, 0x14, 4)
        ]
        hit = values[0] != 0
        rendered = " ".join(f"0x{value:08X}" for value in values)
        print(f"{probe.name:20} hit={str(hit):5} {rendered}")


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
            "Receive path sites: "
            + " ".join(
                f"{probe.name}={current.hex().upper()}"
                for probe, _, _, current in states
            )
        )
        if args.action in ("status", "read"):
            read_journal(client)
            return 0
        if args.action == "apply":
            for probe, stub, patch, current in states:
                if current not in (probe.original, patch):
                    raise RuntimeError(
                        f"Unexpected instruction at 0x{probe.site:08X}"
                    )
                cave = client.read(probe.stub, len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError(
                        f"Code cave 0x{probe.stub:08X} is not free"
                    )
            write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
            for probe, stub, patch, _ in states:
                write_chunks(client, probe.stub, stub)
                client.write(probe.site, patch)
            for probe, _, patch, _ in states:
                if client.read(probe.site, 4) != patch:
                    raise RuntimeError(
                        f"Probe verification failed at 0x{probe.site:08X}"
                    )
            print("Verified: Blaze receive path trace armed.")
            return 0
        for probe, _, patch, current in states:
            if current == patch:
                client.write(probe.site, probe.original)
            elif current != probe.original:
                raise RuntimeError(
                    f"Unexpected instruction at 0x{probe.site:08X}"
                )
        print("Verified: Blaze receive path trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
