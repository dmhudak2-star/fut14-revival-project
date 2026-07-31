#!/usr/bin/env python3
"""Redirect only FIFA 14's Blaze TCP connect to the local revival server."""

from __future__ import annotations

import argparse
import ipaddress

from fifa14_connect_bypass import (
    CONNECT_CALLSITE,
    CONNECT_LOG,
    CONNECT_STUB,
    ORIGINAL_CONNECT_CALL,
)
from fifa14_connect_journal import (
    CONNECT_STUB_BYTES as JOURNAL_STUB_BYTES,
    PATCHED_CONNECT_CALL,
)
from fifa14_plain_recv_hook import conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    insn,
    lwz,
    stw,
    verify_module,
)


CONNECT_WRAPPER = 0x824CA450
DEFAULT_LOCAL_IP = "192.0.2.35"
BLAZE_PORT = 10041
REDIRECTOR_PORTS = (42126, 42127)


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def cmplwi(ra: int, immediate: int) -> int:
    return 0x28000000 | (ra << 16) | (immediate & 0xFFFF)


def build_stub(local_ip: int, *, legacy_global_lr: bool = False) -> bytes:
    local_ip_high = ((local_ip + 0x8000) >> 16) & 0xFFFF
    local_ip_low = local_ip & 0xFFFF
    words = [
        lhz(10, 4, 2),               # sockaddr.sin_port
        cmplwi(10, BLAZE_PORT),
        0,                            # beq redirect
        cmplwi(10, REDIRECTOR_PORTS[0]),
        0,                            # beq redirect
        cmplwi(10, REDIRECTOR_PORTS[1]),
        0,                            # bne direct_call
    ]
    redirect = len(words)
    words.extend(
        [
        addis(10, 0, local_ip_high),
        addi(10, 10, local_ip_low),
        stw(10, 4, 4),                # sockaddr.sin_addr = Mac
        ]
    )
    words.extend(
        [
            addis(12, 0, 0x83C9),
            addi(12, 12, -0x1900),    # -> 0x83C8E700
            lwz(11, 12, 0),
            addi(11, 11, 1),
            stw(11, 12, 0x00),
            stw(3, 12, 0x04),
            stw(5, 12, 0x08),
            stw(31, 12, 0x20),       # owning DirtySock socket object
        ]
    )
    for offset in range(0, 0x10, 4):
        words.append(lwz(10, 4, offset))
        words.append(stw(10, 12, 0x10 + offset))
    high = (CONNECT_WRAPPER + 0x8000) >> 16
    if legacy_global_lr:
        # Historical image retained only so a live old hook can be migrated.
        # It is unsafe for concurrent connects because all threads share the
        # same saved LR word at CONNECT_LOG+0x24.
        words.extend(
            [
                0x7C0802A6,           # mflr r0
                stw(0, 12, 0x24),     # legacy global LR save
                addis(11, 0, high),
                addi(11, 11, CONNECT_WRAPPER & 0xFFFF),
                0x7D6903A6,           # mtctr r11
                0x4E800421,           # bctrl
                addis(12, 0, 0x83C9),
                addi(12, 12, -0x1900),
                stw(3, 12, 0x0C),     # raw NetDll_connect return
                lwz(0, 12, 0x24),
                0x7C0803A6,           # mtlr r0
                0x4E800020,           # blr
            ]
        )
    else:
        # The callsite LR is thread-local state.  Keep it in a conventional
        # stack frame across bctrl; using CONNECT_LOG for this caused one
        # connect thread to restore another thread's return address.
        words.extend(
            [
                0x7C0802A6,           # mflr r0
                stw(0, 1, -0x08),     # save at incoming_sp-8
                0x9421FF90,           # stwu r1,-0x70(r1)
                addis(11, 0, high),
                addi(11, 11, CONNECT_WRAPPER & 0xFFFF),
                0x7D6903A6,           # mtctr r11
                0x4E800421,           # bctrl
                addis(12, 0, 0x83C9),
                addi(12, 12, -0x1900),
                stw(3, 12, 0x0C),     # raw NetDll_connect return
                addi(1, 1, 0x70),
                lwz(0, 1, -0x08),
                0x7C0803A6,           # mtlr r0
                0x4E800020,           # blr
            ]
        )
    direct_call = len(words)
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, CONNECT_WRAPPER & 0xFFFF),
            0x7D6903A6,               # mtctr r11
            0x4E800420,               # bctr; keep callsite LR
        ]
    )
    for index in (2, 4):
        words[index] = conditional_branch(
            CONNECT_STUB + index * 4,
            CONNECT_STUB + redirect * 4,
            12,
            2,
        )                             # beq
    words[6] = conditional_branch(
        CONNECT_STUB + 6 * 4,
        CONNECT_STUB + direct_call * 4,
        4,
        2,
    )                                 # bne
    return b"".join(insn(word) for word in words)


CONNECT_STUB_BYTES = build_stub(
    int(ipaddress.IPv4Address(DEFAULT_LOCAL_IP))
)
LEGACY_CONNECT_STUB_BYTES = build_stub(
    int(ipaddress.IPv4Address(DEFAULT_LOCAL_IP)),
    legacy_global_lr=True,
)


def state(
    client: Xbdm,
    expected_stub: bytes,
    expected_legacy_stub: bytes,
) -> str:
    call = client.read(CONNECT_CALLSITE, 4)
    if call == ORIGINAL_CONNECT_CALL:
        return "original"
    if call != PATCHED_CONNECT_CALL:
        return f"unexpected-call:{call.hex().upper()}"
    stub = client.read(CONNECT_STUB, len(expected_stub))
    if stub == expected_stub:
        return "redirected"
    if stub.startswith(expected_legacy_stub):
        return "legacy-redirected"
    if stub == CONNECT_STUB_BYTES:
        return "redirected-other"
    if stub.startswith(LEGACY_CONNECT_STUB_BYTES):
        return "legacy-redirected-other"
    if stub[: len(JOURNAL_STUB_BYTES)] == JOURNAL_STUB_BYTES:
        return "journaled"
    return "unexpected-stub"


def signed(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


def print_log(client: Xbdm) -> None:
    record = client.read(CONNECT_LOG, 0x28)
    count = int.from_bytes(record[0:4], "big")
    raw_result = int.from_bytes(record[0x0C:0x10], "big")
    sockaddr = record[0x10:0x20]
    port = int.from_bytes(sockaddr[2:4], "big")
    ip = ".".join(str(octet) for octet in sockaddr[4:8])
    owner = int.from_bytes(record[0x20:0x24], "big")
    mapped = None
    if owner:
        try:
            mapped = int.from_bytes(client.read(owner + 0x1C, 4), "big")
        except Exception:
            pass
    suffix = (
        f" mapped={signed(mapped)} (0x{mapped:08X})"
        if mapped is not None
        else ""
    )
    print(
        f"Connect calls: {count}; last target={ip}:{port} "
        f"raw={signed(raw_result)} (0x{raw_result:08X}){suffix}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    parser.add_argument("--local-ip", default=DEFAULT_LOCAL_IP)
    args = parser.parse_args()
    local_ip = str(ipaddress.IPv4Address(args.local_ip))
    local_ip_value = int(ipaddress.IPv4Address(local_ip))
    connect_stub_bytes = build_stub(local_ip_value)
    legacy_connect_stub_bytes = build_stub(
        local_ip_value,
        legacy_global_lr=True,
    )

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = state(
            client,
            connect_stub_bytes,
            legacy_connect_stub_bytes,
        )
        print(f"DirtySock connect callsite: {current}")
        if args.action == "status":
            if current in (
                "redirected",
                "legacy-redirected",
                "redirected-other",
                "legacy-redirected-other",
            ):
                print_log(client)
            return 0

        if args.action == "apply":
            if current == "redirected":
                print_log(client)
                return 0
            if current not in (
                "original",
                "journaled",
                "legacy-redirected",
                "redirected-other",
                "legacy-redirected-other",
            ):
                raise RuntimeError("Refusing to replace an unknown connect hook")
            # Unpublish the old stub before replacing its code.
            if current != "original":
                client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
            client.write(CONNECT_LOG, bytes(0x28))
            client.write(CONNECT_STUB, connect_stub_bytes)
            if (
                client.read(CONNECT_STUB, len(connect_stub_bytes))
                != connect_stub_bytes
            ):
                raise RuntimeError("Connect redirect stub verification failed")
            client.write(CONNECT_CALLSITE, PATCHED_CONNECT_CALL)
            if state(
                client,
                connect_stub_bytes,
                legacy_connect_stub_bytes,
            ) != "redirected":
                client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
                raise RuntimeError("Connect redirect publication failed")
            print(
                "Verified: Blaze ports 10041/42126/42127 redirect to "
                f"{local_ip}."
            )
            return 0

        if current == "original":
            print("Already restored.")
            return 0
        if current not in (
            "redirected",
            "legacy-redirected",
            "redirected-other",
            "legacy-redirected-other",
        ):
            raise RuntimeError("Refusing to restore an unknown connect hook")
        client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
        if state(
            client,
            connect_stub_bytes,
            legacy_connect_stub_bytes,
        ) != "original":
            raise RuntimeError("Connect redirect restore failed")
        print("Verified: original DirtySock connect call restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
