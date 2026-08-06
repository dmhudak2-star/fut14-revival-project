from __future__ import annotations

import struct
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_fut_api_trace as trace
import fifa14_cards_message_dispatch_trace as dispatch


def words(image: bytes) -> list[int]:
    return [
        struct.unpack_from(">I", image, offset)[0]
        for offset in range(0, len(image), 4)
    ]


def test_every_operation_lies_inside_the_cards_module() -> None:
    for name, site in trace.OPERATIONS.items():
        assert trace.MODULE_BASE <= site < trace.MODULE_BASE + trace.MODULE_SIZE, name


def test_operation_handlers_are_distinct() -> None:
    assert len(set(trace.OPERATIONS.values())) == len(trace.OPERATIONS)


def test_stub_starts_with_the_displaced_retail_instruction() -> None:
    stub = trace.build_stub(trace.OPERATIONS["LoginToFUT"])
    assert words(stub)[0] == int.from_bytes(trace.ORIGINAL, "big")


def test_stub_resumes_after_whichever_site_it_was_built_for() -> None:
    for name, site in trace.OPERATIONS.items():
        encoded = words(trace.build_stub(site))
        tail = next(
            index for index, word in enumerate(encoded) if (word >> 26) == 18
        )
        displacement = encoded[tail] & 0x03FFFFFC
        if displacement & 0x02000000:
            displacement -= 0x04000000
        assert trace.STUB + tail * 4 + displacement == site + 4, name
        assert encoded[tail] & 1 == 0, name


def test_stub_only_writes_volatile_scratch_registers() -> None:
    # r3-r5 are the traced arguments and r12 carries the link register the
    # retail prologue stores, so only r10/r11 may be written.
    for word in words(trace.build_stub(trace.OPERATIONS["CreateClub"]))[1:]:
        opcode = word >> 26
        if opcode in (14, 15, 32):
            assert (word >> 21) & 0x1F in (10, 11)
        elif opcode == 21:
            assert (word >> 16) & 0x1F in (10, 11)
        elif opcode == 31 and ((word >> 1) & 0x3FF) == 266:
            assert (word >> 21) & 0x1F in (10, 11)


def test_site_patch_branches_into_the_stub() -> None:
    site = trace.OPERATIONS["CreateMatch"]
    word = struct.unpack(">I", trace.site_patch(site))[0]
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert site + displacement == trace.STUB


def test_cave_does_not_collide_with_the_dispatch_trace() -> None:
    ours = [
        (trace.STUB, trace.STUB_SIZE),
        (trace.JOURNAL, trace.JOURNAL_SIZE),
    ]
    theirs = [
        (dispatch.STUB, dispatch.STUB_SIZE),
        (dispatch.JOURNAL, dispatch.JOURNAL_SIZE),
        (dispatch.POPUP_STUB, dispatch.STUB_SIZE),
        (dispatch.POPUP_JOURNAL, dispatch.JOURNAL_SIZE),
    ]
    for start, size in ours:
        for other, other_size in theirs:
            assert start + size <= other or other + other_size <= start


def test_the_path_to_a_first_match_is_addressable() -> None:
    for required in ("LoginToFUT", "CreateClub", "CreateMatch"):
        assert required in trace.OPERATIONS
