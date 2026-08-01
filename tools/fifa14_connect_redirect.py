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
XAM_NETDLL_WSA_GET_LAST_ERROR = 0x8173FE70
XAM_NETDLL_WSA_SET_LAST_ERROR = 0x8173FE78
DIRTYSOCK_CONTROL = 0x82D6A370
SOCKET_SECURITY_STUB = 0x83C8E780
CONNECT_RESULT_STUB = 0x83C8E880
DEFAULT_LOCAL_IP = "192.0.2.35"
BLAZE_PORT = 10041
REDIRECTOR_PORTS = (42124, 42126, 42127)
IDENTITY_HTTP_PORT = 18080
LOCAL_PLAINTEXT_PORTS = (BLAZE_PORT, *REDIRECTOR_PORTS, IDENTITY_HTTP_PORT)
XINS_TAG_HIGH = 0x7869
XINS_TAG_LOW = 0x6E73


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def lbz(rt: int, ra: int, displacement: int) -> int:
    return 0x88000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def cmplwi(ra: int, immediate: int) -> int:
    return 0x28000000 | (ra << 16) | (immediate & 0xFFFF)


def ori(ra: int, rs: int, immediate: int) -> int:
    return 0x60000000 | (rs << 21) | (ra << 16) | (immediate & 0xFFFF)


def build_security_stub() -> bytes:
    """Enable ``xins`` through FIFA's native DirtySock control routine.

    The helper receives the owning DirtySock socket object in r3. Calling
    SocketControl(owner, 'xins', 1, 0, 0) follows the exact retail path: it
    configures owner->socket and publishes the per-socket flag at owner+0x42.
    """
    control_high = (DIRTYSOCK_CONTROL + 0x8000) >> 16
    get_error_high = (XAM_NETDLL_WSA_GET_LAST_ERROR + 0x8000) >> 16
    set_error_high = (XAM_NETDLL_WSA_SET_LAST_ERROR + 0x8000) >> 16
    words = [
        0x7C0802A6,                 # mflr r0
        stw(0, 1, -0x08),           # save at incoming_sp-8
        0x9421FF90,                 # stwu r1,-0x70(r1)
        stw(3, 1, 0x20),            # owning DirtySock socket object
        addi(3, 0, 0),              # clear stale thread-local WSA error
        addis(11, 0, set_error_high),
        addi(11, 11, XAM_NETDLL_WSA_SET_LAST_ERROR & 0xFFFF),
        0x7D6903A6,                 # mtctr r11
        0x4E800421,                 # bctrl
        lwz(3, 1, 0x20),            # owner
        addis(4, 0, XINS_TAG_HIGH),
        ori(4, 4, XINS_TAG_LOW),     # r4 = 'xins'
        addi(5, 0, 1),              # enable
        addi(6, 0, 0),
        addi(7, 0, 0),
        addis(11, 0, control_high),
        addi(11, 11, DIRTYSOCK_CONTROL & 0xFFFF),
        0x7D6903A6,                 # mtctr r11
        0x4E800421,                 # bctrl
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),      # CONNECT_LOG
        stw(3, 12, 0x28),           # SocketControl result
        lwz(9, 1, 0x20),
        lbz(10, 9, 0x42),
        stw(10, 12, 0x2C),          # owner->xins flag
        addis(11, 0, get_error_high),
        addi(11, 11, XAM_NETDLL_WSA_GET_LAST_ERROR & 0xFFFF),
        0x7D6903A6,                 # mtctr r11
        0x4E800421,                 # bctrl
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),
        stw(3, 12, 0x30),           # WSAGetLastError after xins
        addi(1, 1, 0x70),
        lwz(0, 1, -0x08),
        0x7C0803A6,                 # mtlr r0
        0x4E800020,                 # blr
    ]
    return b"".join(insn(word) for word in words)


SOCKET_SECURITY_STUB_BYTES = build_security_stub()


def build_connect_result_stub() -> bytes:
    """Journal connect's raw return and same-thread WSA error, preserving r3."""
    error_high = (XAM_NETDLL_WSA_GET_LAST_ERROR + 0x8000) >> 16
    words = [
        0x7C0802A6,                 # mflr r0
        stw(0, 1, -0x08),
        0x9421FF90,                 # stwu r1,-0x70(r1)
        stw(3, 1, 0x20),            # raw connect result
        addis(11, 0, error_high),
        addi(11, 11, XAM_NETDLL_WSA_GET_LAST_ERROR & 0xFFFF),
        0x7D6903A6,                 # mtctr r11
        0x4E800421,                 # bctrl
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),
        stw(3, 12, 0x38),           # connect WSAGetLastError
        lwz(3, 1, 0x20),
        stw(3, 12, 0x0C),           # raw NetDll_connect return
        addi(1, 1, 0x70),
        lwz(0, 1, -0x08),
        0x7C0803A6,                 # mtlr r0
        0x4E800020,                 # blr
    ]
    return b"".join(insn(word) for word in words)


CONNECT_RESULT_STUB_BYTES = build_connect_result_stub()


def build_stub(
    local_ip: int,
    *,
    legacy_global_lr: bool = False,
    unsecure_socket: bool = True,
) -> bytes:
    local_ip_high = ((local_ip + 0x8000) >> 16) & 0xFFFF
    local_ip_low = local_ip & 0xFFFF
    words = [lhz(10, 4, 2)]          # sockaddr.sin_port
    port_branch_indices: list[int] = []
    for port in LOCAL_PLAINTEXT_PORTS:
        words.append(cmplwi(10, port))
        port_branch_indices.append(len(words))
        words.append(0)               # beq redirect; final one is bne direct
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
        if not unsecure_socket:
            # Exact pre-plaintext image retained so a live hook from the
            # previous revision can be identified and migrated safely.
            words.extend(
                [
                    0x7C0802A6,       # mflr r0
                    stw(0, 1, -0x08),
                    0x9421FF90,       # stwu r1,-0x70(r1)
                    addis(11, 0, high),
                    addi(11, 11, CONNECT_WRAPPER & 0xFFFF),
                    0x7D6903A6,       # mtctr r11
                    0x4E800421,       # bctrl
                addis(11, 0, (CONNECT_RESULT_STUB + 0x8000) >> 16),
                addi(11, 11, CONNECT_RESULT_STUB & 0xFFFF),
                0x7D6903A6,           # mtctr r11
                0x4E800421,           # bctrl
                    addi(1, 1, 0x70),
                    lwz(0, 1, -0x08),
                    0x7C0803A6,       # mtlr r0
                    0x4E800020,       # blr
                ]
            )
        else:
            words.extend(
                [
                    0x7C0802A6,       # mflr r0
                    stw(0, 1, -0x08),
                    0x9421FF70,       # stwu r1,-0x90(r1)
                    stw(3, 1, 0x20),
                    stw(4, 1, 0x24),
                    stw(5, 1, 0x28),
                    0x7FE3FB78,       # mr r3,r31: owning DirtySock socket
                ]
            )
            helper_high = (SOCKET_SECURITY_STUB + 0x8000) >> 16
            words.extend(
                [
                    addis(11, 0, helper_high),
                    addi(11, 11, SOCKET_SECURITY_STUB & 0xFFFF),
                    0x7D6903A6,       # mtctr r11
                    0x4E800421,       # bctrl
                ]
            )
            words.extend(
                [
                    lwz(3, 1, 0x20),
                    lwz(4, 1, 0x24),
                    lwz(5, 1, 0x28),
                    addis(11, 0, high),
                    addi(11, 11, CONNECT_WRAPPER & 0xFFFF),
                    0x7D6903A6,       # mtctr r11
                    0x4E800421,       # bctrl
                    addis(12, 0, 0x83C9),
                    addi(12, 12, -0x1900),
                    stw(3, 12, 0x0C),
                    addi(1, 1, 0x90),
                    lwz(0, 1, -0x08),
                    0x7C0803A6,       # mtlr r0
                    0x4E800020,       # blr
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
    for index in port_branch_indices[:-1]:
        words[index] = conditional_branch(
            CONNECT_STUB + index * 4,
            CONNECT_STUB + redirect * 4,
            12,
            2,
        )                             # beq
    final_port_branch = port_branch_indices[-1]
    words[final_port_branch] = conditional_branch(
        CONNECT_STUB + final_port_branch * 4,
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
    unsecure_socket=False,
)
SECURE_CONNECT_STUB_BYTES = build_stub(
    int(ipaddress.IPv4Address(DEFAULT_LOCAL_IP)),
    unsecure_socket=False,
)


def state(
    client: Xbdm,
    expected_stub: bytes,
    expected_legacy_stub: bytes,
    expected_secure_stub: bytes,
) -> str:
    call = client.read(CONNECT_CALLSITE, 4)
    if call == ORIGINAL_CONNECT_CALL:
        return "original"
    if call != PATCHED_CONNECT_CALL:
        return f"unexpected-call:{call.hex().upper()}"
    stub = client.read(CONNECT_STUB, len(expected_stub))
    if stub == expected_stub:
        helper = client.read(
            SOCKET_SECURITY_STUB,
            len(SOCKET_SECURITY_STUB_BYTES),
        )
        result_helper = client.read(
            CONNECT_RESULT_STUB,
            len(CONNECT_RESULT_STUB_BYTES),
        )
        return (
            "redirected"
            if helper == SOCKET_SECURITY_STUB_BYTES
            and result_helper == CONNECT_RESULT_STUB_BYTES
            else "helper-mismatch"
        )
    if stub.startswith(expected_secure_stub):
        return "secure-redirected"
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
    record = client.read(CONNECT_LOG, 0x3C)
    count = int.from_bytes(record[0:4], "big")
    handle = int.from_bytes(record[0x04:0x08], "big")
    raw_result = int.from_bytes(record[0x0C:0x10], "big")
    sockaddr = record[0x10:0x20]
    port = int.from_bytes(sockaddr[2:4], "big")
    ip = ".".join(str(octet) for octet in sockaddr[4:8])
    owner = int.from_bytes(record[0x20:0x24], "big")
    xins_result = signed(int.from_bytes(record[0x28:0x2C], "big"))
    xins_flag = int.from_bytes(record[0x2C:0x30], "big")
    xins_error = int.from_bytes(record[0x30:0x34], "big")
    connect_error = int.from_bytes(record[0x38:0x3C], "big")
    mapped = None
    insecure_flag = None
    if owner:
        try:
            mapped = int.from_bytes(client.read(owner + 0x1C, 4), "big")
            insecure_flag = client.read(owner + 0x42, 1)[0]
        except Exception:
            pass
    suffix = (
        f" owner=0x{owner:08X} mapped={signed(mapped)} (0x{mapped:08X})"
        f" xins={insecure_flag}"
        if mapped is not None
        else ""
    )
    print(
        f"Connect calls: {count}; socket=0x{handle:08X}; last target={ip}:{port} "
        f"raw={signed(raw_result)} (0x{raw_result:08X})/WSA:{connect_error}{suffix}; "
        f"native-xins=result:{xins_result}/flag:{xins_flag}/WSA:{xins_error}"
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
        unsecure_socket=False,
    )
    secure_connect_stub_bytes = build_stub(
        local_ip_value,
        unsecure_socket=False,
    )

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = state(
            client,
            connect_stub_bytes,
            legacy_connect_stub_bytes,
            secure_connect_stub_bytes,
        )
        print(f"DirtySock connect callsite: {current}")
        if args.action == "status":
            if current in (
                "redirected",
                "legacy-redirected",
                "redirected-other",
                "legacy-redirected-other",
                "secure-redirected",
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
                "secure-redirected",
            ):
                raise RuntimeError("Refusing to replace an unknown connect hook")
            # Unpublish the old stub before replacing its code.
            if current != "original":
                client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
            client.write(CONNECT_LOG, bytes(0x3C))
            client.write(SOCKET_SECURITY_STUB, SOCKET_SECURITY_STUB_BYTES)
            if (
                client.read(SOCKET_SECURITY_STUB, len(SOCKET_SECURITY_STUB_BYTES))
                != SOCKET_SECURITY_STUB_BYTES
            ):
                raise RuntimeError("Socket security helper verification failed")
            client.write(CONNECT_RESULT_STUB, CONNECT_RESULT_STUB_BYTES)
            if (
                client.read(CONNECT_RESULT_STUB, len(CONNECT_RESULT_STUB_BYTES))
                != CONNECT_RESULT_STUB_BYTES
            ):
                raise RuntimeError("Connect result helper verification failed")
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
                secure_connect_stub_bytes,
            ) != "redirected":
                client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
                raise RuntimeError("Connect redirect publication failed")
            print(
                "Verified: Blaze ports 10041/42124/42126/42127 redirect to "
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
            "secure-redirected",
        ):
            raise RuntimeError("Refusing to restore an unknown connect hook")
        client.write(CONNECT_CALLSITE, ORIGINAL_CONNECT_CALL)
        if state(
            client,
            connect_stub_bytes,
            legacy_connect_stub_bytes,
            secure_connect_stub_bytes,
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
