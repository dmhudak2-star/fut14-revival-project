#!/usr/bin/env python3
"""Capture the PowerPC call stack when Redirector.getServerInstance is sent.

The hook is entirely breakpoint-free.  It diverts the displaced ``mflr r12``
at the known DirtySock send entry to a small code cave, records one matching
Blaze request, replays that instruction, and returns to the next instruction.
Only volatile GPRs are used and neither LR nor SP is modified.
"""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import cmpwi, conditional_branch
from fifa14_plain_recv_log_hook import (
    LOG_STUB_BYTES as RECV_LOG_STUB_BYTES,
    ORIGINAL_RESULT_INSTRUCTION as RECV_LOG_ORIGINAL,
    PATCHED_RESULT_INSTRUCTION as RECV_LOG_PATCH,
    RESULT_SITE as RECV_LOG_SITE,
)
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
from fifa14_qos_send_stack_trace import (
    JOURNAL_SIZE as QOS_JOURNAL_SIZE,
    build_stub as build_qos_stub,
)


SITE = 0x82D69FF8
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x83C8DE00
JOURNAL = 0x83C8E000
FRAME_COUNT = 16
JOURNAL_SIZE = 8 + FRAME_COUNT * 8

HEADER_SIZE = 12
REDIRECTOR_COMPONENT = 5
REDIRECTOR_COMMAND = 1


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_stub() -> bytes:
    """Build the Redirector-filtered stack-capture stub."""
    words = [
        cmpwi(5, HEADER_SIZE),
        0,                           # blt fallback
        lhz(11, 4, 0x02),           # Blaze component
        cmpwi(11, REDIRECTOR_COMPONENT),
        0,                           # bne fallback
        lhz(11, 4, 0x04),           # Blaze command
        cmpwi(11, REDIRECTOR_COMMAND),
        0,                           # bne fallback
        0x7C0802A6,                 # mflr r0 (volatile capture register)
        addis(12, 0, (JOURNAL + 0x8000) >> 16),
        addi(12, 12, JOURNAL & 0xFFFF),
        stw(0, 12, 0x00),           # entry LR
        stw(1, 12, 0x04),           # entry SP
        lwz(11, 1, 0x00),           # caller's pre-frame stack pointer
    ]
    for index in range(FRAME_COUNT):
        words.append(stw(11, 12, 0x08 + index * 8))
        words.append(lwz(10, 11, -8))
        words.append(stw(10, 12, 0x0C + index * 8))
        if index + 1 < FRAME_COUNT:
            words.append(lwz(11, 11, 0))

    fallback = len(words)
    words.extend(
        [
            int.from_bytes(ORIGINAL, "big"),  # displaced mflr r12
            0,                                 # b SITE + 4
        ]
    )

    def address(index: int) -> int:
        return STUB + index * 4

    words[1] = conditional_branch(
        address(1), address(fallback), 12, 0
    )                               # blt
    for index in (4, 7):
        words[index] = conditional_branch(
            address(index), address(fallback), 4, 2
        )                           # bne
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)
    return b"".join(insn(word) for word in words)


STUB_BYTES = build_stub()
QOS_STUB_BYTES = build_qos_stub()
CAVE_SIZE = max(len(STUB_BYTES), len(QOS_STUB_BYTES))
STUB_IMAGE = STUB_BYTES.ljust(CAVE_SIZE, b"\0")
QOS_STUB_IMAGE = QOS_STUB_BYTES.ljust(CAVE_SIZE, b"\0")
EMPTY_CAVE = bytes(CAVE_SIZE)
PATCH = insn(branch(SITE, STUB, False))
JOURNAL_CLEAR_SIZE = max(JOURNAL_SIZE, QOS_JOURNAL_SIZE)


def classify_cave(cave: bytes) -> str:
    if len(cave) != CAVE_SIZE:
        raise ValueError(
            f"Expected a 0x{CAVE_SIZE:X}-byte cave image, got 0x{len(cave):X}"
        )
    if cave == EMPTY_CAVE:
        return "empty"
    if cave == STUB_IMAGE:
        return "redirector"
    if cave == QOS_STUB_IMAGE:
        return "qos"
    return f"unexpected:{cave[:16].hex().upper()}"


def describe_state(current: bytes, cave_kind: str) -> str:
    if current == ORIGINAL:
        if cave_kind == "qos":
            return "original (restored QoS cave is migratable)"
        return f"original (cave: {cave_kind})"
    if current == PATCH:
        if cave_kind == "redirector":
            return "armed"
        if cave_kind == "qos":
            return "QoS probe active"
        return f"shared patch active with {cave_kind} cave"
    return f"unexpected site:{current.hex().upper()} (cave: {cave_kind})"


def describe_recv_log_state(current: bytes) -> str:
    if current == RECV_LOG_ORIGINAL:
        return "inactive"
    if current == RECV_LOG_PATCH:
        return "ACTIVE (its stub overlaps this probe's journal)"
    return f"unexpected:{current.hex().upper()}"


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    if len(raw) != JOURNAL_SIZE:
        raise RuntimeError("Short Redirector stack journal read")
    if raw.startswith(RECV_LOG_STUB_BYTES):
        raise RuntimeError(
            "0x83C8E000 still contains the direct-recv logger stub, not a "
            "Redirector stack journal"
        )
    if not any(raw):
        print("No Redirector stack capture has been recorded.")
        return

    lr = int.from_bytes(raw[0:4], "big")
    sp = int.from_bytes(raw[4:8], "big")
    print(f"entry_lr = 0x{lr:08X}  callsite=0x{(lr - 4) & 0xFFFFFFFF:08X}")
    print(f"entry_sp = 0x{sp:08X}")
    for index in range(FRAME_COUNT):
        offset = 8 + index * 8
        frame_sp = int.from_bytes(raw[offset : offset + 4], "big")
        frame_lr = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        callsite = (frame_lr - 4) & 0xFFFFFFFF if frame_lr else 0
        print(
            f"frame[{index:02d}] sp=0x{frame_sp:08X} "
            f"lr=0x{frame_lr:08X} callsite=0x{callsite:08X}"
        )


def inspect(client: Xbdm) -> tuple[bytes, bytes, str, bytes]:
    current = client.read(SITE, 4)
    cave = client.read(STUB, CAVE_SIZE)
    recv_log_current = client.read(RECV_LOG_SITE, 4)
    return current, cave, classify_cave(cave), recv_log_current


def apply(
    client: Xbdm,
    current: bytes,
    cave_kind: str,
    recv_log_current: bytes,
) -> None:
    if current not in (ORIGINAL, PATCH):
        raise RuntimeError("Unexpected DirtySock send entry")
    if recv_log_current == RECV_LOG_PATCH:
        raise RuntimeError(
            "Direct plaintext receive logger is active and its 0x83C8E000 "
            "stub overlaps the Redirector journal; restore it first"
        )
    if recv_log_current != RECV_LOG_ORIGINAL:
        raise RuntimeError(
            "Direct receive result site is unexpected; refusing to overwrite "
            "the shared 0x83C8E000 region"
        )
    if current == PATCH:
        if cave_kind == "redirector":
            print("Already armed: Redirector send stack capture is active.")
            return
        if cave_kind == "qos":
            raise RuntimeError(
                "QoS send stack capture is still active; restore it before "
                "migrating the shared code cave"
            )
        raise RuntimeError("Refusing to replace an unidentified active hook")
    if cave_kind not in ("empty", "redirector", "qos"):
        raise RuntimeError("Redirector stack trace code cave is not migratable")

    # The site is original here, so neither this image nor a restored QoS image
    # can be executing while the shared cave is replaced.
    write_chunks(client, JOURNAL, bytes(JOURNAL_CLEAR_SIZE))
    if client.read(JOURNAL, JOURNAL_CLEAR_SIZE) != bytes(JOURNAL_CLEAR_SIZE):
        raise RuntimeError("Redirector stack journal clear verification failed")
    write_chunks(client, STUB, STUB_IMAGE)
    if client.read(STUB, CAVE_SIZE) != STUB_IMAGE:
        raise RuntimeError("Redirector stack trace stub verification failed")
    if client.read(SITE, 4) != ORIGINAL:
        raise RuntimeError("DirtySock send entry changed before publication")

    try:
        client.write(SITE, PATCH)
        if client.read(SITE, 4) != PATCH:
            raise RuntimeError("Redirector stack trace site verification failed")
    except Exception:
        try:
            client.write(SITE, ORIGINAL)
        except Exception:
            pass
        raise

    suffix = " (migrated from restored QoS stub)" if cave_kind == "qos" else ""
    print(f"Verified: Redirector send stack capture armed{suffix}.")


def restore(client: Xbdm, current: bytes, cave_kind: str) -> None:
    if current == ORIGINAL:
        print("Already restored: DirtySock send entry is original.")
        return
    if current != PATCH:
        raise RuntimeError("Unexpected DirtySock send entry")
    if cave_kind == "qos":
        raise RuntimeError(
            "The shared patch belongs to the QoS probe; use its restore action"
        )
    if cave_kind != "redirector":
        raise RuntimeError("Refusing to restore an unidentified active hook")

    client.write(SITE, ORIGINAL)
    if client.read(SITE, 4) != ORIGINAL:
        raise RuntimeError("Redirector stack trace restore verification failed")
    print("Verified: Redirector send stack capture restored.")


def run_action(client: Xbdm, action: str) -> int:
    if action not in ("status", "apply", "restore", "read"):
        raise ValueError(f"Unknown action: {action}")
    current, _cave, cave_kind, recv_log_current = inspect(client)
    print(f"Redirector send stack trace: {describe_state(current, cave_kind)}")
    print(
        "Overlapping direct-recv logger: "
        f"{describe_recv_log_state(recv_log_current)}"
    )

    if action == "status":
        return 0
    if action == "read":
        if cave_kind != "redirector":
            raise RuntimeError(
                "The shared cave does not contain the Redirector probe; "
                "refusing to interpret its journal"
            )
        if recv_log_current != RECV_LOG_ORIGINAL:
            raise RuntimeError(
                "The direct-recv logger may own 0x83C8E000; refusing to "
                "interpret the Redirector journal"
            )
        describe(client)
        return 0
    if action == "apply":
        apply(client, current, cave_kind, recv_log_current)
        return 0
    restore(client, current, cave_kind)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        return run_action(client, args.action)
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
