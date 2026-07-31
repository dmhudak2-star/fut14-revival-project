#!/usr/bin/env python3
"""Pump queued local Blaze replies after their request is registered.

The ``--registered-only`` mode deliberately hooks only SITE.  It is the safe
functional mode when QoS is deferred to the Mac-side router: PreAuth and Ping
are pumped after the game's request-registration path, while EARLY_SITE stays
byte-for-byte original.
"""

from __future__ import annotations

import argparse
import time

from fifa14_plain_recv_hook import (
    PENDING_LENGTH,
    cmpwi,
    conditional_branch,
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
)


EARLY_SITE = 0x82EB1314
EARLY_ORIGINAL = bytes.fromhex("382100C0")  # addi r1,r1,0xC0
SITE = 0x82EB2040
ORIGINAL = bytes.fromhex("48000040")  # b 0x82EB2080
STUB = 0x83C8CB00
JOURNAL = 0x83C8CB80
JOURNAL_SIZE = 0x20
DELAY_STUB = 0x83C8CE00
DELAY_CONTROL = 0x83C8CE80
DELAY_TICKS = 3
RECEIVE_PUMP = 0x82EB0D10
RETURN_TARGET = 0x82EB2080
STUB_SPAN = JOURNAL - STUB
DELAY_STUB_SPAN = DELAY_CONTROL - DELAY_STUB
LIVE_STUB_DRAIN_SECONDS = 0.02


def build_stub() -> bytes:
    """Pump normal replies immediately, but arm delayed QoS replies."""
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # PENDING_LENGTH
        lwz(11, 12, 0x00),
        cmpwi(11, 0),
        0,                           # beq finish
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3180),      # DELAY_CONTROL
        lwz(10, 12, 0x00),          # delay ticks
        cmpwi(10, 0),
        0,                           # beq immediate
        addi(9, 0, 1),
        stw(9, 12, 0x04),           # request is now registered
        0,                           # b finish
    ]
    delayed_finish = len(words) - 1
    immediate = len(words)
    words.extend(
        [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3480),      # JOURNAL
        stw(31, 12, 0x04),          # Blaze connection object
        stw(11, 12, 0x08),          # queued bytes before pump
        stw(3, 12, 0x0C),           # preserve request timer result
        lwz(10, 12, 0x00),
        addi(10, 10, 1),
        stw(10, 12, 0x00),          # pump count
        addi(3, 31, 0),             # Blaze connection
        addi(4, 0, 0),              # synthetic event/tick value
        0,                           # bl RECEIVE_PUMP
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3480),
        lwz(3, 12, 0x0C),           # restore request timer result
        ]
    )
    finish = len(words)
    words.append(0)

    def address(index: int) -> int:
        return STUB + index * 4

    words[4] = conditional_branch(
        address(4), address(finish), 12, 2
    )                                # beq
    words[9] = conditional_branch(
        address(9), address(immediate), 12, 2
    )                                # beq
    words[delayed_finish] = branch(
        address(delayed_finish), address(finish), False
    )
    receive_call = immediate + 10
    words[receive_call] = branch(
        address(receive_call), RECEIVE_PUMP, True
    )
    words[-1] = branch(address(len(words) - 1), RETURN_TARGET, False)
    return b"".join(insn(word) for word in words)


def build_delay_stub() -> bytes:
    """Pump QoS only after registration and a few later network ticks."""
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3180),      # DELAY_CONTROL
        lwz(11, 12, 0x04),          # registered flag
        cmpwi(11, 0),
        0,                           # beq finish
        lwz(10, 12, 0x00),          # remaining ticks
        addi(10, 10, -1),
        stw(10, 12, 0x00),
        cmpwi(10, 0),
        0,                           # bne finish
        stw(10, 12, 0x04),          # clear registered flag
        addi(30, 3, 0),             # preserve return value
        addi(3, 27, 0),             # Blaze connection
        addi(4, 0, 0),
        0,                           # bl RECEIVE_PUMP
        addi(3, 30, 0),
    ]
    finish = len(words)
    words.extend((int.from_bytes(EARLY_ORIGINAL, "big"), 0))

    def address(index: int) -> int:
        return DELAY_STUB + index * 4

    words[4] = conditional_branch(
        address(4), address(finish), 12, 2
    )                                # beq
    words[9] = conditional_branch(
        address(9), address(finish), 4, 2
    )                                # bne
    words[14] = branch(address(14), RECEIVE_PUMP, True)
    words[-1] = branch(
        address(len(words) - 1), EARLY_SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def build_immediate_registered_stub() -> bytes:
    """Return the previous registered-site-only pump for safe migration."""
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),
        lwz(11, 12, 0x00),
        cmpwi(11, 0),
        0,
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3480),
        stw(31, 12, 0x04),
        stw(11, 12, 0x08),
        stw(3, 12, 0x0C),
        lwz(10, 12, 0x00),
        addi(10, 10, 1),
        stw(10, 12, 0x00),
        addi(3, 31, 0),
        addi(4, 0, 0),
        0,
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3480),
        lwz(3, 12, 0x0C),
    ]
    finish = len(words)
    words.append(0)

    def address(index: int) -> int:
        return STUB + index * 4

    words[4] = conditional_branch(
        address(4), address(finish), 12, 2
    )
    words[15] = branch(address(15), RECEIVE_PUMP, True)
    words[-1] = branch(
        address(len(words) - 1), RETURN_TARGET, False
    )
    return b"".join(insn(word) for word in words)


def build_registered_only_stub() -> bytes:
    """Pump at the registered-request site with thread-local call state.

    The older registered-only image kept the incoming r3 in JOURNAL+0x0C.
    That word is diagnostic/shared memory, so recursion or two title threads
    could restore another invocation's value.  A small ABI-aligned stack frame
    makes the functional image re-entrant while preserving r3 across the
    RECEIVE_PUMP call.
    """
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),       # PENDING_LENGTH
        lwz(11, 12, 0x00),
        cmpwi(11, 0),
        0,                           # beq finish
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3480),      # JOURNAL
        stw(31, 12, 0x04),          # Blaze connection object
        stw(11, 12, 0x08),          # queued bytes before pump
        stw(3, 12, 0x0C),           # diagnostic copy only
        lwz(10, 12, 0x00),
        addi(10, 10, 1),
        stw(10, 12, 0x00),          # pump count
        0x9421FF90,                  # stwu r1,-0x70(r1)
        stw(3, 1, 0x68),            # incoming r3 at old_sp-8
        addi(3, 31, 0),             # Blaze connection
        addi(4, 0, 0),              # synthetic event/tick value
        0,                           # bl RECEIVE_PUMP
        lwz(3, 1, 0x68),            # restore this invocation's r3
        addi(1, 1, 0x70),
    ]
    finish = len(words)
    words.append(0)

    def address(index: int) -> int:
        return STUB + index * 4

    words[4] = conditional_branch(
        address(4), address(finish), 12, 2
    )                                # beq
    words[17] = branch(address(17), RECEIVE_PUMP, True)
    words[-1] = branch(
        address(len(words) - 1), RETURN_TARGET, False
    )
    return b"".join(insn(word) for word in words)


def build_legacy_stub() -> bytes:
    """Return the former too-early epilogue pump for safe migration."""
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x300),
        lwz(11, 12, 0x00),
        cmpwi(11, 0),
        0,
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x3480),
        stw(27, 12, 0x04),
        stw(11, 12, 0x08),
        lwz(10, 12, 0x00),
        addi(10, 10, 1),
        stw(10, 12, 0x00),
        addi(30, 3, 0),
        addi(3, 27, 0),
        addi(4, 0, 0),
        0,
        addi(3, 30, 0),
    ]
    finish = len(words)
    words.extend((int.from_bytes(EARLY_ORIGINAL, "big"), 0))

    def address(index: int) -> int:
        return STUB + index * 4

    words[4] = conditional_branch(
        address(4), address(finish), 12, 2
    )
    words[15] = branch(address(15), RECEIVE_PUMP, True)
    words[-1] = branch(
        address(len(words) - 1), EARLY_SITE + 4, False
    )
    return b"".join(insn(word) for word in words)


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    delay = client.read(DELAY_CONTROL, 8)
    print(f"pump_count       = {int.from_bytes(raw[0:4], 'big')}")
    print(f"connection       = 0x{int.from_bytes(raw[4:8], 'big'):08X}")
    print(f"queued_before    = {int.from_bytes(raw[8:12], 'big')}")
    pending = int.from_bytes(client.read(PENDING_LENGTH, 4), "big")
    print(f"pending_now      = {pending}")
    print(f"delay_ticks      = {int.from_bytes(delay[0:4], 'big')}")
    print(f"delay_registered = {int.from_bytes(delay[4:8], 'big')}")


def padded(image: bytes, span: int) -> bytes:
    if len(image) > span:
        raise RuntimeError(
            f"Stub image 0x{len(image):X} exceeds cave span 0x{span:X}"
        )
    return image.ljust(span, b"\0")


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--registered-only",
        action="store_true",
        help=(
            "Hook only the registered-request SITE; keep EARLY_SITE original "
            "(requires QoS to be deferred elsewhere)"
        ),
    )
    mode.add_argument(
        "--compat-immediate",
        action="store_true",
        help=(
            "Restore the original registered-site-only pump used by the "
            "003253 QoS-success session; keep EARLY_SITE original"
        ),
    )
    args = parser.parse_args()

    stub = build_stub()
    registered_stub = build_registered_only_stub()
    delay_stub = build_delay_stub()
    legacy_stub = build_legacy_stub()
    immediate_stub = build_immediate_registered_stub()
    known_stub_images = {
        "empty": bytes(STUB_SPAN),
        "registered-only": padded(registered_stub, STUB_SPAN),
        "delayed": padded(stub, STUB_SPAN),
        "legacy-registered-only": padded(immediate_stub, STUB_SPAN),
        "legacy-early": padded(legacy_stub, STUB_SPAN),
    }
    registered_mode = args.registered_only or args.compat_immediate
    target_mode = (
        "legacy-registered-only"
        if args.compat_immediate
        else "registered-only"
        if args.registered_only
        else "delayed"
    )
    target_stub_image = known_stub_images[target_mode]
    target_delay_image = padded(delay_stub, DELAY_STUB_SPAN)
    patch = insn(branch(SITE, STUB, False))
    legacy_early_patch = insn(branch(EARLY_SITE, STUB, False))
    delay_patch = insn(branch(EARLY_SITE, DELAY_STUB, False))
    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = client.read(SITE, 4)
        early = client.read(EARLY_SITE, 4)
        state = (
            "original"
            if current == ORIGINAL
            else "patched"
            if current == patch
            else f"unexpected:{current.hex().upper()}"
        )
        early_state = (
            "original"
            if early == EARLY_ORIGINAL
            else "legacy-patched"
            if early == legacy_early_patch
            else "delay-patched"
            if early == delay_patch
            else f"unexpected:{early.hex().upper()}"
        )
        print(
            f"Pending-response pump: registered-site={state}, "
            f"early-site={early_state}"
        )
        cave = client.read(STUB, STUB_SPAN)
        cave_mode = next(
            (
                label
                for label, image in known_stub_images.items()
                if cave == image
            ),
            "unknown",
        )
        print(f"Pump cave mode: {cave_mode}")
        if args.action in ("status", "read"):
            describe(client)
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected registered-request site")
            if early_state not in (
                "original",
                "legacy-patched",
                "delay-patched",
            ):
                raise RuntimeError("Unexpected early request-send epilogue")
            if cave_mode == "unknown":
                raise RuntimeError("Pending-response pump code cave is not free")
            delay_cave: bytes | None = None
            if not registered_mode:
                delay_cave = client.read(DELAY_STUB, DELAY_STUB_SPAN)
                if delay_cave not in (
                    bytes(DELAY_STUB_SPAN),
                    target_delay_image,
                ):
                    raise RuntimeError("Delayed-response code cave is not free")

            target_early_patch = (
                EARLY_ORIGINAL if registered_mode else delay_patch
            )
            if (
                state == "patched"
                and early == target_early_patch
                and cave == target_stub_image
                and (
                    registered_mode
                    or delay_cave == target_delay_image
                )
            ):
                print(
                    f"Already patched in {target_mode} mode."
                    if registered_mode
                    else "Already patched in delayed mode."
                )
                return 0

            # Remove every published entry before replacing shared executable
            # bytes.  This also migrates legacy EARLY_SITE hooks back to the
            # original instruction for registered-only mode.
            unpublished = False
            if state == "patched":
                client.write(SITE, ORIGINAL)
                unpublished = True
            if early_state in ("legacy-patched", "delay-patched"):
                client.write(EARLY_SITE, EARLY_ORIGINAL)
                unpublished = True
            if unpublished:
                time.sleep(LIVE_STUB_DRAIN_SECONDS)
            try:
                client.write(JOURNAL, bytes(JOURNAL_SIZE))
                client.write(DELAY_CONTROL, bytes(8))
                write_chunks(client, STUB, target_stub_image)
                if client.read(STUB, STUB_SPAN) != target_stub_image:
                    raise RuntimeError("Registered pump stub verification failed")
                if not registered_mode:
                    write_chunks(client, DELAY_STUB, target_delay_image)
                    if (
                        client.read(DELAY_STUB, DELAY_STUB_SPAN)
                        != target_delay_image
                    ):
                        raise RuntimeError("Delay pump stub verification failed")
                client.write(SITE, patch)
                if not registered_mode:
                    client.write(EARLY_SITE, delay_patch)
                if client.read(SITE, 4) != patch:
                    raise RuntimeError("Registered pump site verification failed")
                if client.read(EARLY_SITE, 4) != target_early_patch:
                    raise RuntimeError("Early pump site verification failed")
            except Exception:
                try:
                    client.write(SITE, ORIGINAL)
                    client.write(EARLY_SITE, EARLY_ORIGINAL)
                except Exception:
                    pass
                raise
            if registered_mode:
                if args.compat_immediate:
                    print(
                        "Verified: 003253-compatible immediate registered "
                        "pump; EARLY_SITE remains original."
                    )
                else:
                    print(
                        "Verified: registered-request-only PreAuth/Ping pump; "
                        "EARLY_SITE remains original."
                    )
            else:
                print(
                    "Verified: normal replies pump after registration; "
                    "QoS can be delayed by later network ticks."
                )
            return 0
        unpublished = False
        if state == "patched":
            client.write(SITE, ORIGINAL)
            unpublished = True
        elif state != "original":
            raise RuntimeError("Unexpected registered-request site")
        if early_state in ("legacy-patched", "delay-patched"):
            client.write(EARLY_SITE, EARLY_ORIGINAL)
            unpublished = True
        elif early_state != "original":
            raise RuntimeError("Unexpected early request-send epilogue")
        if unpublished:
            time.sleep(LIVE_STUB_DRAIN_SECONDS)
        client.write(DELAY_CONTROL, bytes(8))
        if (
            client.read(SITE, 4) != ORIGINAL
            or client.read(EARLY_SITE, 4) != EARLY_ORIGINAL
        ):
            raise RuntimeError("Pump restore verification failed")
        print("Verified: original pump sites restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
