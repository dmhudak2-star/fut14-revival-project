from __future__ import annotations

import struct
import sys
from pathlib import Path


ARCHIVE = Path(__file__).resolve().parents[1] / "tools" / "archive"
sys.path.insert(0, str(ARCHIVE))

import lzx_decode


def uncompressed_container(payload: bytes) -> bytes:
    """The layout an uncompressed entry has inside the archive."""
    return (
        b"chunkunc"
        + struct.pack(
            ">10I", 2, len(payload), 0x40000, 1, 0x10, 0, 0, 0, len(payload), 4
        )
        + payload
    )


def test_an_uncompressed_container_round_trips() -> None:
    payload = b'{"name":"futLogInFlow"}'
    assert lzx_decode.decode_container(uncompressed_container(payload)) == payload


def test_a_foreign_container_is_refused_by_name() -> None:
    blob = bytes(4) + b"nonsense" + bytes(40)
    try:
        lzx_decode.decode_container(blob)
    except ValueError as error:
        assert "chunk container" in str(error)
    else:
        raise AssertionError("expected a refusal")


def test_position_bases_follow_the_slot_footer_widths() -> None:
    # Each slot covers exactly 2**extra_bits offsets, so the next base is the
    # previous one plus that span. Getting this wrong decodes every match to
    # the wrong distance while still looking structurally valid.
    for slot in range(1, 50):
        span = 1 << lzx_decode.EXTRA_BITS[slot - 1]
        assert (
            lzx_decode.POSITION_BASE[slot] - lzx_decode.POSITION_BASE[slot - 1] == span
        )


def test_the_first_four_slots_carry_no_footer_bits() -> None:
    assert lzx_decode.EXTRA_BITS[:4] == [0, 0, 0, 0]
    assert lzx_decode.POSITION_BASE[:4] == [0, 1, 2, 3]


def test_slot_counts_match_the_window_sizes_lzx_defines() -> None:
    assert lzx_decode.position_slots(15) == 30
    assert lzx_decode.position_slots(17) == 34
    assert lzx_decode.position_slots(20) == 42
    assert lzx_decode.position_slots(21) == 50


def test_the_bit_reader_takes_words_little_endian_but_bits_high_first() -> None:
    # This is the detail that separates a working LZX decoder from one that
    # produces plausible-looking rubbish: words are little-endian, bits are not.
    reader = lzx_decode.BitReader(bytes([0x34, 0x12]))
    assert reader.read(4) == 0x1
    assert reader.read(4) == 0x2
    assert reader.read(8) == 0x34


def test_aligning_discards_only_the_partial_word() -> None:
    reader = lzx_decode.BitReader(bytes([0x34, 0x12, 0x78, 0x56]))
    reader.read(3)
    reader.align()
    assert reader.bit_count % 16 == 0


def test_the_default_window_is_the_one_this_archive_uses() -> None:
    # Every resource sampled from data1.big decodes at 17 and fails at others,
    # so a regression here would break every caller silently.
    assert lzx_decode.DEFAULT_WINDOW_BITS == 17


def test_a_stream_shorter_than_the_chunk_size_stops_where_it_ends() -> None:
    # The container's chunk size is a maximum, not a measurement. Asking for
    # the full amount made the reader run past the end of the bitstream and
    # read the next block header out of whatever followed, which surfaced as
    # "unsupported block type" -- a format fault by appearance, an
    # off-the-end one in fact.
    payload = b'{"name":"futLogInFlow"}'
    container = uncompressed_container(payload)
    # 40 is the container header; the chunk descriptor is the last 8 bytes.
    chunk = container[48:]
    out = lzx_decode.decode_block(chunk, 1 << 20, partial=True)
    assert out.startswith(payload) or out == b""


def test_history_seeds_the_window_without_being_returned() -> None:
    # The window runs on across chunk boundaries, so a match early in a chunk
    # can reach back into the one before it. The history is not output.
    payload = b"the same twenty-three bytes"
    chunk = uncompressed_container(payload)[48:]
    plain = lzx_decode.decode_block(chunk, 1 << 20, partial=True)
    seeded = lzx_decode.decode_block(
        chunk, 1 << 20, partial=True, history=b"x" * 4096
    )
    assert seeded == plain
