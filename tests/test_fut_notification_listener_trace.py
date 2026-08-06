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


def stub_words() -> list[int]:
    return words(trace.build_stub())


def index_of(encoded: list[int], word: int) -> int:
    return encoded.index(word)


def test_the_displaced_comparison_runs_last() -> None:
    # It sets cr6 for the retail branches that follow, so anything recording
    # after it could clobber the flags the bus is about to read.
    encoded = stub_words()
    displaced = int.from_bytes(trace.ORIGINAL, "big")
    tail = next(index for index, word in enumerate(encoded) if (word >> 26) == 18)
    assert encoded[tail - 1] == displaced


def test_the_condition_register_is_restored_before_the_displaced_comparison() -> None:
    # The filtering comparison writes cr0, so the CR the bus arrived with has
    # to be put back before the retail instruction sets cr6 on top of it.
    encoded = stub_words()
    save = index_of(encoded, trace.mfcr(12))
    restore = index_of(encoded, trace.mtcrf(0xFF, 12))
    compare = index_of(encoded, trace.cmplwi(4, trace.TARGET))
    displaced = index_of(encoded, int.from_bytes(trace.ORIGINAL, "big"))
    assert save < compare < restore < displaced


def test_nothing_between_save_and_restore_clobbers_the_saved_register() -> None:
    encoded = stub_words()
    save = index_of(encoded, trace.mfcr(12))
    restore = index_of(encoded, trace.mtcrf(0xFF, 12))
    for word in encoded[save + 1 : restore]:
        opcode = word >> 26
        if opcode in (14, 15, 32):  # addi / addis / lwz
            assert (word >> 21) & 0x1F != 12
        elif opcode == 21:  # rlwinm
            assert (word >> 16) & 0x1F != 12
        elif opcode == 31 and ((word >> 1) & 0x3FF) in (266, 339):  # add / mfspr
            assert (word >> 21) & 0x1F != 12


def test_the_filter_skips_exactly_the_recording_block() -> None:
    # Landing short would run a partial record; landing long would skip the
    # CR restore and leave the bus reading cr0 from our own comparison.
    encoded = stub_words()
    branch_index = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 16
    )
    displacement = encoded[branch_index] & 0xFFFC
    if displacement & 0x8000:
        displacement -= 0x10000
    target = trace.STUB + branch_index * 4 + displacement
    restore = trace.STUB + index_of(encoded, trace.mtcrf(0xFF, 12)) * 4
    assert target == restore


def test_the_filter_branches_on_inequality_with_the_target_operation() -> None:
    encoded = stub_words()
    branch_index = next(
        index for index, word in enumerate(encoded) if (word >> 26) == 16
    )
    assert encoded[branch_index - 1] == trace.cmplwi(4, trace.TARGET)
    assert (encoded[branch_index] >> 21) & 0x1F == trace.BRANCH_IF_FALSE
    assert (encoded[branch_index] >> 16) & 0x1F == trace.CR0_EQ
    assert encoded[branch_index] & 1 == 0  # never links


def test_stub_resumes_after_the_patched_site() -> None:
    encoded = stub_words()
    tail = next(index for index, word in enumerate(encoded) if (word >> 26) == 18)
    displacement = encoded[tail] & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.STUB + tail * 4 + displacement == trace.SITE + 4
    assert encoded[tail] & 1 == 0


def test_stub_uses_no_recording_forms() -> None:
    # A dot-form would set cr0 outside the saved window, where nothing puts
    # the condition register back.
    for word in stub_words():
        if word == int.from_bytes(trace.ORIGINAL, "big"):
            continue
        if (word >> 26) == 21:  # rlwinm
            assert word & 1 == 0


def test_stub_only_writes_scratch_registers() -> None:
    # r3 and r4 are the bus arguments; r10-r12 are free here.
    for word in stub_words():
        if word == int.from_bytes(trace.ORIGINAL, "big"):
            continue
        opcode = word >> 26
        if opcode in (14, 15, 32):
            assert (word >> 21) & 0x1F in (10, 11, 12)
        elif opcode == 21:
            assert (word >> 16) & 0x1F in (10, 11, 12)
        elif opcode == 31 and ((word >> 1) & 0x3FF) in (19, 266, 339):
            assert (word >> 21) & 0x1F in (10, 11, 12)


def test_the_record_fits_the_slot_it_is_written_into() -> None:
    encoded = stub_words()
    offsets = [
        word & 0xFFFF
        for word in encoded
        if (word >> 26) == 36 and ((word >> 16) & 0x1F) == 11
    ]
    # Offset 0 is the counter that precedes the ring; the rest are the record.
    assert offsets[0] == 0
    fields = [offset - trace.RECORD_SIZE for offset in offsets[1:]]
    assert fields == [0x0, 0x4, 0x8, 0xC]
    assert max(fields) + 4 <= trace.RECORD_SIZE


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
