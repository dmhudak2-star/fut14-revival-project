#!/usr/bin/env python3
"""Passively journal FIFA 14's Lua file-loader paths.

The retail UXLua bootstrap exposes ``fileexists`` and ``loadfileasync``.  An
external navigation flow is not considered loaded merely because its name is
present in the parent ``.nav`` JSON, so this trace records the concrete paths
that reach the native file layer.  It neither invokes a loader nor changes a
file-existence or asynchronous completion result.

The registration table in this Xbox build resolves to these native entries::

    fileexists    -> 0x83728A90
    loadfileasync -> 0x837294A8 (tail branch to 0x83729388)

The probes sit immediately after each ``lua_tolstring`` return, plus the
loadfileasync request constructor.  At those points the path is already a
plain C string in r3/r6 and can be copied without calling back into Lua.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    cmplwi,
    conditional_branch,
    insn,
    lbz,
    or_register,
    stb,
    stw,
    verify_module,
    write_chunks,
)


# This trace owns the same diagnostic page used by the older ION action and
# UXLua-error probes.  The early watcher rejects those combinations.
STUB_BASE = 0x83C8A000
STUB_STRIDE = 0xA0
JOURNAL = 0x83C8A200
RECORD_SIZE = 0x80
PATH_OFFSET = 0x20
PATH_CAPACITY = RECORD_SIZE - PATH_OFFSET
CAVE_END = JOURNAL + 3 * RECORD_SIZE
PAGE_END = 0x83C8AA00


@dataclass(frozen=True)
class Probe:
    event_id: int
    name: str
    site: int
    original: bytes
    path_register: int

    @property
    def stub(self) -> int:
        return STUB_BASE + (self.event_id - 1) * STUB_STRIDE

    @property
    def record(self) -> int:
        return JOURNAL + (self.event_id - 1) * RECORD_SIZE


PROBES = (
    # lua_tolstring(L, -1, NULL) has returned the fileexists path in r3.
    Probe(1, "fileexists_path", 0x83728AF4, bytes.fromhex("817F0004"), 3),
    # lua_tolstring(L, 2, NULL) has returned the loadfileasync path in r3.
    Probe(2, "loadfileasync_path", 0x83729404, bytes.fromhex("817E0000"), 3),
    # Constructor arguments: r6=path, r7=callback reference, r8=async flag.
    Probe(3, "loadfileasync_request", 0x83729200, bytes.fromhex("7D8802A6"), 6),
)


def patch_for(probe: Probe) -> bytes:
    return insn(branch(probe.site, probe.stub, False))


def build_stub(probe: Probe) -> bytes:
    # r9/r10/r11 and r0 are volatile at all three selected sites.  In
    # particular, r7/r8 must remain intact at the request constructor.
    words = [
            addis(10, 0, (probe.record + 0x8000) >> 16),
            addi(10, 10, probe.record & 0xFFFF),
            # Publish the counter only after the bounded string copy.
            0x816A0000,  # lwz r11,0(r10)
            addi(11, 11, 1),
            stw(3, 10, 0x04),
            stw(4, 10, 0x08),
            stw(5, 10, 0x0C),
            stw(6, 10, 0x10),
            stw(7, 10, 0x14),
            stw(8, 10, 0x18),
            or_register(9, probe.path_register, probe.path_register),
            cmplwi(9, 0),
            0,  # beq copy_done
            addi(0, 0, PATH_CAPACITY - 1),
            0x7C0903A6,  # mtctr r0
            addi(10, 10, PATH_OFFSET),
    ]
    copy_loop = len(words)
    words.extend(
        (
            lbz(0, 9, 0),
            stb(0, 10, 0),
            cmplwi(0, 0),
            0,  # beq copy_done
            addi(9, 9, 1),
            addi(10, 10, 1),
            0,  # bdnz copy_loop
        )
    )
    copy_done = len(words)
    # Re-materialise the record address because r10 advanced during copying.
    words.extend(
        (
            addis(10, 0, (probe.record + 0x8000) >> 16),
            addi(10, 10, probe.record & 0xFFFF),
            0x7C0004AC,  # sync
            stw(11, 10, 0x00),
            int.from_bytes(probe.original, "big"),
            0,
        )
    )
    null_branch = 12
    byte_zero_branch = copy_loop + 3
    loop_branch = copy_loop + 6
    words[null_branch] = conditional_branch(
        probe.stub + null_branch * 4,
        probe.stub + copy_done * 4,
        12,
        2,
    )  # beq
    words[byte_zero_branch] = conditional_branch(
        probe.stub + byte_zero_branch * 4,
        probe.stub + copy_done * 4,
        12,
        2,
    )  # beq
    words[loop_branch] = conditional_branch(
        probe.stub + loop_branch * 4,
        probe.stub + copy_loop * 4,
        16,
        0,
    )  # bdnz
    tail = len(words) - 1
    words[tail] = branch(probe.stub + tail * 4, probe.site + 4, False)
    raw = b"".join(insn(word) for word in words)
    if len(raw) > STUB_STRIDE:
        raise RuntimeError(f"{probe.name} stub exceeds its slot")
    return raw.ljust(STUB_STRIDE, b"\0")


def verify_layout() -> None:
    if STUB_BASE + len(PROBES) * STUB_STRIDE > JOURNAL:
        raise RuntimeError("Lua file-loader stubs overlap the journal")
    if CAVE_END > PAGE_END:
        raise RuntimeError("Lua file-loader trace exceeds its diagnostic page")


def state_for(client: Xbdm, probe: Probe) -> str:
    current = client.read(probe.site, 4)
    if current == probe.original:
        return "original"
    if current == patch_for(probe):
        return "traced"
    return f"unexpected:{current.hex().upper()}"


def arm(client: Xbdm) -> None:
    verify_module(client)
    verify_layout()
    states = [state_for(client, probe) for probe in PROBES]
    bad = [
        f"0x{probe.site:08X}={state}"
        for probe, state in zip(PROBES, states)
        if state not in ("original", "traced")
    ]
    if bad:
        raise RuntimeError("refusing unknown Lua loader site(s): " + ", ".join(bad))

    images = [build_stub(probe) for probe in PROBES]
    for probe, image in zip(PROBES, images):
        cave = client.read(probe.stub, STUB_STRIDE)
        if cave not in (bytes(STUB_STRIDE), image):
            raise RuntimeError(f"Lua loader cave 0x{probe.stub:08X} is occupied")

    if "traced" in states:
        for probe, state in zip(PROBES, states):
            if state == "traced":
                client.write(probe.site, probe.original)
        time.sleep(0.02)
    try:
        write_chunks(client, JOURNAL, bytes(CAVE_END - JOURNAL))
        for probe, image in zip(PROBES, images):
            write_chunks(client, probe.stub, image)
        for probe in PROBES:
            client.write(probe.site, patch_for(probe))
        if any(state_for(client, probe) != "traced" for probe in PROBES):
            raise RuntimeError("one or more Lua loader probes did not publish")
    except Exception:
        for probe in PROBES:
            try:
                if state_for(client, probe) == "traced":
                    client.write(probe.site, probe.original)
            except Exception:
                pass
        raise


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def describe(client: Xbdm) -> None:
    raw = client.read(JOURNAL, CAVE_END - JOURNAL)
    for probe in PROBES:
        offset = (probe.event_id - 1) * RECORD_SIZE
        record = raw[offset : offset + RECORD_SIZE]
        path = record[PATH_OFFSET:].split(b"\0", 1)[0]
        rendered = path.decode("utf-8", "backslashreplace") if path else ""
        print(
            f"{probe.name:24s} hits={u32(record, 0x00):5d} "
            f"path={rendered!r} ptr=0x{u32(record, 0x04 + (probe.path_register - 3) * 4):08X}"
        )
        print(
            "  " + " ".join(
                f"r{register}=0x{u32(record, 0x04 + (register - 3) * 4):08X}"
                for register in range(3, 9)
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        verify_layout()
        states = [state_for(client, probe) for probe in PROBES]
        print(
            "Lua file-loader trace: "
            f"{states.count('traced')} traced, "
            f"{states.count('original')} original, "
            f"{sum(state.startswith('unexpected:') for state in states)} unexpected"
        )
        if args.action == "status":
            for probe, state in zip(PROBES, states):
                print(f"  0x{probe.site:08X} {probe.name}: {state}")
            return 0
        if args.action == "read":
            describe(client)
            return 0
        if args.action == "restore":
            for probe, state in zip(PROBES, states):
                if state == "traced":
                    client.write(probe.site, probe.original)
                elif state != "original":
                    raise RuntimeError(f"unexpected entry at 0x{probe.site:08X}: {state}")
            if any(state_for(client, probe) != "original" for probe in PROBES):
                raise RuntimeError("one or more Lua loader entries did not restore")
            print("Verified: Lua file-loader entries restored.")
            return 0

        arm(client)
        print("Verified: passive Lua file-loader trace armed.")
        print("No file, result, completion, event or frontend route was synthesized.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
