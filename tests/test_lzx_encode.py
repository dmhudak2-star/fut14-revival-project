from __future__ import annotations

import sys
from pathlib import Path


ARCHIVE = Path(__file__).resolve().parents[1] / "tools" / "archive"
sys.path.insert(0, str(ARCHIVE))

import lzx_decode
import lzx_encode


def round_trip(payload: bytes) -> bytes:
    return lzx_decode.decode_container(lzx_encode.encode_container(payload))


def test_a_single_byte_survives() -> None:
    assert round_trip(b"A") == b"A"


def test_every_byte_value_survives() -> None:
    payload = bytes(range(256)) * 3
    assert round_trip(payload) == payload


def test_highly_repetitive_input_survives_and_shrinks() -> None:
    payload = b"abcabcabc" * 200
    assert round_trip(payload) == payload
    assert len(lzx_encode.encode_container(payload)) < len(payload)


def test_incompressible_input_survives() -> None:
    # A stream with no useful matches exercises the literal path exclusively.
    payload = bytes((index * 37 + 11) % 256 for index in range(4000))
    assert round_trip(payload) == payload


def test_a_run_longer_than_one_match_survives() -> None:
    # Runs longer than MAX_MATCH have to split across tokens, and overlapping
    # matches (distance 1) are the case that silently corrupts naive encoders.
    payload = b"\x5a" * (lzx_encode.MAX_MATCH * 3)
    assert round_trip(payload) == payload


def test_text_with_long_repeats_survives() -> None:
    payload = (b'{"name":"futLogIn1","targets":["iceBreaker"]}' * 80) + bytes(64)
    assert round_trip(payload) == payload


def test_the_bit_writer_mirrors_the_reader() -> None:
    writer = lzx_encode.BitWriter()
    writer.write(0x1, 4)
    writer.write(0x2, 4)
    writer.write(0x34, 8)
    encoded = writer.finish()
    reader = lzx_decode.BitReader(encoded)
    assert reader.read(4) == 0x1
    assert reader.read(4) == 0x2
    assert reader.read(8) == 0x34


def test_code_lengths_stay_inside_the_format_limit() -> None:
    # A pathological frequency spread would otherwise produce a code the
    # format cannot express, which decodes as garbage rather than failing.
    frequencies = [0] * 300
    for index in range(40):
        frequencies[index] = 2**index
    lengths = lzx_encode.code_lengths(frequencies)
    assert max(lengths) <= lzx_encode.MAX_CODE_LENGTH


def test_canonical_codes_are_prefix_free() -> None:
    lengths = lzx_encode.code_lengths([5, 3, 1, 1, 9, 0, 2, 7])
    codes = lzx_encode.canonical_codes(lengths)
    seen = []
    for code, length in codes.values():
        for other_code, other_length in seen:
            shorter = min(length, other_length)
            if (code >> (length - shorter)) == (other_code >> (other_length - shorter)):
                raise AssertionError("codes share a prefix")
        seen.append((code, length))


def test_a_run_of_unused_symbols_is_planned_as_runs() -> None:
    # Sending 500 unused main-tree symbols one at a time costs more than the
    # payload does; the run forms are what makes the tree affordable.
    lengths = [4] + [0] * 500 + [4]
    items = lzx_encode.plan_tree(lengths, [0] * len(lengths))
    assert len(items) < 20
    assert sum(span for _symbol, _extra, span in items) == len(lengths)


def test_planned_runs_cover_the_tree_exactly() -> None:
    lengths = [0] * 3 + [7] + [0] * 25 + [2, 2] + [0] * 60
    items = lzx_encode.plan_tree(lengths, [0] * len(lengths))
    assert sum(span for _symbol, _extra, span in items) == len(lengths)
