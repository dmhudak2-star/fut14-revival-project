#!/usr/bin/env python3
"""Route local Redirector, PreAuth, Ping, and QoS replies in ProtoSSL."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from blaze_tdf import Field, INTEGER, encode_field, encode_frame
from fifa14_plain_recv_hook import (
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_PAYLOAD,
    PENDING_SOCKET,
    conditional_branch,
)
from fifa14_plain_send_hook import (
    Xbdm,
    add,
    addi,
    addis,
    andi_dot,
    branch,
    insn,
    lwz,
    rlwinm,
    stw,
    verify_module,
)
from fifa14_pending_response_pump_patch import (
    DELAY_CONTROL,
    DELAY_TICKS,
)


SITE = 0x82D8D4A0
ORIGINAL = bytes.fromhex("7C7C1B78")  # mr r28,r3
STUB = 0x83C8DC00
STUB_LIMIT = 0x83C8DE00
STATIC_PREAUTH = 0x83C8D000
STATIC_REDIRECTOR = 0x83C8D200
FOLLOWUP_FRAMES = 0x83C8D300
DEFAULT_RESPONSE = Path(__file__).with_name("fifa14_preauth_local_reply.bin")
DEFAULT_REDIRECTOR = Path(__file__).with_name(
    "fifa14_redirector_standard_insecure_host_reply.bin"
)

# Util/Ping must contain the server time. QoS command 20 has no response
# fields. By default each reply is published when its corresponding request is
# sent. The optional bundled mode publishes Ping and the following QoS response
# together, matching the successful 003253 session.
PING_PAYLOAD = encode_field(Field("STIM", INTEGER, int(time.time())))
PING_FRAME = encode_frame(9, 2, 0, 1, 0, PING_PAYLOAD)
QOS_FRAME = encode_frame(0x7802, 20, 0, 1, 0, b"")
FOLLOWUP_TEMPLATE = PING_FRAME + QOS_FRAME
PING_FRAME_LENGTH = len(PING_FRAME)
QOS_FRAME_OFFSET = PING_FRAME_LENGTH
QOS_FRAME_LENGTH = len(QOS_FRAME)
PING_QOS_BUNDLE_LENGTH = len(FOLLOWUP_TEMPLATE)
PING_TRANSACTION_OFFSET = 9
QOS_TRANSACTION_OFFSET = QOS_FRAME_OFFSET + 9


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def lbz(rt: int, ra: int, displacement: int) -> int:
    return 0x88000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stb(rs: int, ra: int, displacement: int) -> int:
    return 0x98000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def sth(rs: int, ra: int, displacement: int) -> int:
    return 0xB0000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def build_stub(
    preauth_length: int,
    redirector_length: int,
    delay_qos: bool = True,
    route_redirector: bool = True,
    route_qos: bool = True,
    bundle_qos_with_ping: bool = False,
) -> bytes:
    words = [
        lhz(10, 29, 0x02),          # outgoing component
        cmpwi(10, 5),
        0,                           # beq component_5
        cmpwi(10, 9),
        0,                           # beq component_9
        cmpwi(10, 0x7802),
        0,                           # bne finish
        lhz(10, 29, 0x04),
        cmpwi(10, 20),
        0,                           # bne finish
        addis(11, 0, 0x83C9),
        addi(11, 11, -0x2D00),      # FOLLOWUP_FRAMES
        lbz(10, 29, 0x09),
        stb(10, 11, QOS_TRANSACTION_OFFSET),
        lbz(10, 29, 0x0A),
        stb(10, 11, QOS_TRANSACTION_OFFSET + 1),
        lbz(10, 29, 0x0B),
        stb(10, 11, QOS_TRANSACTION_OFFSET + 2),
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # pending metadata
        lwz(10, 30, 0x00),
        stw(10, 12, 0x04),
        addi(11, 11, QOS_FRAME_OFFSET),
        stw(11, 12, 0x08),
        addi(10, 0, QOS_FRAME_LENGTH),
        0x7C0004AC,                 # sync before publishing pending length
        stw(10, 12, 0x00),
    ]
    if delay_qos:
        words.extend(
            [
                addis(12, 0, 0x83C9),
                addi(12, 12, -0x3180),  # DELAY_CONTROL
                addi(10, 0, DELAY_TICKS),
                stw(10, 12, 0x00),
                addi(10, 0, 0),
                stw(10, 12, 0x04),
            ]
        )
    words.append(0)                  # b finish
    qos_finish_branch = len(words) - 1
    component_5 = len(words)
    words.extend(
        [
            lhz(10, 29, 0x04),
            cmpwi(10, 1),
            0,                       # bne finish
            addis(11, 0, 0x83C9),
            addi(11, 11, -0x2E00),  # STATIC_REDIRECTOR
            lbz(10, 29, 0x09),
            stb(10, 11, 0x09),
            lbz(10, 29, 0x0A),
            stb(10, 11, 0x0A),
            lbz(10, 29, 0x0B),
            stb(10, 11, 0x0B),
            addis(12, 0, 0x83C9),
            addi(12, 12, -0x300),   # pending metadata
            lwz(10, 30, 0x00),
            stw(10, 12, 0x04),
            stw(11, 12, 0x08),
            addi(10, 0, redirector_length),
            0x7C0004AC,             # sync before publishing pending length
            stw(10, 12, 0x00),
            0,                       # b finish
        ]
    )
    redirector_finish_branch = len(words) - 1
    component_9 = len(words)
    words.extend(
        [
            lhz(10, 29, 0x04),
            cmpwi(10, 7),
            0,                       # beq component_9_preauth
            cmpwi(10, 2),
            0,                       # bne finish
            addis(11, 0, 0x83C9),
            addi(11, 11, -0x2D00),  # FOLLOWUP_FRAMES / Ping
            lbz(10, 29, 0x09),
            stb(10, 11, PING_TRANSACTION_OFFSET),
            lbz(10, 29, 0x0A),
            stb(10, 11, PING_TRANSACTION_OFFSET + 1),
            lbz(10, 29, 0x0B),
            stb(10, 11, PING_TRANSACTION_OFFSET + 2),
        ]
    )
    if bundle_qos_with_ping:
        # Blaze transaction numbers are 20-bit values. Increment the low
        # 16 bits, propagate their carry into the low nibble of byte 9, then
        # mask that nibble so 0xFFFFF wraps to zero. The bundled QoS header is
        # unaligned at FOLLOWUP_FRAMES+0x15, so write its txn byte-by-byte.
        words.extend(
            [
                lhz(10, 29, 0x0A),
                addi(10, 10, 1),
                stb(10, 11, QOS_TRANSACTION_OFFSET + 2),
                rlwinm(12, 10, 24, 8, 31),  # txn >> 8
                stb(12, 11, QOS_TRANSACTION_OFFSET + 1),
                rlwinm(10, 10, 16, 16, 31), # carry = txn >> 16
                lbz(12, 29, 0x09),
                add(10, 12, 10),
                andi_dot(10, 10, 0x0F),
                stb(10, 11, QOS_TRANSACTION_OFFSET),
            ]
        )
    words.extend(
        [
            addis(12, 0, 0x83C9),
            addi(12, 12, -0x300),   # pending metadata
            lwz(10, 30, 0x00),
            stw(10, 12, 0x04),
            stw(11, 12, 0x08),
            addi(
                10,
                0,
                PING_QOS_BUNDLE_LENGTH
                if bundle_qos_with_ping
                else PING_FRAME_LENGTH,
            ),
            0x7C0004AC,             # sync before publishing pending length
            stw(10, 12, 0x00),
            0,                       # b finish
        ]
    )
    ping_finish_branch = len(words) - 1
    component_9_preauth = len(words)
    words.extend(
        [
            addis(12, 0, 0x83C9),
            addi(12, 12, -0x300),   # pending metadata
            addis(11, 0, 0x83C9),
            addi(11, 11, -0x3000),  # STATIC_PREAUTH
            lbz(10, 29, 0x09),
            stb(10, 11, 0x09),
            lbz(10, 29, 0x0A),
            stb(10, 11, 0x0A),
            lbz(10, 29, 0x0B),
            stb(10, 11, 0x0B),
            lwz(10, 30, 0x00),
            stw(10, 12, 0x04),
            stw(11, 12, 0x08),
            addi(10, 0, preauth_length),
            0x7C0004AC,             # sync before publishing pending length
            stw(10, 12, 0x00),
        ]
    )
    finish = len(words)
    words.extend(
        [
            0x7C7C1B78,             # displaced mr r28,r3
            0,
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    words[2] = (
        conditional_branch(
            address(2), address(component_5), 12, 2
        )
        if route_redirector
        else conditional_branch(
            address(2), address(finish), 12, 2
        )
    )                                # beq
    words[4] = conditional_branch(
        address(4), address(component_9), 12, 2
    )                                # beq
    words[6] = (
        conditional_branch(
            address(6), address(finish), 4, 2
        )
        if route_qos
        else branch(address(6), address(finish), False)
    )                                # bne, or always defer QoS
    words[9] = conditional_branch(
        address(9), address(finish), 4, 2
    )                                # bne
    words[qos_finish_branch] = branch(
        address(qos_finish_branch), address(finish), False
    )
    words[component_5 + 2] = conditional_branch(
        address(component_5 + 2), address(finish), 4, 2
    )                                # bne
    words[redirector_finish_branch] = branch(
        address(redirector_finish_branch), address(finish), False
    )
    words[component_9 + 2] = conditional_branch(
        address(component_9 + 2), address(component_9_preauth), 12, 2
    )                                # beq
    words[component_9 + 4] = conditional_branch(
        address(component_9 + 4), address(finish), 4, 2
    )                                # bne
    words[ping_finish_branch] = branch(
        address(ping_finish_branch), address(finish), False
    )
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    parser.add_argument("--response", type=Path, default=DEFAULT_RESPONSE)
    parser.add_argument(
        "--redirector-response",
        type=Path,
        default=DEFAULT_REDIRECTOR,
    )
    parser.add_argument(
        "--defer-redirector",
        action="store_true",
        help="Let the Mac-side router answer Redirector after a short delay",
    )
    parser.add_argument(
        "--defer-qos",
        action="store_true",
        help="Let the Mac-side router answer QoS after listener registration",
    )
    parser.add_argument(
        "--bundle-qos-with-ping",
        action="store_true",
        help=(
            "Queue Ping plus the next-transaction QoS response as one "
            "33-byte pending receive"
        ),
    )
    args = parser.parse_args()

    preauth = args.response.read_bytes()
    redirector = args.redirector_response.read_bytes()
    preauth_capacity = STATIC_REDIRECTOR - STATIC_PREAUTH
    if not 12 <= len(preauth) <= preauth_capacity:
        raise RuntimeError("Invalid staged PreAuth response length")
    if not 12 <= len(redirector) <= 0x100:
        raise RuntimeError("Invalid staged Redirector response length")
    stub = build_stub(
        len(preauth),
        len(redirector),
        route_redirector=not args.defer_redirector,
        route_qos=not args.defer_qos,
        bundle_qos_with_ping=args.bundle_qos_with_ping,
    )
    raw_compatible_stubs = {
        build_stub(
            len(preauth),
            len(redirector),
            delay_qos=delay_qos,
            route_redirector=route_redirector,
            route_qos=route_qos,
            bundle_qos_with_ping=bundle_qos_with_ping,
        )
        for delay_qos in (False, True)
        for route_redirector in (False, True)
        for route_qos in (False, True)
        for bundle_qos_with_ping in (False, True)
    }
    cave_size = max(map(len, raw_compatible_stubs))
    if STUB + cave_size > STUB_LIMIT:
        raise RuntimeError("ProtoSSL flow stub exceeds its reserved code cave")
    compatible_stubs = {
        candidate.ljust(cave_size, b"\0")
        for candidate in raw_compatible_stubs
    }
    stub_image = stub.ljust(cave_size, b"\0")
    if args.bundle_qos_with_ping and PING_QOS_BUNDLE_LENGTH != 33:
        raise RuntimeError("Ping/QoS template is not the expected 33 bytes")
    patch = insn(branch(SITE, STUB, False))

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = client.read(SITE, 4)
        state = (
            "original"
            if current == ORIGINAL
            else "patched"
            if current == patch
            else f"unexpected:{current.hex().upper()}"
        )
        print(f"ProtoSSL flow site: {state}")
        if args.action == "status":
            pending = int.from_bytes(client.read(PENDING_LENGTH, 4), "big")
            print(f"Pending response: {pending} bytes")
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Refusing to overwrite unexpected code")
            cave = client.read(STUB, cave_size)
            allowed_caves = compatible_stubs | (
                {bytes(cave_size)} if state == "original" else set()
            )
            if cave not in allowed_caves:
                raise RuntimeError("ProtoSSL flow code cave is not free")
            if state == "patched":
                # Prevent a title thread from entering the cave while it is
                # upgraded in-place or while its response data is replaced.
                client.write(SITE, ORIGINAL)
                time.sleep(0.02)
            try:
                client.write(PENDING_LENGTH, bytes(4))
                client.write(DELAY_CONTROL, bytes(8))
                write_chunks(client, STATIC_PREAUTH, preauth)
                write_chunks(client, STATIC_REDIRECTOR, redirector)
                client.write(
                    PENDING_CURSOR,
                    PENDING_PAYLOAD.to_bytes(4, "big"),
                )
                client.write(PENDING_SOCKET, bytes(4))
                client.write(FOLLOWUP_FRAMES, FOLLOWUP_TEMPLATE)
                write_chunks(client, STUB, stub_image)
                if (
                    client.read(STATIC_PREAUTH, len(preauth)) != preauth
                    or client.read(STATIC_REDIRECTOR, len(redirector))
                    != redirector
                    or client.read(FOLLOWUP_FRAMES, len(FOLLOWUP_TEMPLATE))
                    != FOLLOWUP_TEMPLATE
                    or client.read(STUB, cave_size) != stub_image
                ):
                    raise RuntimeError("ProtoSSL flow data verification failed")
                client.write(SITE, patch)
            except Exception:
                client.write(SITE, ORIGINAL)
                raise
            if client.read(SITE, 4) != patch:
                raise RuntimeError("ProtoSSL flow patch verification failed")
            routes = [
                "PreAuth",
                (
                    f"Ping+QoS bundle ({PING_QOS_BUNDLE_LENGTH} bytes)"
                    if args.bundle_qos_with_ping
                    else "Ping"
                ),
            ]
            routes.append(
                "Redirector deferred"
                if args.defer_redirector
                else "Redirector"
            )
            routes.append(
                (
                    "standalone QoS deferred"
                    if args.defer_qos
                    else "standalone QoS"
                )
                if args.bundle_qos_with_ping
                else ("QoS deferred" if args.defer_qos else "QoS")
            )
            print("Verified: " + " + ".join(routes) + ".")
            return 0
        if state == "original":
            print("Already restored.")
            return 0
        if state != "patched":
            raise RuntimeError("Refusing to restore unexpected code")
        client.write(SITE, ORIGINAL)
        client.write(PENDING_LENGTH, bytes(4))
        if client.read(SITE, 4) != ORIGINAL:
            raise RuntimeError("ProtoSSL flow restore failed")
        print("Verified: original ProtoSSL post-send instruction restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
