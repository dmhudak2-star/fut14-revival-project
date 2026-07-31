#!/usr/bin/env python3
"""Call CardsDLL root vtable[3] once from either EnterFUT UI path.

The wrapper preserves the callsite ABI, conditionally issues the verified
virtual call, always executes the displaced FUT-manager getter, and returns
to 0x82835244.  It is a volatile, reversible live patch.
"""

from __future__ import annotations

import argparse

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


SITE = 0x82835240
FAST_SITE = 0x828352F4
ORIGINAL_TARGET = 0x827C6370
ORIGINAL = bytes.fromhex("4BF91131")  # bl 0x827C6370
FAST_ORIGINAL = bytes.fromhex("4BF9107D")  # bl 0x827C6370
CALLSITES = ((SITE, ORIGINAL), (FAST_SITE, FAST_ORIGINAL))

STUB = 0x83C8B000
STUB_SLOT_END = 0x83C8B300
JOURNAL = 0x83C8B300
JOURNAL_SIZE = 0x68
NEXT_KNOWN_CAVE = 0x83C8C000

ROOT_GLOBAL = 0x897C3608
ROOT_INITIALIZE = 0x89748A38
HOST_GLOBALS = (0x897C33A0, 0x897C3370, 0x897C339C, 0x897C33CC)

ROOT_STATE = 0x0080
ROOT_C_OBJECTS = (0x3A08, 0x3A0C, 0x3A10, 0x3A14)
ROOT_PREINIT_OBJECTS = (0x3A44, 0x3A48, 0x3AF8, 0x3A4C)

B0C_LISTENER = 0xBD2DC740
B0C_SIGNAL_OFFSETS = (0x6F8, 0x778, 0x7F8)
CONNECTED_CALLBACK = 0x8251A560
MODULE_MANAGER_STATE = 0x0974

# Journal layout.
J_INVOCATIONS = 0x00
J_ONESHOT_STATE = 0x04  # 0=idle, 1=in progress, 2=completed
J_STATUS = 0x08
J_ROOT = 0x0C
J_BEFORE_STATE = 0x10
J_BEFORE_C = (0x14, 0x18, 0x1C, 0x20)
J_PREINIT = (0x24, 0x28, 0x2C, 0x30)
J_VTABLE = 0x34
J_TARGET = 0x38
J_CALLBACK_RESULT = 0x3C
J_AFTER_STATE = 0x40
J_AFTER_C = (0x44, 0x48, 0x4C, 0x50)
J_CALLBACK_COUNT = 0x54
J_ORIGINAL_RESULT = 0x58
J_MANAGER = 0x5C
J_MANAGER_STATE = 0x60

STATUS_NAMES = {
    0: "idle",
    1: "root-null",
    2: "root+0x80-nonzero",
    3: "root+0x3A08-nonzero",
    4: "root+0x3A0C-nonzero",
    5: "root+0x3A10-nonzero",
    6: "root+0x3A14-nonzero",
    7: "root+0x3A44-null",
    8: "root+0x3A48-null",
    9: "root+0x3AF8-null",
    10: "root+0x3A4C-null",
    11: "vtable-null",
    12: "vtable[3]-unexpected",
    13: "calling",
    14: "completed",
}

# 0x00..0x6F is outbound-call/linkage space.  Private saves start at 0x70.
FRAME_SIZE = 0xC0
FRAME_ARGS = 0x70
FRAME_ROOT = 0xB0

MFLR_R0 = 0x7C0802A6
MTLR_R0 = 0x7C0803A6
MTCTR_R11 = 0x7D6903A6
BCTRL = 0x4E800421
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


def load_address(rt: int, address: int) -> tuple[int, int]:
    """Return a high-adjusted lis/addi pair for a 32-bit address."""
    high_adjusted = ((address + 0x8000) >> 16) & 0xFFFF
    low_signed = address & 0xFFFF
    if low_signed & 0x8000:
        low_signed -= 0x10000
    return addis(rt, 0, high_adjusted), addi(rt, rt, low_signed)


def build_stub(receiver: int) -> bytes:
    words: list[int] = []
    labels: dict[str, int] = {}
    fixups: list[tuple[int, str, int | None, int | None]] = []

    def emit(*values: int) -> None:
        words.extend(values)

    def mark(name: str) -> None:
        if name in labels:
            raise AssertionError(f"duplicate label: {name}")
        labels[name] = len(words)

    def jump(label: str) -> None:
        fixups.append((len(words), label, None, None))
        words.append(0)

    def condition(label: str, branch_option: int, condition_bit: int) -> None:
        fixups.append((len(words), label, branch_option, condition_bit))
        words.append(0)

    # SITE is patched with bl STUB, so LR initially contains SITE+4.  Save it
    # and all eight volatile argument registers before the optional callback.
    emit(MFLR_R0, stw(0, 1, -8), stwu(1, 1, -FRAME_SIZE))
    for register in range(3, 11):
        emit(std(register, 1, FRAME_ARGS + (register - 3) * 8))

    emit(*load_address(12, JOURNAL))
    emit(
        lwz(11, 12, J_INVOCATIONS),
        addi(11, 11, 1),
        stw(11, 12, J_INVOCATIONS),
        lwz(10, 12, J_ONESHOT_STATE),
        cmpwi(10, 0),
    )
    condition("original_path", 4, 2)  # bne: running or completed

    emit(*load_address(11, ROOT_GLOBAL))
    emit(
        lwz(11, 11, 0),
        stw(11, 1, FRAME_ROOT),
        stw(11, 12, J_ROOT),
        cmpwi(11, 0),
    )
    condition("fail_root", 12, 2)

    emit(
        lwz(10, 11, ROOT_STATE),
        stw(10, 12, J_BEFORE_STATE),
        cmpwi(10, 0),
    )
    condition("fail_state", 4, 2)

    # The +C lifecycle must be wholly pristine, not merely missing auth.
    for index, offset in enumerate(ROOT_C_OBJECTS):
        emit(
            lwz(10, 11, offset),
            stw(10, 12, J_BEFORE_C[index]),
            cmpwi(10, 0),
        )
        condition(f"fail_c_{index}", 4, 2)

    # Every prerequisite produced by the earlier +8 lifecycle must exist.
    for index, offset in enumerate(ROOT_PREINIT_OBJECTS):
        emit(
            lwz(10, 11, offset),
            stw(10, 12, J_PREINIT[index]),
            cmpwi(10, 0),
        )
        condition(f"fail_pre_{index}", 12, 2)

    # Snapshot the OSDK state for diagnostics.  It is intentionally not a
    # guard: root->vtable[3] does not consume ModuleManager/Blaze state, and
    # the failed online attempt can legitimately leave this value at zero.
    emit(
        *load_address(10, receiver),
        stw(10, 12, J_MANAGER),
        lwz(10, 10, MODULE_MANAGER_STATE),
        stw(10, 12, J_MANAGER_STATE),
    )

    emit(
        lwz(10, 11, 0),
        stw(10, 12, J_VTABLE),
        cmpwi(10, 0),
    )
    condition("fail_vtable", 12, 2)
    emit(
        lwz(11, 10, 0x0C),
        stw(11, 12, J_TARGET),
        *load_address(10, ROOT_INITIALIZE),
        cmpw(11, 10),
    )
    condition("fail_target", 4, 2)

    # Set the one-shot flag before bctrl.  The full barrier makes a recursive
    # or cross-core observer see it before any initializer code can execute.
    emit(
        addi(10, 0, 13),
        stw(10, 12, J_STATUS),
        lwz(10, 12, J_CALLBACK_COUNT),
        addi(10, 10, 1),
        stw(10, 12, J_CALLBACK_COUNT),
        addi(10, 0, 1),
        stw(10, 12, J_ONESHOT_STATE),
        SYNC,
        lwz(3, 1, FRAME_ROOT),
        MTCTR_R11,
        BCTRL,
        *load_address(12, JOURNAL),
        stw(3, 12, J_CALLBACK_RESULT),
        lwz(11, 1, FRAME_ROOT),
        lwz(10, 11, ROOT_STATE),
        stw(10, 12, J_AFTER_STATE),
    )
    for index, offset in enumerate(ROOT_C_OBJECTS):
        emit(
            lwz(10, 11, offset),
            stw(10, 12, J_AFTER_C[index]),
        )
    emit(
        addi(10, 0, 14),
        stw(10, 12, J_STATUS),
        addi(10, 0, 2),
        SYNC,
        stw(10, 12, J_ONESHOT_STATE),
    )
    jump("original_path")

    failures = (
        ("fail_root", 1),
        ("fail_state", 2),
        ("fail_c_0", 3),
        ("fail_c_1", 4),
        ("fail_c_2", 5),
        ("fail_c_3", 6),
        ("fail_pre_0", 7),
        ("fail_pre_1", 8),
        ("fail_pre_2", 9),
        ("fail_pre_3", 10),
        ("fail_vtable", 11),
        ("fail_target", 12),
    )
    for label, status in failures:
        mark(label)
        emit(addi(10, 0, status))
        jump("record_failure")

    mark("record_failure")
    emit(stw(10, 12, J_STATUS))
    jump("original_path")

    mark("original_path")
    for register in range(3, 11):
        emit(ld(register, 1, FRAME_ARGS + (register - 3) * 8))
    original_call = len(words)
    emit(0)
    emit(
        *load_address(12, JOURNAL),
        stw(3, 12, J_ORIGINAL_RESULT),
        addi(1, 1, FRAME_SIZE),
        lwz(0, 1, -8),
        MTLR_R0,
        BLR,
    )

    def address(index: int) -> int:
        return STUB + index * 4

    for index, label, option, bit in fixups:
        if label not in labels:
            raise AssertionError(f"undefined label: {label}")
        target = address(labels[label])
        words[index] = (
            branch(address(index), target, False)
            if option is None
            else conditional_branch(address(index), target, option, bit or 0)
        )
    words[original_call] = branch(
        address(original_call), ORIGINAL_TARGET, True
    )
    return b"".join(insn(word) for word in words)


def validate_layout(stub: bytes) -> None:
    for site, original in CALLSITES:
        if insn(branch(site, ORIGINAL_TARGET, True)) != original:
            raise AssertionError(
                f"original opcode at 0x{site:08X} does not encode the getter"
            )
    if STUB + len(stub) > STUB_SLOT_END:
        raise AssertionError(
            f"stub ends at 0x{STUB + len(stub):08X}, beyond its slot"
        )
    if STUB_SLOT_END > JOURNAL:
        raise AssertionError("stub slot overlaps journal")
    if JOURNAL + JOURNAL_SIZE > NEXT_KNOWN_CAVE:
        raise AssertionError("B000 allocation overlaps the C000 neighbour")


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    state = u32(raw, J_ONESHOT_STATE)
    status = u32(raw, J_STATUS)
    state_name = ("idle", "calling", "completed")[state] if state < 3 else "unexpected"
    print(f"invocations       = {u32(raw, J_INVOCATIONS)}")
    print(f"oneshot_state     = {state} ({state_name})")
    print(f"guard_status      = {status} ({STATUS_NAMES.get(status, 'unexpected')})")
    print(f"callback_count    = {u32(raw, J_CALLBACK_COUNT)}")
    print(f"root              = 0x{u32(raw, J_ROOT):08X}")
    print(f"vtable            = 0x{u32(raw, J_VTABLE):08X}")
    print(f"vtable[3]         = 0x{u32(raw, J_TARGET):08X}")
    print(f"before +0x80      = 0x{u32(raw, J_BEFORE_STATE):08X}")
    for index, offset in enumerate(ROOT_C_OBJECTS):
        print(f"before +0x{offset:X}    = 0x{u32(raw, J_BEFORE_C[index]):08X}")
    for index, offset in enumerate(ROOT_PREINIT_OBJECTS):
        print(f"preinit +0x{offset:X}   = 0x{u32(raw, J_PREINIT[index]):08X}")
    print(f"after  +0x80      = 0x{u32(raw, J_AFTER_STATE):08X}")
    for index, offset in enumerate(ROOT_C_OBJECTS):
        print(f"after  +0x{offset:X}    = 0x{u32(raw, J_AFTER_C[index]):08X}")
    print(f"callback r3       = 0x{u32(raw, J_CALLBACK_RESULT):08X}")
    print(f"original getter r3 = 0x{u32(raw, J_ORIGINAL_RESULT):08X}")
    print(f"ModuleManager     = 0x{u32(raw, J_MANAGER):08X}")
    print(f"manager state     = {u32(raw, J_MANAGER_STATE)}")


def discover_connected_receiver(client: Xbdm) -> int:
    matches: list[int] = []
    for signal_offset in B0C_SIGNAL_OFFSETS:
        signal = B0C_LISTENER + signal_offset
        begin = u32(client.read(signal + 4, 4), 0)
        end = u32(client.read(signal + 8, 4), 0)
        if end < begin or (end - begin) % 4 or end - begin > 0x400:
            continue
        for slot in range(begin, end, 4):
            receiver = u32(client.read(slot, 4), 0)
            if not receiver:
                continue
            try:
                vtable = u32(client.read(receiver, 4), 0)
                callback = u32(client.read(vtable + 4, 4), 0)
            except Exception:
                continue
            if callback == CONNECTED_CALLBACK and receiver not in matches:
                matches.append(receiver)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one connected ModuleManager receiver, found {len(matches)}"
        )
    return matches[0]


def site_state(current: bytes, original: bytes, patch: bytes) -> str:
    if current == original:
        return "original"
    if current == patch:
        return "patched-v2"
    return f"unexpected:{current.hex().upper()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        patches = {
            site: insn(branch(site, STUB, True)) for site, _ in CALLSITES
        }
        states: dict[int, str] = {}
        for site, original in CALLSITES:
            current = client.read(site, 4)
            states[site] = site_state(current, original, patches[site])
            print(f"Cards UI callsite 0x{site:08X}: {states[site]}")

        if args.action in ("status", "read"):
            describe(client)
            return 0
        if any(
            state not in ("original", "patched-v2")
            for state in states.values()
        ):
            raise RuntimeError("Unexpected EnterFUT callsite instruction")

        if args.action == "restore":
            for site, original in CALLSITES:
                if states[site] == "patched-v2":
                    client.write(site, original)
            for site, original in CALLSITES:
                if client.read(site, 4) != original:
                    raise RuntimeError(
                        f"EnterFUT callsite 0x{site:08X} restore failed"
                    )
            print("Verified: both original EnterFUT getter calls restored.")
            return 0

        pow_module = next(
            (
                line
                for line in client.multiline("modules")
                if 'name="powdllzf.xex.dll"' in line.lower()
            ),
            None,
        )
        if pow_module is None or "base=0x89700000" not in pow_module.lower():
            raise RuntimeError(f"Unexpected or missing powdllzf module: {pow_module}")
        receiver = discover_connected_receiver(client)
        receiver_state = u32(client.read(receiver + MODULE_MANAGER_STATE, 4), 0)
        print(f"ModuleManager receiver=0x{receiver:08X} state={receiver_state}")
        host_values = {
            address: u32(client.read(address, 4), 0) for address in HOST_GLOBALS
        }
        for address, value in host_values.items():
            print(f"Cards host 0x{address:08X}=0x{value:08X}")
        if any(value == 0 for value in host_values.values()):
            raise RuntimeError("A required CardsDLL host global is null")

        stub = build_stub(receiver)
        validate_layout(stub)
        stub_image = stub.ljust(STUB_SLOT_END - STUB, b"\0")

        cave = client.read(STUB, len(stub_image))
        journal = client.read(JOURNAL, JOURNAL_SIZE)
        if cave not in (bytes(len(stub_image)), stub_image):
            raise RuntimeError("Cards UI init-once v2 cave is occupied")
        if cave == bytes(len(stub_image)) and journal != bytes(JOURNAL_SIZE):
            raise RuntimeError("Cards UI init-once v2 journal is occupied")
        if all(state == "patched-v2" for state in states.values()):
            if cave != stub_image:
                raise RuntimeError("Live Cards UI init-once v2 stub does not match")
            print("Already armed; one-shot state and journal preserved.")
            return 0

        try:
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            write_chunks(client, STUB, stub_image)
            if client.read(STUB, len(stub_image)) != stub_image:
                raise RuntimeError("Cards UI init-once v2 stub verification failed")
            for site, _ in CALLSITES:
                client.write(site, patches[site])
            for site, _ in CALLSITES:
                if client.read(site, 4) != patches[site]:
                    raise RuntimeError(
                        f"Cards UI callsite 0x{site:08X} verification failed"
                    )
        except Exception:
            try:
                for site, original in CALLSITES:
                    if client.read(site, 4) == patches[site]:
                        client.write(site, original)
            except Exception:
                pass
            raise

        print("Verified: guarded one-shot armed on both EnterFUT paths.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
