#!/usr/bin/env python3
"""Passively journal FIFA 14's native FUT auth/config completion path.

This trace never changes a return value, argument, state flag, event, or
frontend route.  Each entry trampoline records the ABI arguments and selected
fields from the retail FUT adapter, executes the displaced retail instruction,
and resumes at the following instruction.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    lbz,
    lwz,
    stw,
    verify_module,
    write_chunks,
)


STUB_BASE = 0x83C87000
STUB_STRIDE = 0x80
JOURNAL = 0x83C87800
RECORD_SIZE = 0x50


@dataclass(frozen=True)
class Probe:
    name: str
    site: int
    original_hex: str
    object_kind: str = "adapter"

    @property
    def original(self) -> bytes:
        return bytes.fromhex(self.original_hex)


PROBES = (
    Probe("fut_status_evaluator", 0x82782028, "8943014D"),
    Probe("fut_auth_start", 0x82782078, "7D8802A6"),
    Probe("fut_async_dispatch", 0x827E6870, "7D8802A6", "event"),
    Probe("fut_auth_state_1_to_2", 0x827E67E0, "7D8802A6"),
    Probe("fut_direct_event_wrapper", 0x82805C88, "7D8802A6", "event"),
    Probe("fut_config_download_start", 0x82798A68, "7D8802A6"),
    Probe("fut_config_download_callback", 0x827DF380, "7D8802A6", "callback"),
    Probe("enterfut2_wrapper", 0x82DA6850, "7D8802A6", "event"),
    Probe("enterfut2_handler", 0x828350C8, "7D8802A6", "event"),
    Probe("ion_send_nav_event", 0x82805C10, "7D8802A6", "event"),
    Probe("ion_flow_action_dispatch", 0x83622D20, "7D8802A6", "event"),
    # Deeper stock ION routes.  Normal retail navigation can bypass the
    # convenience wrapper above and call the core sender or another overload
    # of the flow-action dispatcher directly.
    Probe("ion_core_send_event", 0x8288D9F0, "7D8802A6", "event"),
    Probe("ion_dispatch_v18", 0x83622C38, "7D8802A6", "event"),
    Probe("ion_dispatch_v1c", 0x83622CB8, "7D8802A6", "event"),
    # The retail main-menu SWF enters the native navigation graph through
    # ScreenController::HandleScreenEvent.  Its ABI is
    # (controller, screen-id, event-name, payload), so r5 identifies whether
    # the tile really submitted LaunchFUT before any flow action runs.
    Probe("screen_controller_handle_event", 0x82B00198, "7D8802A6", "event"),
    # Retail mainfeflow.nav enters futLauncher and executes the built-in
    # sendScreenEvent action with FUTStartUp.  This action handler is separate
    # from SendNavEvent and the flow-action dispatchers above.  Recording its
    # ABI arguments proves whether the native launcher really publishes the
    # screen event, without synthesising it or changing its result.
    Probe("ion_send_screen_event_action", 0x8288BF68, "7D8802A6", "event"),
)


def stub_address(index: int) -> int:
    return STUB_BASE + index * STUB_STRIDE


def site_patch(index: int, probe: Probe) -> bytes:
    return insn(branch(probe.site, stub_address(index), False))


def build_stub(index: int, probe: Probe) -> bytes:
    """Build an entry logger while preserving the retail first instruction."""
    stub = stub_address(index)
    record = index * RECORD_SIZE
    original = int.from_bytes(probe.original, "big")

    # mflr r12 is part of the normal function prologue.  Execute it before the
    # logger so r12 retains exactly the value expected by the next instruction.
    words: list[int] = []
    original_is_mflr = original == 0x7D8802A6
    if original_is_mflr:
        words.append(original)

    words.extend(
        (
            addis(11, 0, 0x83C9),
            addi(11, 11, -0x8800),       # JOURNAL = 0x83C87800
            lwz(10, 11, record + 0x00),
            addi(10, 10, 1),
            stw(10, 11, record + 0x00),
            stw(3, 11, record + 0x04),
            stw(4, 11, record + 0x08),
            stw(5, 11, record + 0x0C),
            stw(6, 11, record + 0x10),
            stw(7, 11, record + 0x14),
        )
    )

    if original_is_mflr:
        words.append(stw(12, 11, record + 0x18))
    else:
        words.extend((0x7D4802A6, stw(10, 11, record + 0x18)))  # mflr r10

    # Most probes receive the FUT adapter directly in r3.  The download
    # callback receives its embedded callback object at adapter+0x1C0.
    if probe.object_kind == "callback":
        words.append(addi(9, 3, -0x1C0))
        object_register = 9
    elif probe.object_kind == "adapter":
        object_register = 3
    else:
        object_register = None

    if object_register is not None:
        words.extend(
            (
                stw(object_register, 11, record + 0x1C),
                lwz(10, object_register, 0x114),
                stw(10, 11, record + 0x20),
                lbz(10, object_register, 0x13C),
                stw(10, 11, record + 0x24),
                lbz(10, object_register, 0x14D),
                stw(10, 11, record + 0x28),
                lbz(10, object_register, 0x14E),
                stw(10, 11, record + 0x2C),
                lbz(10, object_register, 0x14F),
                stw(10, 11, record + 0x30),
                lbz(10, object_register, 0x152),
                stw(10, 11, record + 0x34),
                lbz(10, object_register, 0x1C4),
                stw(10, 11, record + 0x38),
                lwz(10, object_register, 0x1C8),
                stw(10, 11, record + 0x3C),
            )
        )

    if not original_is_mflr:
        words.append(original)
    tail = stub + len(words) * 4
    words.append(branch(tail, probe.site + 4, False))

    image = b"".join(insn(word) for word in words)
    if len(image) > STUB_STRIDE:
        raise AssertionError(f"{probe.name} trace exceeds its code-cave slot")
    return image.ljust(STUB_STRIDE, b"\0")


def probe_states(client: Xbdm) -> list[str]:
    states: list[str] = []
    for index, probe in enumerate(PROBES):
        current = client.read(probe.site, 4)
        if current == probe.original:
            states.append("original")
        elif current == site_patch(index, probe):
            states.append("armed")
        else:
            states.append(f"unexpected:{current.hex().upper()}")
    return states


def arm(client: Xbdm) -> None:
    states = probe_states(client)
    unexpected = [
        f"{probe.name}=0x{probe.site:08X}:{state}"
        for probe, state in zip(PROBES, states)
        if state.startswith("unexpected:")
    ]
    if unexpected:
        raise RuntimeError("Refusing unexpected trace sites: " + ", ".join(unexpected))

    # Support extending an already armed trace: every occupied slot must be
    # exactly our own image, while every newly used slot must still be zero.
    for index, (probe, state) in enumerate(zip(PROBES, states)):
        existing = client.read(stub_address(index), STUB_STRIDE)
        expected = build_stub(index, probe)
        if state == "armed" and existing != expected:
            raise RuntimeError(
                f"Owned trace image changed at 0x{stub_address(index):08X}"
            )
        if state == "original" and existing not in (bytes(STUB_STRIDE), expected):
            raise RuntimeError(
                f"The trace cave slot at 0x{stub_address(index):08X} is occupied"
            )

    # Clear records first, install all code images, then publish branch hooks.
    write_chunks(client, JOURNAL, bytes(len(PROBES) * RECORD_SIZE))
    for index, probe in enumerate(PROBES):
        write_chunks(client, stub_address(index), build_stub(index, probe))
    for index, probe in enumerate(PROBES):
        if states[index] == "original":
            client.write(probe.site, site_patch(index, probe))

    verified = probe_states(client)
    if any(state != "armed" for state in verified):
        raise RuntimeError("FUT auth completion trace verification failed")


def restore(client: Xbdm) -> None:
    states = probe_states(client)
    for probe, state in zip(PROBES, states):
        if state == "armed":
            client.write(probe.site, probe.original)
        elif state != "original":
            raise RuntimeError(f"Unexpected entry at 0x{probe.site:08X}: {state}")


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def safe_cstring(client: Xbdm, pointer: int, limit: int = 96) -> str | None:
    """Read a diagnostic C string without trusting an arbitrary ABI value."""
    if not 0x80000000 <= pointer < 0xD0000000:
        return None
    try:
        raw = client.read(pointer, limit)
    except Exception:
        return None
    raw = raw.split(b"\0", 1)[0]
    if not raw or any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None
    return raw.decode("ascii")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, len(PROBES) * RECORD_SIZE)
    hits = 0
    for index, probe in enumerate(PROBES):
        record = raw[index * RECORD_SIZE : (index + 1) * RECORD_SIZE]
        count = u32(record, 0x00)
        if not count:
            continue
        hits += 1
        print(
            f"{probe.name:30} hits={count} "
            f"r3=0x{u32(record, 0x04):08X} "
            f"r4=0x{u32(record, 0x08):08X} "
            f"r5=0x{u32(record, 0x0C):08X} "
            f"r6=0x{u32(record, 0x10):08X} "
            f"r7=0x{u32(record, 0x14):08X} "
            f"caller=0x{(u32(record, 0x18) - 4) & 0xFFFFFFFF:08X}"
        )
        if probe.object_kind != "event":
            print(
                f"  adapter=0x{u32(record, 0x1C):08X} "
                f"state114={u32(record, 0x20)} "
                f"flags(13c/14d/14e/14f/152)="
                f"{u32(record, 0x24)}/{u32(record, 0x28)}/"
                f"{u32(record, 0x2C)}/{u32(record, 0x30)}/{u32(record, 0x34)} "
                f"async(1c4/1c8)={u32(record, 0x38)}/{u32(record, 0x3C)}"
            )
        else:
            strings = []
            for register, offset in (("r4", 0x08), ("r5", 0x0C),
                                     ("r6", 0x10), ("r7", 0x14)):
                value = safe_cstring(client, u32(record, offset))
                if value is not None:
                    strings.append(f"{register}={value!r}")
            if strings:
                print("  strings: " + ", ".join(strings))
    if not hits:
        print("No native FUT auth/config completion probe was hit.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = probe_states(client)
        print(
            "FUT auth completion trace: "
            f"{states.count('armed')} armed, "
            f"{states.count('original')} original, "
            f"{sum(state.startswith('unexpected:') for state in states)} unexpected"
        )
        if args.action == "status":
            for probe, state in zip(PROBES, states):
                print(f"  0x{probe.site:08X} {probe.name}: {state}")
            return 0
        if args.action == "apply":
            arm(client)
            print("Verified: passive native FUT auth/config completion trace armed.")
            return 0
        if args.action == "read":
            describe(client)
            return 0
        restore(client)
        print("Verified: native FUT auth/config completion trace restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
