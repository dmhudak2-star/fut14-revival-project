from __future__ import annotations

import struct
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_nav_event_trace as trace


def words(image: bytes) -> list[int]:
    return [
        struct.unpack_from(">I", image, offset)[0]
        for offset in range(0, len(image), 4)
    ]


def test_the_displaced_link_save_runs_last() -> None:
    # SendNavEvent's own prologue saves LR into r12; if the stub returned
    # before that ran, the function would save whatever the stub left there.
    encoded = words(trace.build_stub())
    tail = next(index for index, word in enumerate(encoded) if (word >> 26) == 18)
    assert encoded[tail - 1] == int.from_bytes(trace.ORIGINAL, "big")


def test_the_stub_never_writes_the_register_the_site_saves_into() -> None:
    displaced = int.from_bytes(trace.ORIGINAL, "big")
    saved_register = (displaced >> 21) & 0x1F
    for word in words(trace.build_stub()):
        if word == displaced:
            continue
        opcode = word >> 26
        if opcode in (14, 15, 32):  # addi / addis / lwz
            assert (word >> 21) & 0x1F != saved_register
        elif opcode == 21:  # rlwinm
            assert (word >> 16) & 0x1F != saved_register
        elif opcode == 31 and ((word >> 1) & 0x3FF) in (266, 339):  # add / mfspr
            assert (word >> 21) & 0x1F != saved_register


def test_the_stub_leaves_both_arguments_alone() -> None:
    for word in words(trace.build_stub()):
        opcode = word >> 26
        if opcode in (14, 15, 32):
            assert (word >> 21) & 0x1F not in (3, 4)
        elif opcode == 21:
            assert (word >> 16) & 0x1F not in (3, 4)


def test_stub_resumes_after_the_patched_site() -> None:
    encoded = words(trace.build_stub())
    tail = next(index for index, word in enumerate(encoded) if (word >> 26) == 18)
    displacement = encoded[tail] & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    assert trace.STUB + tail * 4 + displacement == trace.SITE + 4
    assert encoded[tail] & 1 == 0


def test_the_ring_index_masks_to_the_declared_record_count() -> None:
    # A mask that disagrees with RECORD_COUNT writes past the journal and
    # corrupts whatever the console keeps after it.
    encoded = words(trace.build_stub())
    masks = [word for word in encoded if (word >> 26) == 21]
    begin = (masks[0] >> 6) & 0x1F
    assert (1 << (32 - begin)) == trace.RECORD_COUNT


def test_the_record_fields_fit_inside_one_slot() -> None:
    encoded = words(trace.build_stub())
    offsets = [
        word & 0xFFFF
        for word in encoded
        if (word >> 26) == 36 and ((word >> 16) & 0x1F) == 11
    ]
    assert offsets[0] == 0  # the counter ahead of the ring
    fields = [offset - trace.RECORD_SIZE for offset in offsets[1:]]
    assert fields == [0x0, 0x4, 0x8]
    assert max(fields) + 4 <= trace.RECORD_SIZE


def test_the_journal_sits_clear_of_the_stub() -> None:
    assert trace.JOURNAL >= trace.STUB + trace.STUB_SIZE


def test_it_does_not_share_a_cave_with_the_listener_trace() -> None:
    import fifa14_fut_notification_listener_trace as listener

    assert not (
        trace.STUB < listener.JOURNAL + listener.JOURNAL_SIZE
        and listener.STUB < trace.JOURNAL + trace.JOURNAL_SIZE
    )


def test_a_pointer_outside_memory_is_reported_not_dereferenced() -> None:
    class Refuses:
        def read(self, *_args):
            raise AssertionError("must not read a pointer outside title memory")

    assert "not a string pointer" in trace.event_name(Refuses(), 0x00000000)


def test_an_injected_event_buffer_is_still_read() -> None:
    # JRPC2 hands the title a temporary buffer far below the title base; a
    # range that starts at the title base reads every injected event as
    # unnamed, which hides exactly what the trace exists to show.
    class Memory:
        def read(self, _address, length):
            return b"iceBreaker\0".ljust(length, b"\0")

    assert trace.event_name(Memory(), 0x78D524E0) == "iceBreaker"
