from __future__ import annotations

import struct
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_fut_notification_listener_trace as trace


def words(image: bytes) -> list[int]:
    return [
        struct.unpack_from(">I", image, offset)[0]
        for offset in range(0, len(image), 4)
    ]


def test_the_displaced_comparison_runs_last() -> None:
    # It sets cr6 for the retail branches that follow, so anything recording
    # after it could clobber the flags the bus is about to read.
    encoded = words(trace.build_stub())
    displaced = int.from_bytes(trace.ORIGINAL, "big")
    branch_index = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 18
    )
    assert encoded[branch_index - 1] == displaced


def test_stub_resumes_after_the_patched_site() -> None:
    encoded = words(trace.build_stub())
    tail = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 18
    )
    displacement = encoded[tail] & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.STUB + tail * 4 + displacement == trace.SITE + 4
    assert encoded[tail] & 1 == 0


def test_stub_uses_no_recording_forms() -> None:
    # A dot-form would set cr0, and worse, any comparison would disturb the
    # flags the displaced instruction is there to produce.
    for word in words(trace.build_stub()):
        if word == int.from_bytes(trace.ORIGINAL, "big"):
            continue
        opcode = word >> 26
        assert opcode not in (10, 11)  # cmpli / cmpi
        if opcode == 21:  # rlwinm
            assert word & 1 == 0


def test_stub_only_writes_scratch_registers() -> None:
    # r3 and r4 are the bus arguments; r10-r12 are free at this leaf entry.
    for word in words(trace.build_stub()):
        if word == int.from_bytes(trace.ORIGINAL, "big"):
            continue
        opcode = word >> 26
        if opcode in (14, 15, 32):
            assert (word >> 21) & 0x1F in (10, 11, 12)
        elif opcode == 21:
            assert (word >> 16) & 0x1F in (10, 11, 12)
        elif opcode == 31 and ((word >> 1) & 0x3FF) == 266:
            assert (word >> 21) & 0x1F in (10, 11, 12)


def test_site_patch_branches_into_the_stub() -> None:
    word = struct.unpack(">I", trace.site_patch())[0]
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.SITE + displacement == trace.STUB


def test_a_missing_listener_is_called_out() -> None:
    assert "no listener" in trace.describe(0xDF, trace.NO_LISTENER)
    assert "no listener" not in trace.describe(0xDF, 2)
    assert "FirstTimeInit" in trace.describe(0xDF, 0)
