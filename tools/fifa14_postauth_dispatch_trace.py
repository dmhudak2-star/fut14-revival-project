#!/usr/bin/env python3
"""Passively trace the complete Blaze Util::postAuth response path.

The hooks are filtered to component 9, command 8 and never stop execution.
They show whether the response transaction was found, decoded into the exact
PostAuthResponse object, completed, and finally delivered to its user callback.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from fifa14_plain_recv_hook import conditional_branch
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


STUB_BASE = 0x83C8C000
STUB_STRIDE = 0x90
JOURNAL = 0x83C8C400
RECORD_SIZE = 0x40


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


@dataclass(frozen=True)
class Probe:
    name: str
    site: int
    original_hex: str
    request_register: int
    registers: tuple[int, ...]
    frame_lookup: bool = False
    snapshot_delegate: bool = False

    @property
    def original(self) -> bytes:
        return bytes.fromhex(self.original_hex)


PROBES = (
    Probe(
        "request_lookup", 0x82EB063C, "7C7F1B79", 3,
        (3, 11, 30, 28, 27, 29), True,
    ),
    Probe(
        "handler_entry", 0x82EAF400, "9083002C", 3,
        (3, 4, 5, 6, 31, 29),
    ),
    Probe(
        "existing_decode_return", 0x82EAF438, "897E0011", 31,
        (3, 4, 5, 6, 31, 30),
    ),
    Probe(
        "response_factory_call", 0x82EAF490, "389F0034", 31,
        (11, 31, 30, 29, 3, 4),
    ),
    Probe(
        "response_factory_return", 0x82EAF4A0, "7C651B79", 31,
        (3, 31, 30, 29, 4, 5),
    ),
    Probe(
        "completion_call", 0x82EAF4C4, "7D6903A6", 31,
        (11, 31, 3, 4, 5, 6),
    ),
    Probe(
        "user_callback", 0x82EAE254, "7D6903A6", 3,
        (11, 3, 4, 5, 6, 31), False, True,
    ),
)


def stub_address(index: int) -> int:
    return STUB_BASE + index * STUB_STRIDE


def patch_for(index: int, probe: Probe) -> bytes:
    return insn(branch(probe.site, stub_address(index), False))


def build_stub(index: int, probe: Probe) -> bytes:
    address = stub_address(index)
    record = index * RECORD_SIZE
    words: list[int] = []

    # At request lookup r11 still points at the Blaze frame.  Everywhere else
    # the request object exposes component/command at +0x28/+0x2A.
    if probe.frame_lookup:
        words.extend((lhz(10, 11, 0x02), cmpwi(10, 9), 0))
        words.extend((lhz(10, 11, 0x04), cmpwi(10, 8), 0))
    else:
        words.extend(
            (lhz(10, probe.request_register, 0x28), cmpwi(10, 9), 0)
        )
        words.extend(
            (lhz(10, probe.request_register, 0x2A), cmpwi(10, 8), 0)
        )

    words.extend((
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3C00),  # JOURNAL = 0x83C8C400
        lwz(10, 12, record + 0x00),
        addi(10, 10, 1),
        stw(10, 12, record + 0x00),
    ))
    for slot, register in enumerate(probe.registers, start=1):
        words.append(stw(register, 12, record + slot * 4))
    words.append(stw(probe.request_register, 12, record + 0x1C))

    if probe.frame_lookup:
        # Do not dereference r3: a zero lookup result is exactly what this hook
        # must be able to report safely.
        for slot, offset in enumerate((0x00, 0x04, 0x08, 0x0C), start=8):
            words.extend((lwz(10, 11, offset), stw(10, 12, record + slot * 4)))
    else:
        for slot, offset in enumerate((0x28, 0x2C, 0x30, 0x38), start=8):
            words.extend(
                (
                    lwz(10, probe.request_register, offset),
                    stw(10, 12, record + slot * 4),
                )
            )
        if probe.snapshot_delegate:
            # This request wrapper invokes a game-owned delegate from
            # request+0x44. Preserve the bound target and both callable slots
            # while the request is alive; its storage is reused immediately.
            for slot, offset in enumerate((0x0C, 0x44, 0x48, 0x4C), start=12):
                words.extend(
                    (
                        lwz(10, probe.request_register, offset),
                        stw(10, 12, record + slot * 4),
                    )
                )

    finish = len(words)
    words.extend((
        int.from_bytes(probe.original, "big"),
        branch(address + (finish + 1) * 4, probe.site + 4, False),
    ))

    def at(word_index: int) -> int:
        return address + word_index * 4

    # Both comparisons use CR0; BO=4, BI=2 is bne.
    words[2] = conditional_branch(at(2), at(finish), 4, 2)
    words[5] = conditional_branch(at(5), at(finish), 4, 2)
    image = b"".join(insn(word) for word in words)
    if len(image) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot")
    return image.ljust(STUB_STRIDE, b"\0")


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, len(PROBES) * RECORD_SIZE)
    total = 0
    for index, probe in enumerate(PROBES):
        record = raw[index * RECORD_SIZE : (index + 1) * RECORD_SIZE]
        count = u32(record, 0x00)
        if not count:
            print(f"{probe.name:24} count=0")
            continue
        total += count
        values = [u32(record, 0x04 + slot * 4) for slot in range(6)]
        print(
            f"{probe.name:24} count={count} "
            + " ".join(
                f"r{register}=0x{value:08X}"
                for register, value in zip(probe.registers, values)
            )
        )
        request = u32(record, 0x1C)
        print(f"  request/frame = 0x{request:08X}")
        if probe.frame_lookup:
            words = [u32(record, 0x20 + slot * 4) for slot in range(4)]
            print("  frame words   = " + " ".join(f"{word:08X}" for word in words))
        else:
            route = u32(record, 0x20)
            print(
                f"  route={route >> 16}:{route & 0xFFFF} "
                f"error=0x{u32(record, 0x24):08X} "
                f"parsed=0x{u32(record, 0x28):08X} "
                f"supplied=0x{u32(record, 0x2C):08X}"
            )
            if probe.snapshot_delegate:
                print(
                    "  delegate: "
                    f"target=0x{u32(record, 0x30):08X} "
                    f"call=0x{u32(record, 0x34):08X} "
                    f"context=0x{u32(record, 0x38):08X} "
                    f"fallback=0x{u32(record, 0x3C):08X}"
                )
    if not total:
        print("No Util::postAuth dispatch stage was captured.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states: list[str] = []
        for index, probe in enumerate(PROBES):
            current = client.read(probe.site, 4)
            states.append(
                "original" if current == probe.original else
                "armed" if current == patch_for(index, probe) else
                f"unexpected:{current.hex().upper()}"
            )
        print(
            "PostAuth dispatch trace: "
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
                raise RuntimeError("At least one postAuth dispatch site is unexpected")
            write_chunks(client, JOURNAL, bytes(len(PROBES) * RECORD_SIZE))
            for index, probe in enumerate(PROBES):
                write_chunks(client, stub_address(index), build_stub(index, probe))
                client.write(probe.site, patch_for(index, probe))
            for index, probe in enumerate(PROBES):
                if client.read(probe.site, 4) != patch_for(index, probe):
                    raise RuntimeError(
                        f"Trace verification failed at 0x{probe.site:08X}"
                    )
            print("Verified: Util::postAuth dispatch path is journaled.")
            return 0

        for index, (probe, state) in enumerate(zip(PROBES, states)):
            if state == "armed":
                client.write(probe.site, probe.original)
            elif state != "original":
                raise RuntimeError(
                    f"Unexpected instruction at 0x{probe.site:08X}: {state}"
                )
        print("Verified: Util::postAuth dispatch trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
