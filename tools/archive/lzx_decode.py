#!/usr/bin/env python3
"""Decode the LZX streams FIFA 14 stores its resources in.

The title wraps most resources in a ``chunklzx`` container whose payload is
LZX as specified for CAB, which is also what Xbox 360's XMemCompress emits.
Every earlier pass over these archives shelled out to an external decoder that
is not part of this repository, so nothing here could read a ``.nav`` graph or
any other compressed entry on its own.

Layout of the container::

    0x00  8s    "chunklzx"
    0x08  u32   version (2)
    0x0C  u32   total uncompressed size
    0x10  u32   chunk size (0x40000)
    0x14  u32   chunk count
    0x18  u32   header size (0x10)
    0x1C  u32   reserved x3
    0x28  u32   stored size
    0x2C  u32   block type (3 = LZX)
    0x30  ...   framed LZX blocks

Each frame is ``FF <u16 uncompressed> <u16 compressed>`` followed by that many
bytes of LZX bitstream.  The bitstream is read most-significant-bit first out
of 16-bit little-endian words, which is the one detail that makes an otherwise
ordinary Huffman decoder produce garbage when got wrong.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


MAGIC = b"chunklzx"
UNCOMPRESSED_MAGIC = b"chunkunc"
BLOCK_TYPE_LZX = 3

# Block types inside the bitstream.
VERBATIM, ALIGNED, UNCOMPRESSED = 1, 2, 3

MAIN_TREE_ELEMENTS = 256
ALIGNED_TREE_ELEMENTS = 8
LENGTH_TREE_ELEMENTS = 249
PRETREE_ELEMENTS = 20
MIN_MATCH = 2
NUM_SECONDARY_LENGTHS = 249

# Base position and footer bits for each position slot, per the LZX spec.
POSITION_BASE: list[int] = []
EXTRA_BITS: list[int] = []


def _build_position_tables() -> None:
    if POSITION_BASE:
        return
    base = 0
    for slot in range(51):
        if slot < 4:
            bits = 0
        elif slot < 36:
            bits = (slot // 2) - 1
        else:
            bits = 17
        EXTRA_BITS.append(bits)
        POSITION_BASE.append(base)
        base += 1 << bits


_build_position_tables()


def position_slots(window_bits: int) -> int:
    """How many position slots a window of this size uses."""
    if window_bits == 20:
        return 42
    if window_bits == 21:
        return 50
    return window_bits * 2


class BitReader:
    """MSB-first bit reader over 16-bit little-endian words."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0
        self.bit_buffer = 0
        self.bit_count = 0

    def _fill(self) -> None:
        while self.bit_count <= 16:
            if self.position + 1 < len(self.data):
                word = self.data[self.position] | (self.data[self.position + 1] << 8)
                self.position += 2
            elif self.position < len(self.data):
                word = self.data[self.position]
                self.position += 1
            else:
                word = 0
            self.bit_buffer = ((self.bit_buffer << 16) | word) & 0xFFFFFFFFFFFF
            self.bit_count += 16

    def read(self, count: int) -> int:
        if count == 0:
            return 0
        self._fill()
        value = (self.bit_buffer >> (self.bit_count - count)) & ((1 << count) - 1)
        self.bit_count -= count
        return value

    def peek(self, count: int) -> int:
        self._fill()
        return (self.bit_buffer >> (self.bit_count - count)) & ((1 << count) - 1)

    def align(self) -> None:
        """Drop to the next 16-bit boundary, as an uncompressed block needs."""
        self.bit_count -= self.bit_count % 16

    def read_bytes_aligned(self, count: int) -> bytes:
        self.align()
        out = bytearray()
        while self.bit_count >= 8 and len(out) < count:
            out.append(self.read(8))
        remaining = count - len(out)
        if remaining:
            out.extend(self.data[self.position : self.position + remaining])
            self.position += remaining
        return bytes(out)


class HuffmanTable:
    """Canonical Huffman decoder driven by a code-length vector."""

    def __init__(self, lengths: list[int]) -> None:
        self.lengths = lengths
        self.max_length = max(lengths) if lengths else 0
        self.first_code: dict[int, int] = {}
        self.first_symbol: dict[int, int] = {}
        self.sorted_symbols: list[int] = []
        code = 0
        for length in range(1, self.max_length + 1):
            self.first_code[length] = code
            self.first_symbol[length] = len(self.sorted_symbols)
            self.sorted_symbols.extend(
                symbol for symbol, value in enumerate(lengths) if value == length
            )
            code = (code + sum(1 for value in lengths if value == length)) << 1

    def decode(self, reader: BitReader) -> int:
        code = 0
        for length in range(1, self.max_length + 1):
            code = (code << 1) | reader.read(1)
            first = self.first_code.get(length)
            if first is None:
                continue
            count = sum(1 for value in self.lengths if value == length)
            if count and code - first < count:
                return self.sorted_symbols[self.first_symbol[length] + code - first]
        raise ValueError("Invalid Huffman code in LZX stream")


def read_lengths(
    reader: BitReader, previous: list[int], start: int, end: int
) -> None:
    """Read a delta-encoded slice of a Huffman tree's code lengths.

    Lengths arrive as differences from the previous block's tree, coded with a
    small pretree.  Codes 17-19 are run-length forms, which is where a decoder
    that treats them as literals silently desynchronises.
    """
    pretree_lengths = [reader.read(4) for _ in range(PRETREE_ELEMENTS)]
    pretree = HuffmanTable(pretree_lengths)
    index = start
    while index < end:
        symbol = pretree.decode(reader)
        if symbol == 17:
            run = reader.read(4) + 4
            for _ in range(run):
                if index >= end:
                    break
                previous[index] = 0
                index += 1
        elif symbol == 18:
            run = reader.read(5) + 20
            for _ in range(run):
                if index >= end:
                    break
                previous[index] = 0
                index += 1
        elif symbol == 19:
            run = reader.read(1) + 4
            repeated = pretree.decode(reader)
            value = (previous[index] - repeated) % 17
            for _ in range(run):
                if index >= end:
                    break
                previous[index] = value
                index += 1
        else:
            previous[index] = (previous[index] - symbol) % 17
            index += 1


# Every resource sampled from this archive decodes with a 128 KiB window, and
# a wrong size silently mis-sizes the main tree rather than failing cleanly.
DEFAULT_WINDOW_BITS = 17


def decode_block(
    data: bytes, expected: int, window_bits: int = DEFAULT_WINDOW_BITS
) -> bytes:
    """Decode one framed LZX block into ``expected`` bytes."""
    reader = BitReader(data)
    slots = position_slots(window_bits)
    main_elements = MAIN_TREE_ELEMENTS + slots * 8
    main_lengths = [0] * main_elements
    length_lengths = [0] * LENGTH_TREE_ELEMENTS
    output = bytearray()
    r0, r1, r2 = 1, 1, 1

    # A set bit here introduces the E8 call-translation size, which the
    # resources in this archive do not use but still announce.
    if reader.read(1):
        reader.read(16)
        reader.read(16)

    while len(output) < expected:
        block_type = reader.read(3)
        block_size = (reader.read(8) << 16) | (reader.read(8) << 8) | reader.read(8)

        if block_type == UNCOMPRESSED:
            reader.align()
            r0 = int.from_bytes(reader.read_bytes_aligned(4), "little")
            r1 = int.from_bytes(reader.read_bytes_aligned(4), "little")
            r2 = int.from_bytes(reader.read_bytes_aligned(4), "little")
            output.extend(reader.read_bytes_aligned(block_size))
            continue

        if block_type == ALIGNED:
            aligned_lengths = [reader.read(3) for _ in range(ALIGNED_TREE_ELEMENTS)]
            aligned = HuffmanTable(aligned_lengths)
        elif block_type == VERBATIM:
            aligned = None
        else:
            raise ValueError(f"Unsupported LZX block type {block_type}")

        read_lengths(reader, main_lengths, 0, 256)
        read_lengths(reader, main_lengths, 256, main_elements)
        main = HuffmanTable(main_lengths)
        read_lengths(reader, length_lengths, 0, NUM_SECONDARY_LENGTHS)
        lengths = HuffmanTable(length_lengths)

        produced = 0
        while produced < block_size and len(output) < expected:
            symbol = main.decode(reader)
            if symbol < 256:
                output.append(symbol)
                produced += 1
                continue

            symbol -= 256
            length_header = symbol & 7
            position_slot = symbol >> 3
            if length_header == 7:
                match_length = lengths.decode(reader) + 7 + MIN_MATCH
            else:
                match_length = length_header + MIN_MATCH

            if position_slot == 0:
                match_offset = r0
            elif position_slot == 1:
                match_offset, r1 = r1, r0
                r0 = match_offset
            elif position_slot == 2:
                match_offset, r2 = r2, r0
                r0 = match_offset
            else:
                extra = EXTRA_BITS[position_slot]
                if aligned is not None and extra >= 3:
                    verbatim = reader.read(extra - 3) << 3
                    match_offset = (
                        POSITION_BASE[position_slot] - 2 + verbatim + aligned.decode(reader)
                    )
                else:
                    match_offset = POSITION_BASE[position_slot] - 2 + reader.read(extra)
                r0, r1, r2 = match_offset, r0, r1

            if match_offset > len(output):
                raise ValueError("LZX match reaches before the start of the window")
            start = len(output) - match_offset
            for step in range(match_length):
                output.append(output[start + step])
            produced += match_length

    return bytes(output[:expected])


def decode_container(blob: bytes) -> bytes:
    """Decode a whole ``chunklzx``/``chunkunc`` container."""
    magic = blob[0:8]
    if magic == UNCOMPRESSED_MAGIC:
        total = struct.unpack(">I", blob[12:16])[0]
        return blob[48 : 48 + total]
    if magic != MAGIC:
        raise ValueError(f"Not a chunk container: {magic!r}")

    total, _chunk_size, chunk_count = struct.unpack(">III", blob[12:24])
    stored_size, block_type = struct.unpack(">II", blob[40:48])
    if block_type != BLOCK_TYPE_LZX:
        raise ValueError(f"Unsupported chunk block type {block_type}")

    output = bytearray()
    cursor = 48
    for _ in range(chunk_count):
        if blob[cursor] == 0xFF:
            raw_size, packed_size = struct.unpack(">HH", blob[cursor + 1 : cursor + 5])
            cursor += 5
        else:
            # The framed form carries its sizes in sixteen bits, so a resource
            # that decompresses past 64 KiB cannot use it and the bitstream
            # begins immediately.  The container header already states both
            # sizes for that case.
            raw_size, packed_size = total, stored_size
        output.extend(decode_block(blob[cursor : cursor + packed_size], raw_size))
        cursor += packed_size
    if len(output) != total:
        raise ValueError(f"Decoded {len(output)} bytes, container declares {total}")
    return bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    decoded = decode_container(args.source.read_bytes())
    args.destination.write_bytes(decoded)
    print(f"Decoded {len(decoded)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
