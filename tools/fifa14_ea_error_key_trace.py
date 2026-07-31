#!/usr/bin/env python3
"""Trace every plausible EA/EASW server-error localization-key load."""

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


STUB_BASE = 0x83C8EC00
STUB_STRIDE = 0x30
JOURNAL = 0x83C8EB00
JOURNAL_SIZE = 0x100


@dataclass(frozen=True)
class Probe:
    site: int
    original_hex: str
    key: str

    @property
    def original(self) -> bytes:
        return bytes.fromhex(self.original_hex)


PROBES = (
    Probe(0x82558BA8, "3BEBC9C0", "SERVER_CONN_LOST"),
    Probe(0x827B21D0, "388B3EA0", "OSDK_UNAVAILABLE"),
    Probe(0x827D2150, "388A4F20", "STORE_ERROR_NOEASERVERS"),
    Probe(0x827F02BC, "388A5FB4", "STORE_ERROR_SERVER_MAINTENANCE"),
    Probe(0x828595A8, "3BCBE8BC", "FLB_SERVER_DOWN/main"),
    Probe(0x82A85A2C, "38ABE8BC", "FLB_SERVER_DOWN/secondary"),
    Probe(0x82EADDBC, "386BB450", "ERR_CONNECTION_FAILED"),
    Probe(0x82EF1214, "386BC3EC", "UTIL_PSS_NO_SERVERS_AVAILABLE"),
    Probe(0x82EF1208, "386BC40C", "UTIL_TELEMETRY_NO_SERVERS_AVAILABLE"),
    Probe(0x82EF11D8, "386BC490", "UTIL_TICKER_NO_SERVERS_AVAILABLE"),
    Probe(0x82EF9010, "386BFE90", "ERR_SERVER_BUSY"),
    Probe(0x82EF8B54, "386B038C", "SDK_ERR_SERVER_DISCONNECT"),
    Probe(0x82F34720, "386B4B00", "REDIRECTOR_SERVER_DOWN"),
    Probe(0x82F3466C, "386B4BB0", "REDIRECTOR_SERVER_SUNSET"),
    Probe(0x82F34660, "386B4BCC", "REDIRECTOR_SERVER_NOT_FOUND"),
    Probe(0x82F34654, "386B4BE8", "REDIRECTOR_NO_SERVER_CAPACITY"),
)


def stub_address(index: int) -> int:
    return STUB_BASE + index * STUB_STRIDE


def build_stub(index: int, probe: Probe) -> bytes:
    word = int.from_bytes(probe.original, "big")
    target_register = (word >> 21) & 0x1F
    count_offset = index * 8
    scratch_offset = count_offset + 4
    address = stub_address(index)
    words = [
        addis(target_register, 0, 0x83C9),
        addi(target_register, target_register, -0x1500),  # JOURNAL
        stw(0, target_register, scratch_offset),
        lwz(0, target_register, count_offset),
        addi(0, 0, 1),
        stw(0, target_register, count_offset),
        lwz(0, target_register, scratch_offset),
        word,
    ]
    # Each stub occupies exactly STUB_STRIDE bytes. The site's branch back is
    # installed in the final word by replacing the displaced instruction's
    # fall-through with a direct branch.
    words.append(branch(address + len(words) * 4, probe.site + 4, False))
    raw = b"".join(insn(item) for item in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError("Probe stub exceeds its slot")
    return raw.ljust(STUB_STRIDE, b"\0")


def patch_for(index: int, probe: Probe) -> bytes:
    return insn(branch(probe.site, stub_address(index), False))


def read_counts(client: Xbdm) -> list[int]:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    return [
        int.from_bytes(raw[index * 8 : index * 8 + 4], "big")
        for index in range(len(PROBES))
    ]


def describe(client: Xbdm) -> None:
    counts = read_counts(client)
    hits = 0
    for probe, count in zip(PROBES, counts):
        if count:
            hits += 1
            print(f"{count:8d}  0x{probe.site:08X}  {probe.key}")
    if not hits:
        print("No candidate EA error key was loaded.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = []
        for index, probe in enumerate(PROBES):
            current = client.read(probe.site, 4)
            patch = patch_for(index, probe)
            state = (
                "original"
                if current == probe.original
                else "patched"
                if current == patch
                else f"unexpected:{current.hex().upper()}"
            )
            states.append(state)
        print(
            "EA error-key probes: "
            f"{states.count('patched')} patched, "
            f"{states.count('original')} original, "
            f"{sum(s.startswith('unexpected:') for s in states)} unexpected"
        )

        if args.action in ("status", "read"):
            describe(client)
            return 0

        if args.action == "apply":
            unexpected = [
                f"0x{probe.site:08X}={state}"
                for probe, state in zip(PROBES, states)
                if state not in ("original", "patched")
            ]
            if unexpected:
                raise RuntimeError(
                    "Unexpected candidate instruction(s): " + ", ".join(unexpected)
                )
            for index, probe in enumerate(PROBES):
                stub = build_stub(index, probe)
                cave = client.read(stub_address(index), len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError(
                        f"Probe cave at 0x{stub_address(index):08X} is not free"
                    )
            write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
            for index, probe in enumerate(PROBES):
                client.write(stub_address(index), build_stub(index, probe))
                client.write(probe.site, patch_for(index, probe))
            for index, probe in enumerate(PROBES):
                if client.read(probe.site, 4) != patch_for(index, probe):
                    raise RuntimeError(
                        f"Probe verification failed at 0x{probe.site:08X}"
                    )
            print("Verified: all EA error-key probes armed and counters cleared.")
            return 0

        for index, (probe, state) in enumerate(zip(PROBES, states)):
            if state == "patched":
                client.write(probe.site, probe.original)
            elif state != "original":
                raise RuntimeError(
                    f"Unexpected instruction at 0x{probe.site:08X}: {state}"
                )
        print("Verified: all EA error-key probes restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
