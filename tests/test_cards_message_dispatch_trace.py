from __future__ import annotations

import struct
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_cards_message_dispatch_trace as trace


def words(image: bytes) -> list[int]:
    return [
        struct.unpack_from(">I", image, offset)[0]
        for offset in range(0, len(image), 4)
    ]


def test_stub_starts_with_the_displaced_retail_instruction() -> None:
    assert words(trace.build_stub())[0] == int.from_bytes(trace.ORIGINAL, "big")


def test_stub_fits_its_cave_and_resumes_after_the_patched_site() -> None:
    stub = trace.build_stub()
    assert len(stub) == trace.STUB_SIZE
    encoded = words(stub)
    tail = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 18
    )
    displacement = encoded[tail] & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.STUB + tail * 4 + displacement == trace.SITE + 4
    assert encoded[tail] & 1 == 0  # never link: the retail LR must survive


def test_stub_only_writes_volatile_scratch_registers() -> None:
    # r3-r5 are the traced arguments and r12 carries the link register the
    # retail prologue stores, so the recorder may only touch r10/r11.
    for word in words(trace.build_stub())[1:]:
        opcode = word >> 26
        if opcode in (14, 15, 32):  # addi / addis / lwz write rT
            assert (word >> 21) & 0x1F in (10, 11)
        elif opcode == 21:  # rlwinm writes rA
            assert (word >> 16) & 0x1F in (10, 11)
        elif opcode == 31 and ((word >> 1) & 0x3FF) == 266:  # add writes rT
            assert (word >> 21) & 0x1F in (10, 11)


def test_journal_never_overlaps_the_stub() -> None:
    assert (
        trace.STUB + trace.STUB_SIZE <= trace.JOURNAL
        or trace.JOURNAL + trace.JOURNAL_SIZE <= trace.STUB
    )


def test_regions_stay_inside_the_cards_module() -> None:
    for start, size in (
        (trace.STUB, trace.STUB_SIZE),
        (trace.JOURNAL, trace.JOURNAL_SIZE),
        (trace.SITE, 4),
    ):
        assert trace.MODULE_BASE <= start
        assert start + size <= trace.MODULE_BASE + 0x2B0000


def test_site_patch_branches_into_the_stub() -> None:
    word = struct.unpack(">I", trace.site_patch())[0]
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.SITE + displacement == trace.STUB


def test_login_message_is_the_recovered_fatal_case() -> None:
    assert trace.FATAL_MESSAGE == 0x65
    assert "2148:101" in trace.describe_message(0x65)
    assert "not handled" in trace.describe_message(0x1234)


def test_popup_stub_is_a_separate_recorder_in_its_own_cave() -> None:
    stub = trace.build_popup_stub()
    assert len(stub) == trace.STUB_SIZE
    assert words(stub)[0] == int.from_bytes(trace.ORIGINAL, "big")
    assert (
        trace.POPUP_STUB + trace.STUB_SIZE <= trace.POPUP_JOURNAL
        or trace.POPUP_JOURNAL + trace.JOURNAL_SIZE <= trace.POPUP_STUB
    )
    for start, size in (
        (trace.POPUP_STUB, trace.STUB_SIZE),
        (trace.POPUP_JOURNAL, trace.JOURNAL_SIZE),
    ):
        assert start + size <= trace.STUB or trace.JOURNAL + trace.JOURNAL_SIZE <= start


def test_popup_stub_resumes_after_its_patched_site() -> None:
    encoded = words(trace.build_popup_stub())
    tail = next(index for index, word in enumerate(encoded) if (word >> 26) == 18)
    displacement = encoded[tail] & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.POPUP_STUB + tail * 4 + displacement == trace.POPUP_SITE + 4
    assert encoded[tail] & 1 == 0
