#!/usr/bin/env python3
"""Relay FIFA 14's natural network-failure callback to state 2.

The patch changes only the state-0 function pointer in the title's static
network-listener vtable.  When the listener is already in state 1, a guarded
one-shot wrapper substitutes the native state-2 transition.  Every other path
calls the original state-0 callback with its untouched reason argument.  The
allocation is volatile and is removed when the title is unloaded.
"""

from __future__ import annotations

import argparse
import time

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    cmpw,
    cmpwi,
    conditional_branch,
    insn,
    lwz,
    stw,
    verify_module,
)


VTABLE = 0x8202D0F8
STATE1_SLOT = 0x8202D0FC
STATE0_SLOT = 0x8202D100
STATE2_SLOT = 0x8202D104
ORIGINAL_STATE1 = 0x8251A560
ORIGINAL_STATE0 = 0x8251A658
ORIGINAL_STATE2 = 0x8251A6E0

STATE0_STUB = 0x83C8B400
STATE0_END = 0x83C8B900
JOURNAL = 0x83C8B900
JOURNAL_SIZE = 0xC0
ALLOCATION_END = 0x83C8B9C0
LIMIT = 0x83C8C000

ROOT_GLOBAL = 0x897C3608
ROOT_VTABLE = 0x89708AE0
ROOT_INITIALIZE = 0x89748A38
ROOT_STATE = 0x80
ROOT_C_FIELDS = (0x3A08, 0x3A0C, 0x3A10, 0x3A14)
ROOT_PREINIT = (0x3A44, 0x3A48, 0x3AF8, 0x3A4C)
HOST_GLOBALS = (
    0x897C335C,
    0x897C3370,
    0x897C339C,
    0x897C33A0,
    0x897C33B4,
    0x897C33CC,
)
LISTENER_STATE = 0x974
B0C_OWNER = 0xBD2DC740
B0C_SIGNAL_OFFSETS = (0x6F8, 0x778, 0x7F8)
POWDLL_FE_THREAD = 0x82D63528

# Journal (all words are big endian).
J_MAGIC = 0x00
J_VERSION = 0x04
J_ONESHOT = 0x08       # 0=idle, 1=inside state2, 2=completed
J_STATUS = 0x0C
J_STATE1_COUNT = 0x10
J_STATE0_COUNT = 0x14
J_STATE2_COUNT = 0x18
J_SYNTH_STATE1_COUNT = 0x1C
J_FALLBACK_COUNT = 0x20
J_GUARD_PASS = 0x24
J_GUARD_FAIL = 0x28
J_LAST_PATH = 0x2C
J_LISTENER = 0x30
J_REASON = 0x34
J_BEFORE_STATE = 0x38
J_AFTER_STATE1 = 0x3C
J_AFTER_STATE2 = 0x40
J_ROOT = 0x44
J_ROOT_VTABLE = 0x48
J_ROOT_TARGET = 0x4C
J_ROOT_STATE = 0x50
J_C_FIELDS = (0x54, 0x58, 0x5C, 0x60)
J_PREINIT = (0x64, 0x68, 0x6C, 0x70)
J_HOSTS = (0x74, 0x78, 0x7C, 0x80, 0x84, 0x88)
J_ORIGINAL_RESULT = 0x8C
J_RELAY_RESULT = 0x90
J_CLAIM_LOST = 0x94
J_EXPECTED_LISTENER = 0x98

MAGIC = 0x4E535232  # "NSR2"
VERSION = 1

STATUS_NAMES = {
    0: "idle",
    8: "listener-instance-unexpected",
    9: "listener-null",
    10: "listener-vtable-unexpected",
    11: "root-null",
    12: "root-vtable-unexpected",
    13: "root-target-unexpected",
    14: "root+0x80-nonzero",
    15: "root+0x3A08-nonzero",
    16: "root+0x3A0C-nonzero",
    17: "root+0x3A10-nonzero",
    18: "root+0x3A14-nonzero",
    19: "root+0x3A44-null",
    20: "root+0x3A48-null",
    21: "root+0x3AF8-null",
    22: "root+0x3A4C-null",
    23: "host-335C-null",
    24: "host-3370-null",
    25: "host-339C-null",
    26: "host-33A0-null",
    27: "host-33B4-null",
    28: "host-33CC-null",
    40: "one-shot-busy",
    41: "state-not-0-or-1",
    50: "state1-native-complete",
    51: "state1-relaying-state2",
    52: "state0-relay-0-to-1-to-2",
    53: "state0-relay-1-to-2",
    54: "relay-complete",
    55: "native-state0-fallback",
    60: "post-state-not-2",
    61: "post-root+0x80-not-1",
    62: "post-root+0x3A08-null",
    63: "post-root+0x3A0C-null",
    64: "post-root+0x3A10-null",
    65: "post-root+0x3A14-null",
    66: "post-auth-vtable-unexpected",
}

FRAME_SIZE = 0xC0
FRAME_ARGS = 0x70
FRAME_RESULT = 0xB0
MFLR_R0 = 0x7C0802A6
MTLR_R0 = 0x7C0803A6
BLR = 0x4E800020
SYNC = 0x7C0004AC


def stwu(rs: int, ra: int, displacement: int) -> int:
    return 0x94000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def std(rs: int, ra: int, displacement: int) -> int:
    if displacement & 3:
        raise ValueError("std displacement must be four-byte aligned")
    return 0xF8000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFC)


def ld(rt: int, ra: int, displacement: int) -> int:
    if displacement & 3:
        raise ValueError("ld displacement must be four-byte aligned")
    return 0xE8000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFC)


def lwarx(rt: int, ra: int, rb: int) -> int:
    return 0x7C000028 | (rt << 21) | (ra << 16) | (rb << 11)


def stwcx(rs: int, ra: int, rb: int) -> int:
    return 0x7C00012D | (rs << 21) | (ra << 16) | (rb << 11)


def load_address(rt: int, address: int) -> tuple[int, int]:
    high = ((address + 0x8000) >> 16) & 0xFFFF
    low = address & 0xFFFF
    if low & 0x8000:
        low -= 0x10000
    return addis(rt, 0, high), addi(rt, rt, low)


class Builder:
    def __init__(self, base: int):
        self.base = base
        self.words: list[int] = []
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int | None, int | None]] = []

    def emit(self, *words: int) -> None:
        self.words.extend(words)

    def mark(self, label: str) -> None:
        if label in self.labels:
            raise AssertionError(f"duplicate label {label}")
        self.labels[label] = len(self.words)

    def jump(self, label: str) -> None:
        self.fixups.append((len(self.words), label, None, None))
        self.words.append(0)

    def condition(self, label: str, bo: int, bi: int) -> None:
        self.fixups.append((len(self.words), label, bo, bi))
        self.words.append(0)

    def call(self, target: int) -> None:
        address = self.base + len(self.words) * 4
        self.words.append(branch(address, target, True))

    def finish(self) -> bytes:
        for index, label, bo, bi in self.fixups:
            if label not in self.labels:
                raise AssertionError(f"undefined label {label}")
            source = self.base + index * 4
            target = self.base + self.labels[label] * 4
            self.words[index] = (
                branch(source, target, False)
                if bo is None
                else conditional_branch(source, target, bo, bi or 0)
            )
        return b"".join(insn(word) for word in self.words)


def prologue(b: Builder) -> None:
    b.emit(MFLR_R0, stw(0, 1, -8), stwu(1, 1, -FRAME_SIZE))
    for register in range(3, 11):
        b.emit(std(register, 1, FRAME_ARGS + (register - 3) * 8))


def epilogue(b: Builder) -> None:
    b.emit(ld(3, 1, FRAME_RESULT))
    for register in range(4, 11):
        b.emit(ld(register, 1, FRAME_ARGS + (register - 3) * 8))
    b.emit(addi(1, 1, FRAME_SIZE), lwz(0, 1, -8), MTLR_R0, BLR)


def journal_increment(b: Builder, offset: int) -> None:
    b.emit(lwz(10, 12, offset), addi(10, 10, 1), stw(10, 12, offset))


def emit_guards(b: Builder, prefix: str, expected_listener: int) -> list[tuple[str, int]]:
    """Emit guards; r12 must be JOURNAL and saved r3 must be the listener."""
    failures: list[tuple[str, int]] = []

    def require_equal(register: int, value: int, label: str, status: int) -> None:
        b.emit(*load_address(9, value), cmpw(register, 9))
        b.condition(label, 4, 2)  # bne
        failures.append((label, status))

    listener_null = f"{prefix}_listener_null"
    b.emit(ld(11, 1, FRAME_ARGS), cmpwi(11, 0))
    b.condition(listener_null, 12, 2)
    failures.append((listener_null, 9))
    b.emit(*load_address(10, expected_listener), cmpw(11, 10))
    listener_instance = f"{prefix}_listener_instance"
    b.condition(listener_instance, 4, 2)
    failures.append((listener_instance, 8))
    b.emit(lwz(10, 11, 0))
    require_equal(10, VTABLE, f"{prefix}_listener_vtable", 10)

    root_null = f"{prefix}_root_null"
    b.emit(*load_address(10, ROOT_GLOBAL), lwz(11, 10, 0), stw(11, 12, J_ROOT), cmpwi(11, 0))
    b.condition(root_null, 12, 2)
    failures.append((root_null, 11))

    b.emit(lwz(10, 11, 0), stw(10, 12, J_ROOT_VTABLE))
    require_equal(10, ROOT_VTABLE, f"{prefix}_root_vtable", 12)
    b.emit(lwz(10, 10, 0x0C), stw(10, 12, J_ROOT_TARGET))
    require_equal(10, ROOT_INITIALIZE, f"{prefix}_root_target", 13)

    fields = ((ROOT_STATE, J_ROOT_STATE, 14),) + tuple(
        (offset, journal, 15 + index)
        for index, (offset, journal) in enumerate(zip(ROOT_C_FIELDS, J_C_FIELDS))
    )
    for index, (offset, journal, status) in enumerate(fields):
        label = f"{prefix}_field_{index}"
        b.emit(lwz(10, 11, offset), stw(10, 12, journal), cmpwi(10, 0))
        b.condition(label, 4, 2)
        failures.append((label, status))

    for index, (offset, journal) in enumerate(zip(ROOT_PREINIT, J_PREINIT)):
        label = f"{prefix}_pre_{index}"
        b.emit(lwz(10, 11, offset), stw(10, 12, journal), cmpwi(10, 0))
        b.condition(label, 12, 2)
        failures.append((label, 19 + index))

    for index, (address, journal) in enumerate(zip(HOST_GLOBALS, J_HOSTS)):
        label = f"{prefix}_host_{index}"
        b.emit(*load_address(10, address), lwz(9, 10, 0), stw(9, 12, journal), cmpwi(9, 0))
        b.condition(label, 12, 2)
        failures.append((label, 23 + index))
    return failures


def emit_claim(b: Builder, busy_label: str) -> None:
    b.mark(f"{busy_label}_retry")
    b.emit(addi(11, 12, J_ONESHOT), lwarx(10, 0, 11), cmpwi(10, 0))
    b.condition(busy_label, 4, 2)
    b.emit(addi(10, 0, 1), stwcx(10, 0, 11))
    b.condition(f"{busy_label}_retry", 4, 2)  # lost reservation
    b.emit(SYNC)


def emit_failures(b: Builder, failures: list[tuple[str, int]], target: str) -> None:
    for label, status in failures:
        b.mark(label)
        b.emit(addi(10, 0, status))
        b.jump(target)


def build_state0_stub(expected_listener: int) -> bytes:
    b = Builder(STATE0_STUB)
    prologue(b)
    # Fallback result is initialized to the incoming r3 for the successful
    # (void) relay paths; native fallback overwrites it with its own result.
    b.emit(ld(10, 1, FRAME_ARGS), std(10, 1, FRAME_RESULT), *load_address(12, JOURNAL))
    journal_increment(b, J_STATE0_COUNT)
    b.emit(ld(10, 1, FRAME_ARGS), stw(10, 12, J_LISTENER), ld(9, 1, FRAME_ARGS + 8), stw(9, 12, J_REASON), addi(9, 0, 2), stw(9, 12, J_LAST_PATH))

    failures = emit_guards(b, "s0", expected_listener)
    b.emit(ld(11, 1, FRAME_ARGS), lwz(10, 11, LISTENER_STATE), stw(10, 12, J_BEFORE_STATE), cmpwi(10, 1))
    b.condition("s0_bad_state", 4, 2)
    emit_claim(b, "s0_busy")
    journal_increment(b, J_GUARD_PASS)
    b.emit(addi(10, 0, 53), stw(10, 12, J_STATUS))

    journal_increment(b, J_STATE2_COUNT)
    b.emit(ld(3, 1, FRAME_ARGS))
    b.call(ORIGINAL_STATE2)
    b.emit(*load_address(12, JOURNAL), stw(3, 12, J_RELAY_RESULT), ld(11, 1, FRAME_ARGS), lwz(10, 11, LISTENER_STATE), stw(10, 12, J_AFTER_STATE2), cmpwi(10, 2))
    b.condition("s0_post_state", 4, 2)
    b.emit(lwz(11, 12, J_ROOT), lwz(10, 11, ROOT_STATE), stw(10, 12, J_ROOT_STATE), cmpwi(10, 1))
    b.condition("s0_post_root_state", 4, 2)
    for index, (offset, journal) in enumerate(zip(ROOT_C_FIELDS, J_C_FIELDS)):
        b.emit(lwz(10, 11, offset), stw(10, 12, journal), cmpwi(10, 0))
        b.condition(f"s0_post_c_{index}", 12, 2)
    b.emit(lwz(10, 11, ROOT_C_FIELDS[0]), lwz(10, 10, 0), *load_address(9, 0x89707078), cmpw(10, 9))
    b.condition("s0_post_auth_vtable", 4, 2)
    b.emit(addi(10, 0, 54), stw(10, 12, J_STATUS), SYNC, addi(10, 0, 2), stw(10, 12, J_ONESHOT), SYNC)
    b.jump("s0_done")

    post_failures = (
        ("s0_post_state", 60),
        ("s0_post_root_state", 61),
        ("s0_post_c_0", 62),
        ("s0_post_c_1", 63),
        ("s0_post_c_2", 64),
        ("s0_post_c_3", 65),
        ("s0_post_auth_vtable", 66),
    )
    for label, status in post_failures:
        b.mark(label)
        b.emit(addi(10, 0, status))
        b.jump("s0_terminal_failure")
    b.mark("s0_terminal_failure")
    b.emit(stw(10, 12, J_STATUS), SYNC, addi(10, 0, 3), stw(10, 12, J_ONESHOT), SYNC)
    b.jump("s0_done")

    b.mark("s0_bad_state")
    b.emit(addi(10, 0, 41))
    b.jump("s0_record_fallback")

    b.mark("s0_busy")
    b.emit(lwz(10, 12, J_CLAIM_LOST), addi(10, 10, 1), stw(10, 12, J_CLAIM_LOST), addi(10, 0, 40))
    b.jump("s0_record_fallback")
    emit_failures(b, failures, "s0_record_fallback")

    b.mark("s0_record_fallback")
    b.emit(stw(10, 12, J_STATUS))
    journal_increment(b, J_GUARD_FAIL)
    journal_increment(b, J_FALLBACK_COUNT)
    b.emit(addi(10, 0, 55), stw(10, 12, J_LAST_PATH), ld(3, 1, FRAME_ARGS), ld(4, 1, FRAME_ARGS + 8))
    b.call(ORIGINAL_STATE0)
    b.emit(std(3, 1, FRAME_RESULT), *load_address(12, JOURNAL), stw(3, 12, J_ORIGINAL_RESULT))

    b.mark("s0_done")
    epilogue(b)
    return b.finish()


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def u32(raw: bytes, offset: int = 0) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def discover_listener(client: Xbdm) -> tuple[int, int]:
    """Find and validate the sole listener carrying powdll_FEThread."""
    matches: list[int] = []
    for signal_offset in B0C_SIGNAL_OFFSETS:
        signal = B0C_OWNER + signal_offset
        begin = u32(client.read(signal + 4, 4))
        end = u32(client.read(signal + 8, 4))
        if end < begin or (end - begin) % 4 or end - begin > 0x400:
            continue
        for slot in range(begin, end, 4):
            receiver = u32(client.read(slot, 4))
            if not receiver:
                continue
            try:
                vtable = u32(client.read(receiver, 4))
                callback = u32(client.read(vtable + 4, 4))
            except Exception:
                continue
            if vtable == VTABLE and callback == ORIGINAL_STATE1 and receiver not in matches:
                matches.append(receiver)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one network listener, found {len(matches)}")
    listener = matches[0]
    observers = u32(client.read(listener - 0x48, 4))
    count = u32(client.read(listener - 0x44, 4))
    if not observers or not 0 < count <= 0x100:
        raise RuntimeError(f"Invalid listener observer vector/count: {count}")
    callbacks: list[int] = []
    for index in range(count):
        observer = u32(client.read(observers + index * 4, 4))
        if not observer:
            continue
        callbacks.append(u32(client.read(u32(client.read(observer, 4)), 4)))
    if callbacks.count(POWDLL_FE_THREAD) != 1:
        raise RuntimeError("powdll_FEThread observer is absent or duplicated")
    return listener, count


def pointer_state(value: int, original: int, patched: int) -> str:
    if value == original:
        return "original"
    if value == patched:
        return "relay"
    return f"unexpected:0x{value:08X}"


def restore_slot(client: Xbdm) -> None:
    """Restore only this script's exact pointer and verify the result."""
    current = u32(client.read(STATE0_SLOT, 4))
    if current == ORIGINAL_STATE0:
        return
    if current != STATE0_STUB:
        raise RuntimeError(
            f"Refusing to overwrite foreign state0 pointer 0x{current:08X}"
        )
    client.write(STATE0_SLOT, ORIGINAL_STATE0.to_bytes(4, "big"))
    restored = u32(client.read(STATE0_SLOT, 4))
    if restored != ORIGINAL_STATE0:
        raise RuntimeError(f"State0 pointer restore failed: 0x{restored:08X}")


def restore_slot_fresh(host: str, attempts: int = 3) -> None:
    """Retry restoration on fresh sockets after an ACK timeout."""
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        recovery: Xbdm | None = None
        try:
            recovery = Xbdm(host)
            verify_module(recovery)
            restore_slot(recovery)
            return
        except Exception as error:
            errors.append(f"attempt {attempt}: {error}")
            if attempt != attempts:
                time.sleep(0.15)
        finally:
            if recovery is not None:
                try:
                    recovery.close()
                except Exception:
                    pass
    raise RuntimeError(
        "CRITICAL: automatic state0-pointer restoration failed ("
        + "; ".join(errors)
        + f"). Run immediately: python3 outputs/fifa14_network_state2_relay.py "
        f"{host} restore"
    )


def restore_slot_resilient(host: str, client: Xbdm) -> None:
    try:
        restore_slot(client)
    except Exception as current_error:
        print(
            f"Primary restore failed ({current_error}); retrying with "
            "fresh XBDM connections.",
            flush=True,
        )
        restore_slot_fresh(host)


def validate_layout(state0: bytes) -> None:
    if STATE0_STUB + len(state0) > STATE0_END:
        raise AssertionError(f"state0 stub ends at 0x{STATE0_STUB + len(state0):08X}")
    if not (0x83C8B400 <= STATE0_STUB < STATE0_END <= JOURNAL):
        raise AssertionError("code regions overlap or leave B400..BFFF")
    if JOURNAL + JOURNAL_SIZE != ALLOCATION_END or ALLOCATION_END > LIMIT:
        raise AssertionError("journal allocation is outside B400..BFFF")

    def sign_extend(value: int, bits: int) -> int:
        return value - (1 << bits) if value & (1 << (bits - 1)) else value

    external_calls: list[int] = []
    end = STATE0_STUB + len(state0)
    for offset in range(0, len(state0), 4):
        word = u32(state0, offset)
        opcode = word >> 26
        source = STATE0_STUB + offset
        if opcode == 18:  # b/bl
            displacement = sign_extend(word & 0x03FFFFFC, 26)
            target = displacement if word & 2 else source + displacement
            if not STATE0_STUB <= target < end:
                if not word & 1:
                    raise AssertionError(f"external non-link branch at 0x{source:08X}")
                external_calls.append(target & 0xFFFFFFFF)
        elif opcode == 16:  # bc
            displacement = sign_extend(word & 0xFFFC, 16)
            target = displacement if word & 2 else source + displacement
            if not STATE0_STUB <= target < end:
                raise AssertionError(f"external conditional branch at 0x{source:08X}")
    if external_calls.count(ORIGINAL_STATE0) != 1:
        raise AssertionError("stub must call original state0 exactly once")
    if external_calls.count(ORIGINAL_STATE2) != 1:
        raise AssertionError("stub must call native state2 exactly once")
    if len(external_calls) != 2:
        raise AssertionError(f"unexpected external calls: {external_calls}")
    if state0.count(insn(lwarx(10, 0, 11))) != 1:
        raise AssertionError("stub must contain one lwarx claim")
    if state0.count(insn(stwcx(10, 0, 11))) != 1:
        raise AssertionError("stub must contain one stwcx. claim")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    status = u32(raw, J_STATUS)
    one = u32(raw, J_ONESHOT)
    print(f"journal magic       = 0x{u32(raw, J_MAGIC):08X}")
    print(f"journal version     = {u32(raw, J_VERSION)}")
    one_names = ("idle", "inside-state2", "completed", "terminal-failed")
    print(f"one-shot            = {one} ({one_names[one] if one < len(one_names) else 'unexpected'})")
    print(f"status              = {status} ({STATUS_NAMES.get(status, 'unexpected')})")
    print(f"state1 callbacks    = {u32(raw, J_STATE1_COUNT)}")
    print(f"state0 callbacks    = {u32(raw, J_STATE0_COUNT)}")
    print(f"state2 relays       = {u32(raw, J_STATE2_COUNT)}")
    print(f"synthetic state1    = {u32(raw, J_SYNTH_STATE1_COUNT)}")
    print(f"native state0       = {u32(raw, J_FALLBACK_COUNT)}")
    print(f"guard pass/fail     = {u32(raw, J_GUARD_PASS)}/{u32(raw, J_GUARD_FAIL)}")
    print(f"listener/reason     = 0x{u32(raw, J_LISTENER):08X}/0x{u32(raw, J_REASON):08X}")
    print(f"state before/after2 = {u32(raw, J_BEFORE_STATE)}/{u32(raw, J_AFTER_STATE2)}")
    print(f"expected listener   = 0x{u32(raw, J_EXPECTED_LISTENER):08X}")
    print(f"Cards root          = 0x{u32(raw, J_ROOT):08X}")
    print(f"root vtable/target  = 0x{u32(raw, J_ROOT_VTABLE):08X}/0x{u32(raw, J_ROOT_TARGET):08X}")
    print(f"root +0x80          = 0x{u32(raw, J_ROOT_STATE):08X}")
    print("+C fields           = " + " ".join(f"0x{u32(raw, x):08X}" for x in J_C_FIELDS))
    print("+8 prerequisites    = " + " ".join(f"0x{u32(raw, x):08X}" for x in J_PREINIT))
    print("host globals        = " + " ".join(f"0x{u32(raw, x):08X}" for x in J_HOSTS))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    # This catches layout/branch-range regressions without needing a live
    # receiver.  Apply rebuilds the image with the discovered exact instance.
    validate_layout(build_state0_stub(0xBD2B3C08))

    client = Xbdm(args.host)
    try:
        verify_module(client)
        values = {
            STATE1_SLOT: u32(client.read(STATE1_SLOT, 4)),
            STATE0_SLOT: u32(client.read(STATE0_SLOT, 4)),
            STATE2_SLOT: u32(client.read(STATE2_SLOT, 4)),
        }
        state1_state = "original" if values[STATE1_SLOT] == ORIGINAL_STATE1 else f"unexpected:0x{values[STATE1_SLOT]:08X}"
        state0_state = pointer_state(values[STATE0_SLOT], ORIGINAL_STATE0, STATE0_STUB)
        print(f"state1 pointer = {state1_state}")
        print(f"state0 pointer = {state0_state}")
        print(f"state2 pointer = 0x{values[STATE2_SLOT]:08X}")
        if args.action in ("status", "read"):
            describe(client)
            return 0

        if args.action == "restore":
            # Disarm our exact pointer before reporting unrelated hooks.  A
            # foreign value in any other vslot must never prevent recovery of
            # the pointer owned by this script.
            if state0_state == "relay":
                restore_slot_resilient(args.host, client)
                state0_state = "original"
            problems: list[str] = []
            if state0_state != "original":
                problems.append(f"foreign state0 slot left untouched ({state0_state})")
            if state1_state != "original":
                problems.append(f"foreign state1 slot left untouched ({state1_state})")
            if values[STATE2_SLOT] != ORIGINAL_STATE2:
                problems.append(
                    "foreign state2 slot left untouched "
                    f"(0x{values[STATE2_SLOT]:08X})"
                )
            if problems:
                raise RuntimeError(
                    "Owned state0 relay restored if present; " + "; ".join(problems)
                )
            print("Verified: owned state0 pointer is original; state1/state2 are native.")
            return 0

        if state1_state != "original" or state0_state.startswith("unexpected") or values[STATE2_SLOT] != ORIGINAL_STATE2:
            raise RuntimeError("Unexpected listener vtable; refusing to modify it")

        # Require the exact Cards build before placing code that references it.
        pow_module = next((line for line in client.multiline("modules") if 'name="powdllzf.xex.dll"' in line.lower()), None)
        if (
            pow_module is None
            or "base=0x89700000" not in pow_module.lower()
            or "size=0x00150000" not in pow_module.lower()
            or "timestamp=0x534c9611" not in pow_module.lower()
        ):
            raise RuntimeError(f"Unexpected or missing powdllzf: {pow_module}")

        listener, observer_count = discover_listener(client)
        print(f"network listener = 0x{listener:08X} ({observer_count} observers)")
        state0 = build_state0_stub(listener)
        validate_layout(state0)
        state0_image = state0.ljust(STATE0_END - STATE0_STUB, b"\0")

        cave0 = client.read(STATE0_STUB, len(state0_image))
        valid0 = cave0 in (bytes(len(cave0)), state0_image)
        if not valid0:
            # A pointer to a nonmatching image is immediately unsafe.  Roll
            # back only pointers that are exactly ours, then stop.
            if state0_state == "relay":
                restore_slot_resilient(args.host, client)
            raise RuntimeError("Relay cave occupied/mismatched; owned pointers restored")

        already = cave0 == state0_image
        if already and state0_state == "relay":
            journal = client.read(JOURNAL, JOURNAL_SIZE)
            if u32(journal, J_MAGIC) != MAGIC or u32(journal, J_VERSION) != VERSION:
                restore_slot_resilient(args.host, client)
                raise RuntimeError("Relay journal invalid; pointer restored")
            print("Already armed; journal preserved.")
            return 0

        # If a pointer survived a partial/failed code write, first remove the
        # dangling reference.  The clean transaction below writes code first.
        if state0_state == "relay" and not already:
            restore_slot_resilient(args.host, client)
            state0_state = "original"

        patch_maybe_live = False
        try:
            current_journal = client.read(JOURNAL, JOURNAL_SIZE)
            own_journal = (
                u32(current_journal, J_MAGIC) == MAGIC
                and u32(current_journal, J_VERSION) == VERSION
            )
            if current_journal != bytes(JOURNAL_SIZE) and not own_journal:
                raise RuntimeError("Relay journal region is occupied")
            if own_journal and u32(current_journal, J_ONESHOT) == 1:
                raise RuntimeError(
                    "Refusing to re-arm while the previous state2 relay is in progress"
                )
            # Applying from an original pointer is an explicit re-arm, so the
            # old one-shot state is reset even when the code image is reused.
            journal = bytearray(JOURNAL_SIZE)
            journal[J_MAGIC:J_MAGIC + 4] = MAGIC.to_bytes(4, "big")
            journal[J_VERSION:J_VERSION + 4] = VERSION.to_bytes(4, "big")
            journal[J_EXPECTED_LISTENER:J_EXPECTED_LISTENER + 4] = listener.to_bytes(4, "big")
            write_chunks(client, JOURNAL, bytes(journal))
            if not already:
                write_chunks(client, STATE0_STUB, state0_image)
            if client.read(STATE0_STUB, len(state0_image)) != state0_image:
                raise RuntimeError("Relay stub verification failed")
            # Set this before setmem: XBDM can apply a write and then lose its
            # acknowledgement, leaving this socket unusable but the patch live.
            patch_maybe_live = True
            client.write(STATE0_SLOT, STATE0_STUB.to_bytes(4, "big"))
            if u32(client.read(STATE1_SLOT, 4)) != ORIGINAL_STATE1 or u32(client.read(STATE0_SLOT, 4)) != STATE0_STUB:
                raise RuntimeError("Relay pointer verification failed")
            if u32(client.read(STATE2_SLOT, 4)) != ORIGINAL_STATE2:
                raise RuntimeError("Native state2 pointer changed during apply")
            patch_maybe_live = False
        except BaseException:
            if patch_maybe_live:
                restore_slot_resilient(args.host, client)
            raise
        print("Verified: guarded natural-thread state2 relay armed.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
