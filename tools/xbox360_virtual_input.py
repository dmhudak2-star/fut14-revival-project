#!/usr/bin/env python3
"""Inject short virtual controller pulses through XamInputGetState."""

from __future__ import annotations

import argparse
import struct
import time

from fifa14_plain_send_hook import Xbdm, addi, addis, insn, lwz, stw


SITE = 0x816F14E8
STOCK_ORIGINAL = bytes.fromhex(
    "3C008081"  # lis r0, 0x8081
    "6000DC20"  # ori r0, r0, 0xDC20
    "7C0903A6"  # mtctr r0
    "4E800420"  # bctr
)
CAVE = 0x81A7F000
MAILBOX = 0x81A7F100
MAILBOX_SIZE = 0x20
ORIGINAL_SAVE = 0x81A7F120
SAVE_MAGIC = b"VIH1"

BUTTONS = {
    "UP": 0x0001,
    "DOWN": 0x0002,
    "LEFT": 0x0004,
    "RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "LS": 0x0040,
    "RS": 0x0080,
    "LB": 0x0100,
    "RB": 0x0200,
    "GUIDE": 0x0400,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}


def ori(ra: int, rs: int, immediate: int) -> int:
    return (
        0x60000000
        | ((rs & 31) << 21)
        | ((ra & 31) << 16)
        | (immediate & 0xFFFF)
    )


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | ((ra & 31) << 16) | (immediate & 0xFFFF)


def lhz(rt: int, ra: int, displacement: int) -> int:
    return (
        0xA0000000
        | ((rt & 31) << 21)
        | ((ra & 31) << 16)
        | (displacement & 0xFFFF)
    )


def lbz(rt: int, ra: int, displacement: int) -> int:
    return (
        0x88000000
        | ((rt & 31) << 21)
        | ((ra & 31) << 16)
        | (displacement & 0xFFFF)
    )


def sth(rs: int, ra: int, displacement: int) -> int:
    return (
        0xB0000000
        | ((rs & 31) << 21)
        | ((ra & 31) << 16)
        | (displacement & 0xFFFF)
    )


def stb(rs: int, ra: int, displacement: int) -> int:
    return (
        0x98000000
        | ((rs & 31) << 21)
        | ((ra & 31) << 16)
        | (displacement & 0xFFFF)
    )


def conditional_branch(
    source: int, target: int, *, equal: bool
) -> int:
    displacement = target - source
    if displacement % 4 or not -0x8000 <= displacement < 0x8000:
        raise ValueError("Conditional branch target is out of range")
    opcode = 0x41820000 if equal else 0x40820000
    return opcode | (displacement & 0xFFFC)


def absolute_jump(address: int) -> bytes:
    words = [
        addis(0, 0, address >> 16),
        ori(0, 0, address),
        0x7C0903A6,  # mtctr r0
        0x4E800420,  # bctr
    ]
    return b"".join(insn(word) for word in words)


def build_stub(original: bytes) -> bytes:
    if len(original) != 16:
        raise ValueError("The chained XamInputGetState trampoline must be 16 bytes")
    words = [
        addis(11, 0, MAILBOX >> 16),
        ori(11, 11, MAILBOX),
        cmpwi(3, 0),
        0,                              # bne fallback
        lwz(10, 11, 0x00),              # enabled
        cmpwi(10, 0),
        0,                              # beq fallback
        lwz(10, 11, 0x14),              # remaining frames
        cmpwi(10, 0),
        0,                              # beq fallback
        addi(10, 10, -1),
        stw(10, 11, 0x14),
        lwz(10, 11, 0x04),              # packet number
        addi(10, 10, 1),
        stw(10, 11, 0x04),
        stw(10, 5, 0x00),
        lhz(10, 11, 0x08),              # buttons
        sth(10, 5, 0x04),
        lbz(10, 11, 0x0A),              # triggers
        stb(10, 5, 0x06),
        lbz(10, 11, 0x0B),
        stb(10, 5, 0x07),
        lhz(10, 11, 0x0C),              # sticks
        sth(10, 5, 0x08),
        lhz(10, 11, 0x0E),
        sth(10, 5, 0x0A),
        lhz(10, 11, 0x10),
        sth(10, 5, 0x0C),
        lhz(10, 11, 0x12),
        sth(10, 5, 0x0E),
        addi(3, 0, 0),                   # ERROR_SUCCESS
        0x4E800020,                      # blr
    ]
    fallback_index = len(words)
    words.extend(
        int.from_bytes(original[i : i + 4], "big")
        for i in range(0, len(original), 4)
    )
    fallback = CAVE + fallback_index * 4
    for index in (3, 6, 9):
        words[index] = conditional_branch(
            CAVE + index * 4,
            fallback,
            equal=index != 3,
        )
    return b"".join(insn(word) for word in words)


def verify_xam(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if 'name="xam.xex"' in line.lower()
        ),
        None,
    )
    if module is None or "base=0x815f0000" not in module.lower():
        raise RuntimeError(f"Unexpected or missing xam.xex: {module}")


def trampoline_target(code: bytes) -> int | None:
    if len(code) != 16:
        return None
    w0, w1, w2, w3 = struct.unpack(">IIII", code)
    register = (w0 >> 21) & 31
    if (w0 >> 26) != 15 or ((w0 >> 16) & 31) != 0:
        return None
    if (w1 >> 26) != 24:
        return None
    if ((w1 >> 21) & 31) != register or ((w1 >> 16) & 31) != register:
        return None
    if (w2 & ~(31 << 21)) != 0x7C0903A6:
        return None
    if ((w2 >> 21) & 31) != register or w3 != 0x4E800420:
        return None
    return ((w0 & 0xFFFF) << 16) | (w1 & 0xFFFF)


def state_name(current: bytes, hook: bytes) -> str:
    if current == hook:
        return "patched"
    target = trampoline_target(current)
    if target is not None:
        return f"original->0x{target:08X}"
    return f"unexpected:{current.hex().upper()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument(
        "action", choices=("status", "apply", "restore", "press", "release")
    )
    parser.add_argument("button", nargs="?", choices=sorted(BUTTONS))
    parser.add_argument("--frames", type=int, default=4)
    args = parser.parse_args()

    hook = absolute_jump(CAVE)
    client = Xbdm(args.host)
    try:
        verify_xam(client)
        current = client.read(SITE, len(hook))
        state = state_name(current, hook)
        print(f"XamInputGetState hook: {state}")

        if args.action == "status":
            mailbox = client.read(MAILBOX, MAILBOX_SIZE)
            print(f"enabled          = {int.from_bytes(mailbox[0:4], 'big')}")
            print(f"packet           = {int.from_bytes(mailbox[4:8], 'big')}")
            print(f"buttons          = 0x{int.from_bytes(mailbox[8:10], 'big'):04X}")
            print(f"remaining_frames = {int.from_bytes(mailbox[0x14:0x18], 'big')}")
            return 0

        if args.action == "apply":
            if not (state.startswith("original->") or state == "patched"):
                raise RuntimeError("Unexpected XamInputGetState entry")
            if state == "patched":
                saved = client.read(ORIGINAL_SAVE, 20)
                if saved[:4] != SAVE_MAGIC or trampoline_target(saved[4:]) is None:
                    raise RuntimeError("Missing saved chained XAM trampoline")
                original = saved[4:]
            else:
                original = current
            stub = build_stub(original)
            cave = client.read(CAVE, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("XAM virtual-input cave is not free")
            client.write(ORIGINAL_SAVE, SAVE_MAGIC + original)
            client.write(MAILBOX, bytes(MAILBOX_SIZE))
            client.write(CAVE, stub)
            client.write(SITE, hook)
            if client.read(SITE, len(hook)) != hook:
                raise RuntimeError("Virtual-input hook verification failed")
            print("Verified: reversible virtual-input hook installed.")
            return 0

        if args.action == "restore":
            if state == "patched":
                saved = client.read(ORIGINAL_SAVE, 20)
                if saved[:4] != SAVE_MAGIC or trampoline_target(saved[4:]) is None:
                    raise RuntimeError("Missing saved chained XAM trampoline")
                client.write(MAILBOX, bytes(MAILBOX_SIZE))
                client.write(SITE, saved[4:])
            elif not state.startswith("original->"):
                raise RuntimeError("Unexpected XamInputGetState entry")
            print("Verified: original XamInputGetState trampoline restored.")
            return 0

        if state != "patched":
            raise RuntimeError("Install the hook with the apply action first")

        if args.action == "release":
            client.write(MAILBOX, bytes(MAILBOX_SIZE))
            print("Virtual controller released.")
            return 0

        if args.button is None:
            raise RuntimeError("The press action requires a button name")
        if not 1 <= args.frames <= 120:
            raise RuntimeError("--frames must be between 1 and 120")

        old = client.read(MAILBOX, MAILBOX_SIZE)
        packet = int.from_bytes(old[4:8], "big")
        mailbox = struct.pack(
            ">IIHBBhhhhI8x",
            1,
            packet,
            BUTTONS[args.button],
            0,
            0,
            0,
            0,
            0,
            0,
            args.frames,
        )
        client.write(MAILBOX, mailbox)
        print(
            f"Pressed {args.button} for {args.frames} input polls "
            f"(mask=0x{BUTTONS[args.button]:04X})."
        )
    finally:
        client.close()

    time.sleep(min(0.5, max(0.12, args.frames / 50.0)))
    client = Xbdm(args.host)
    try:
        client.write(MAILBOX, bytes(MAILBOX_SIZE))
    finally:
        client.close()
    print("Virtual controller released.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
