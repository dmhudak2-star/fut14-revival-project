#!/usr/bin/env python3
"""Passively trace UXLua's two ``Could not find '%s'`` error branches.

The retail external-flow loader uses these branches when a requested Lua/UX
function or asynchronously loaded module cannot be resolved.  The probe only
records the missing-name pointer and surrounding ABI values.  It never hides
the error, supplies a module, resumes a flow, or changes a frontend result.
"""

from __future__ import annotations

import argparse
import time
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


# The ION action-pipeline ring ends at 0x83C8A800.  Native EA-login
# diagnostics begin at 0x83C8AA00.
STUB_BASE = 0x83C8A800
STUB_STRIDE = 0x80
JOURNAL = 0x83C8A900
RECORD_SIZE = 0x40
CAVE_END = JOURNAL + 2 * RECORD_SIZE
NEXT_KNOWN_CAVE = 0x83C8AA00


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    original: bytes
    original_first: bool
    string_register: int

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE

    @property
    def record(self) -> int:
        return JOURNAL + (self.event_id - 1) * RECORD_SIZE


PROBES = (
    # r30 is passed as the %s value to "Could not find '%s'".
    Probe(1, "ux_function_not_found", 0x83729A20,
          bytes.fromhex("3D60821B"), False, 30),
    # The displaced lwz materialises the missing-name pointer in r5.
    Probe(2, "ux_async_module_not_found", 0x8372A000,
          bytes.fromhex("80AA0000"), True, 5),
)


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def build_stub(probe: Probe) -> bytes:
    original = int.from_bytes(probe.original, "big")
    words: list[int] = []
    if probe.original_first:
        words.append(original)
    words.extend(
        (
            0x7D0802A6,  # mflr r8
            addis(11, 0, (probe.record + 0x8000) >> 16),
            addi(11, 11, probe.record & 0xFFFF),
            lwz(9, 11, 0x00),
            addi(9, 9, 1),
            stw(9, 11, 0x00),
            stw(3, 11, 0x04),
            stw(4, 11, 0x08),
            stw(5, 11, 0x0C),
            stw(6, 11, 0x10),
            stw(7, 11, 0x14),
            stw(10, 11, 0x18),
            stw(30, 11, 0x1C),
            stw(31, 11, 0x20),
            stw(8, 11, 0x24),
        )
    )
    if not probe.original_first:
        words.append(original)
    tail = probe.stub + len(words) * 4
    words.append(branch(tail, probe.site + 4, False))
    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot")
    return raw.ljust(STUB_STRIDE, b"\0")


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE != JOURNAL:
        raise RuntimeError("UX Lua error stub allocation is inconsistent")
    if CAVE_END > NEXT_KNOWN_CAVE:
        raise RuntimeError("UX Lua error trace overlaps EA-login diagnostics")


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == probe.original:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def arm(client: Xbdm) -> None:
    verify_module(client)
    verify_layout()
    states = [state_for(client, probe) for probe in PROBES]
    unexpected = [
        f"0x{probe.site:08X}={state}"
        for probe, state in zip(PROBES, states)
        if state not in ("original", "traced")
    ]
    if unexpected:
        raise RuntimeError("refusing unknown UX Lua site(s): " + ", ".join(unexpected))

    images = [build_stub(probe) for probe in PROBES]
    for probe, image in zip(PROBES, images):
        cave = client.read(probe.stub, STUB_STRIDE)
        if cave not in (bytes(STUB_STRIDE), image):
            raise RuntimeError(f"UX Lua trace cave 0x{probe.stub:08X} is occupied")

    if "traced" in states:
        for probe, state in zip(PROBES, states):
            if state == "traced":
                client.write(probe.site, probe.original)
        time.sleep(0.02)
    try:
        write_chunks(client, JOURNAL, bytes(CAVE_END - JOURNAL))
        for probe, image in zip(PROBES, images):
            write_chunks(client, probe.stub, image)
        for probe in PROBES:
            client.write(probe.site, patch_for(probe))
        if any(state_for(client, probe) != "traced" for probe in PROBES):
            raise RuntimeError("one or more UX Lua error probes did not publish")
    except Exception:
        for probe in PROBES:
            try:
                if state_for(client, probe) == "traced":
                    client.write(probe.site, probe.original)
            except Exception:
                pass
        raise


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def safe_cstring(client: Xbdm, pointer: int, limit: int = 0x100) -> str | None:
    if not 0x20000000 <= pointer < 0xE0000000:
        return None
    try:
        raw = client.read(pointer, limit).split(b"\0", 1)[0]
    except Exception:
        return None
    if not raw or any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None
    return raw.decode("ascii")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, CAVE_END - JOURNAL)
    hits = 0
    for probe in PROBES:
        offset = (probe.event_id - 1) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        count = u32(record, 0x00)
        if not count:
            continue
        hits += 1
        registers = {
            3: u32(record, 0x04), 4: u32(record, 0x08),
            5: u32(record, 0x0C), 6: u32(record, 0x10),
            7: u32(record, 0x14), 10: u32(record, 0x18),
            30: u32(record, 0x1C), 31: u32(record, 0x20),
        }
        name_pointer = registers[probe.string_register]
        print(
            f"{probe.name:28s} hits={count} missing=0x{name_pointer:08X} "
            f"text={safe_cstring(client, name_pointer)!r} "
            f"LR=0x{u32(record, 0x24):08X}"
        )
        print(
            "  " + " ".join(
                f"r{register}=0x{value:08X}"
                for register, value in registers.items()
            )
        )
    if not hits:
        print("No UXLua missing-function or missing-module branch was hit.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        verify_layout()
        states = [state_for(client, probe) for probe in PROBES]
        print(
            "UX Lua error trace: "
            f"{states.count('traced')} traced, "
            f"{states.count('original')} original, "
            f"{sum(state.startswith('unexpected:') for state in states)} unexpected"
        )
        if args.action == "status":
            for probe, state in zip(PROBES, states):
                print(f"  0x{probe.site:08X} {probe.name}: {state}")
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "restore":
            for probe, state in zip(PROBES, states):
                if state == "traced":
                    client.write(probe.site, probe.original)
                elif state != "original":
                    raise RuntimeError(f"unexpected entry at 0x{probe.site:08X}: {state}")
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("one or more UX Lua entries did not restore")
            print("Verified: UX Lua error entries restored.")
            return 0

        arm(client)
        print("Verified: passive UX Lua error trace armed.")
        print("No error, resource, completion or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
