#!/usr/bin/env python3
"""Relay QoS completion to Blaze when FIFA installed no signal listener."""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import cmpwi, conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    cmpw,
    insn,
    lwz,
    stw,
    verify_module,
)
from fifa14_qos_signal_trace_v2 import (
    SITE,
    ORIGINAL,
    STUB as TRACE_STUB,
    build_stub as build_trace_stub,
)


STUB = 0x83C8C700
JOURNAL = 0x83C8C800
JOURNAL_SIZE = 0x40
CONNECTION_RESULT = 0x82F02D90


def build_stub() -> bytes:
    words = [
        addi(9, 3, 0x0A8C),         # signal = manager + 0xA8C
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3800),      # JOURNAL
        lwz(11, 12, 0x00),
        addi(11, 11, 1),
        stw(11, 12, 0x00),          # invocation count
        stw(3, 12, 0x04),           # manager
        stw(9, 12, 0x08),           # signal
        lwz(10, 9, 0x04),           # subscriber begin
        lwz(11, 9, 0x08),           # subscriber end
        stw(10, 12, 0x0C),
        stw(11, 12, 0x10),
        lwz(9, 9, 0x40),
        stw(9, 12, 0x14),           # dispatch depth
        cmpw(10, 11),
        0,                           # beq no_subscriber
        lwz(10, 10, 0x00),
        stw(10, 12, 0x18),          # first listener
        cmpwi(10, 0),
        0,                           # beq original_path
    ]
    for index, offset in enumerate((0x00, 0x04, 0x08, 0x0C)):
        words.append(lwz(11, 10, offset))
        words.append(stw(11, 12, 0x1C + index * 4))

    original_path = len(words)
    words.extend(
        [
            int.from_bytes(ORIGINAL, "big"),
            0,                       # b SITE+4
        ]
    )

    no_subscriber = len(words)
    words.extend(
        [
            lwz(10, 12, 0x34),      # already relayed this arming?
            cmpwi(10, 0),
            0,                       # bne original_path
            addi(10, 10, 1),
            stw(10, 12, 0x34),      # relay count
            0x7D8802A6,             # mflr r12
            0x9181FFF8,             # stw r12,-8(r1)
            0xFBE1FFF0,             # std r31,-0x10(r1)
            0x9421FFA0,             # stwu r1,-0x60(r1)
            0x7C7F1B78,             # mr r31,r3
            addi(4, 0, 0),          # successful connection result
            0,                       # bl CONNECTION_RESULT
            addi(1, 1, 0x60),
            0x8181FFF8,             # lwz r12,-8(r1)
            0x7D8803A6,             # mtlr r12
            0xEBE1FFF0,             # ld r31,-0x10(r1)
            0x4E800020,             # blr
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    words[15] = conditional_branch(
        address(15), address(no_subscriber), 12, 2
    )                                # beq
    words[19] = conditional_branch(
        address(19), address(original_path), 12, 2
    )                                # beq
    words[original_path + 1] = branch(
        address(original_path + 1), SITE + 4, False
    )
    words[no_subscriber + 2] = conditional_branch(
        address(no_subscriber + 2), address(original_path), 4, 2
    )                                # bne
    words[no_subscriber + 11] = branch(
        address(no_subscriber + 11), CONNECTION_RESULT, True
    )
    return b"".join(insn(word) for word in words)


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    begin = u32(raw, 0x0C)
    end = u32(raw, 0x10)
    print(f"invocation_count = {u32(raw, 0x00)}")
    print(f"manager          = 0x{u32(raw, 0x04):08X}")
    print(f"signal           = 0x{u32(raw, 0x08):08X}")
    print(f"begin            = 0x{begin:08X}")
    print(f"end              = 0x{end:08X}")
    print(
        f"subscriber_count = {(end - begin) // 4 if end >= begin else -1}"
    )
    print(f"dispatch_depth   = {u32(raw, 0x14)}")
    print(f"listener         = 0x{u32(raw, 0x18):08X}")
    print(f"relay_count      = {u32(raw, 0x34)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    stub = build_stub()
    trace_patch = insn(branch(SITE, TRACE_STUB, False))
    relay_patch = insn(branch(SITE, STUB, False))
    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = client.read(SITE, 4)
        state = (
            "original"
            if current == ORIGINAL
            else "trace"
            if current == trace_patch
            else "relay"
            if current == relay_patch
            else f"unexpected:{current.hex().upper()}"
        )
        print(f"QoS completion relay: {state}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "trace", "relay"):
                raise RuntimeError("Unexpected QoS signal entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("QoS relay code cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            if state != "original":
                client.write(SITE, ORIGINAL)
            try:
                write_chunks(client, STUB, stub)
                client.write(SITE, relay_patch)
            except Exception:
                client.write(SITE, ORIGINAL)
                raise
            if client.read(SITE, 4) != relay_patch:
                raise RuntimeError("QoS relay verification failed")
            print(
                "Verified: missing QoS listener relays one successful "
                "connection result."
            )
            return 0
        if state in ("trace", "relay"):
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected QoS signal entry")
        print("Verified: original QoS signal entry restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
