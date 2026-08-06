from __future__ import annotations

import struct
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_fut_firsttimeinit_submit_trace as trace
import fifa14_fut_api_trace as api


def words(image: bytes) -> list[int]:
    return [
        struct.unpack_from(">I", image, offset)[0]
        for offset in range(0, len(image), 4)
    ]


def test_stub_performs_the_displaced_instruction_before_resuming() -> None:
    encoded = words(trace.build_stub())
    displaced = int.from_bytes(trace.ORIGINAL, "big")
    assert displaced in encoded
    tail = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 18
    )
    # The mtctr must run before the branch back, or bctrl jumps nowhere.
    assert encoded.index(displaced) < tail


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


def test_stub_leaves_the_call_registers_alone() -> None:
    # r3/r4 are the call's arguments and r11 the method the displaced mtctr
    # consumes, so only r10 and the dead r12 may be written.
    for word in words(trace.build_stub()):
        if word == int.from_bytes(trace.ORIGINAL, "big"):
            continue
        opcode = word >> 26
        if opcode in (14, 15, 32):
            assert (word >> 21) & 0x1F in (10, 12)
        elif opcode == 21:
            assert (word >> 16) & 0x1F in (10, 12)
        elif opcode == 31 and ((word >> 1) & 0x3FF) == 266:
            assert (word >> 21) & 0x1F in (10, 12)


def test_stub_fits_its_cave() -> None:
    assert len(trace.build_stub()) == trace.STUB_SIZE


def test_site_patch_branches_into_the_stub() -> None:
    word = struct.unpack(">I", trace.site_patch())[0]
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.SITE + displacement == trace.STUB


def test_cave_does_not_collide_with_the_api_tracer() -> None:
    ours = [(trace.STUB, trace.STUB_SIZE), (trace.JOURNAL, trace.JOURNAL_SIZE)]
    for name in api.ORDER:
        stub, journal = api.slot(name)
        for start, size in ours:
            assert start + size <= stub or stub + api.STUB_SIZE <= start
            assert start + size <= journal or journal + api.JOURNAL_SIZE <= start


def test_the_site_sits_inside_firsttimeinit() -> None:
    # FirstTimeInit's implementation begins at 0x8908D3D0 and returns at
    # 0x8908D430; hooking outside that window would patch someone else's code.
    assert 0x8908D3D0 < trace.SITE < 0x8908D430
    assert trace.OPERATION_ID == 0xDF
