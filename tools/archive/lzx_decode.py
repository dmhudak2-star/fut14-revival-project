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
        # Counted once. Recounting inside decode() made every bit read a scan
        # of the whole length vector -- correct, and slow enough that a full
        # database took minutes rather than seconds.
        self.counts: dict[int, int] = {}
        by_length: dict[int, list[int]] = {}
        for symbol, value in enumerate(lengths):
            if value:
                by_length.setdefault(value, []).append(symbol)
        code = 0
        for length in range(1, self.max_length + 1):
            group = by_length.get(length, ())
            self.first_code[length] = code
            self.first_symbol[length] = len(self.sorted_symbols)
            self.counts[length] = len(group)
            self.sorted_symbols.extend(group)
            code = (code + len(group)) << 1

    def decode(self, reader: BitReader) -> int:
        code = 0
        for length in range(1, self.max_length + 1):
            code = (code << 1) | reader.read(1)
            count = self.counts.get(length)
            if not count:
                continue
            offset = code - self.first_code[length]
            if offset < count:
                return self.sorted_symbols[self.first_symbol[length] + offset]
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


FRAME_SIZE = 0x8000


def split_frames(chunk: bytes) -> list[tuple[int, int, int]]:
    """Split a chunk into its frames as ``(raw, offset, packed)``.

    A frame is either ``FF <u16 raw> <u16 packed>`` or a bare ``<u16 packed>``
    standing for a full 32 KiB of output. This is the framing XCompress emits
    on the 360, and the reason a chunk does not decode as one bitstream: the
    two bytes before each frame are a length, not data, and a decoder reading
    straight through them desynchronises a few frames in.

    Reading the whole archive this way accounts for every declared byte --
    ten chunks of eight full frames and a last of four, 2 730 320 in total,
    exactly what the container announces.
    """
    frames: list[tuple[int, int, int]] = []
    position = 0
    while position + 2 <= len(chunk):
        if chunk[position] == 0xFF:
            if position + 5 > len(chunk):
                break
            raw, packed = struct.unpack(">HH", chunk[position + 1 : position + 5])
            position += 5
        else:
            packed = struct.unpack(">H", chunk[position : position + 2])[0]
            raw = FRAME_SIZE
            position += 2
        if not packed or position + packed > len(chunk):
            break
        frames.append((raw, position, packed))
        position += packed
    return frames


class LzxStream:
    """An LZX decoder whose state outlives the frame it is reading.

    Blocks run across frame boundaries -- a block announces a size far larger
    than the 32 KiB a frame carries -- so the window, the Huffman trees, the
    repeated offsets and the number of bytes left in the current block all have
    to persist. Only the bit reader restarts, because each frame's bitstream is
    byte-aligned and preceded by its own length.
    """

    def __init__(self, window_bits: int = DEFAULT_WINDOW_BITS) -> None:
        slots = position_slots(window_bits)
        self.main_elements = MAIN_TREE_ELEMENTS + slots * 8
        self.main_lengths = [0] * self.main_elements
        self.length_lengths = [0] * LENGTH_TREE_ELEMENTS
        self.main: HuffmanTable | None = None
        self.lengths: HuffmanTable | None = None
        self.aligned: HuffmanTable | None = None
        self.block_type = 0
        self.block_remaining = 0
        self.r0 = self.r1 = self.r2 = 1
        self.window = bytearray()
        self.header_read = False
        self.e8_size = 0

    def decode_frame(self, data: bytes, out_size: int) -> bytes:
        reader = BitReader(data)
        if not self.header_read:
            self.header_read = True
            # Announced once for the whole stream, not once per frame.
            if reader.read(1):
                self.e8_size = (reader.read(16) << 16) | reader.read(16)
        start = len(self.window)
        while len(self.window) - start < out_size:
            if self.block_remaining == 0:
                self._start_block(reader)
            self._decode_symbols(reader, start + out_size)
        return bytes(self.window[start : start + out_size])

    def _start_block(self, reader: BitReader) -> None:
        self.block_type = reader.read(3)
        self.block_remaining = (
            (reader.read(8) << 16) | (reader.read(8) << 8) | reader.read(8)
        )
        if self.block_type == UNCOMPRESSED:
            reader.align()
            self.r0 = int.from_bytes(reader.read_bytes_aligned(4), "little")
            self.r1 = int.from_bytes(reader.read_bytes_aligned(4), "little")
            self.r2 = int.from_bytes(reader.read_bytes_aligned(4), "little")
            return
        if self.block_type == ALIGNED:
            self.aligned = HuffmanTable(
                [reader.read(3) for _ in range(ALIGNED_TREE_ELEMENTS)]
            )
        elif self.block_type == VERBATIM:
            self.aligned = None
        else:
            raise ValueError(f"Unsupported LZX block type {self.block_type}")
        read_lengths(reader, self.main_lengths, 0, 256)
        read_lengths(reader, self.main_lengths, 256, self.main_elements)
        self.main = HuffmanTable(self.main_lengths)
        read_lengths(reader, self.length_lengths, 0, NUM_SECONDARY_LENGTHS)
        self.lengths = HuffmanTable(self.length_lengths)

    def _decode_symbols(self, reader: BitReader, limit: int) -> None:
        output = self.window
        if self.block_type == UNCOMPRESSED:
            take = min(self.block_remaining, limit - len(output))
            output.extend(reader.read_bytes_aligned(take))
            self.block_remaining -= take
            return

        assert self.main is not None and self.lengths is not None
        while self.block_remaining > 0 and len(output) < limit:
            symbol = self.main.decode(reader)
            if symbol < 256:
                output.append(symbol)
                self.block_remaining -= 1
                continue

            symbol -= 256
            length_header = symbol & 7
            position_slot = symbol >> 3
            if length_header == 7:
                match_length = self.lengths.decode(reader) + 7 + MIN_MATCH
            else:
                match_length = length_header + MIN_MATCH

            if position_slot == 0:
                match_offset = self.r0
            elif position_slot == 1:
                match_offset, self.r1 = self.r1, self.r0
                self.r0 = match_offset
            elif position_slot == 2:
                match_offset, self.r2 = self.r2, self.r0
                self.r0 = match_offset
            else:
                extra = EXTRA_BITS[position_slot]
                if self.aligned is not None and extra >= 3:
                    verbatim = reader.read(extra - 3) << 3
                    match_offset = (
                        POSITION_BASE[position_slot]
                        - 2
                        + verbatim
                        + self.aligned.decode(reader)
                    )
                else:
                    match_offset = (
                        POSITION_BASE[position_slot] - 2 + reader.read(extra)
                    )
                self.r0, self.r1, self.r2 = match_offset, self.r0, self.r1

            if match_offset > len(output):
                raise ValueError("LZX match reaches before the start of the window")
            start = len(output) - match_offset
            for step in range(match_length):
                output.append(output[start + step])
            self.block_remaining -= match_length


def decode_block(
    data: bytes,
    expected: int,
    window_bits: int = DEFAULT_WINDOW_BITS,
    partial: bool = False,
    history: bytes = b"",
) -> bytes:
    """Decode a framed LZX bitstream into at most ``expected`` bytes.

    With ``partial`` set, a stream that ends before ``expected`` returns what it
    produced instead of raising. The container's chunk size is a maximum rather
    than a measurement -- the card and player databases decode to less -- and
    asking for the full amount made the reader run past the end and read the
    next block header out of whatever followed. That surfaced as "unsupported
    block type", which reads like a format fault and is really an
    off-the-end one.

    ``history`` seeds the window with what earlier chunks produced. The window
    runs on across chunk boundaries in these archives, so a match early in a
    chunk can reach back into the one before it; decoding each chunk from an
    empty window makes those matches read before the start and the chunk stops
    a fraction of the way in.
    """
    reader = BitReader(data)
    slots = position_slots(window_bits)
    main_elements = MAIN_TREE_ELEMENTS + slots * 8
    main_lengths = [0] * main_elements
    length_lengths = [0] * LENGTH_TREE_ELEMENTS
    output = bytearray(history)
    produced_before = len(output)
    r0, r1, r2 = 1, 1, 1

    # A set bit here introduces the E8 call-translation size, which the
    # resources in this archive do not use but still announce.
    if reader.read(1):
        reader.read(16)
        reader.read(16)

    available = len(data) * 8
    # The limit counts new bytes only: the history seeds the window, it is not
    # something this call is asked to produce again.
    while len(output) - produced_before < expected:
        if partial and reader.bit_count + 27 > available:
            break
        block_type = reader.read(3)
        block_size = (reader.read(8) << 16) | (reader.read(8) << 8) | reader.read(8)
        if partial and (
            block_type not in (UNCOMPRESSED, VERBATIM, ALIGNED)
            or not block_size
            or block_size > expected - (len(output) - produced_before) + (1 << 20)
        ):
            break

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
        # The history seeds the window, so the limit has to count new bytes
        # here too -- comparing the whole buffer meant a seeded call was
        # already "full" and decoded nothing at all.
        while produced < block_size and len(output) - produced_before < expected:
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

    if partial:
        # However much the stream held. Truncating to the requested amount
        # would throw away the tail of a chunk that produced more than the
        # caller's estimate, and these chunks do not announce their length.
        return bytes(output[produced_before:])
    return bytes(output[produced_before : produced_before + expected])


def decode_container(blob: bytes) -> bytes:
    """Decode a whole ``chunklzx``/``chunkunc`` container."""
    magic = blob[0:8]
    if magic == UNCOMPRESSED_MAGIC:
        total = struct.unpack(">I", blob[12:16])[0]
        return blob[48 : 48 + total]
    if magic != MAGIC:
        raise ValueError(f"Not a chunk container: {magic!r}")

    total, chunk_size, chunk_count = struct.unpack(">III", blob[12:24])
    stored_size, block_type = struct.unpack(">II", blob[40:48])
    if block_type != BLOCK_TYPE_LZX:
        raise ValueError(f"Unsupported chunk block type {block_type}")

    # Each chunk carries its own eight-byte (stored, block type) descriptor
    # immediately before its data, and every chunk's data starts on a sixteen
    # byte boundary -- so the descriptor always sits at 8 mod 16, and the gap
    # after a chunk is whatever padding that alignment needs. Measured against
    # the eleven descriptors of the card database, which is the same rule the
    # title update's resources follow; an earlier version counted the
    # descriptor twice and landed sixteen bytes past every one of them, which
    # only ever showed up on a resource of more than one chunk.
    output = bytearray()
    cursor = 40
    for index in range(chunk_count):
        stored_size, block_type = struct.unpack(">II", blob[cursor : cursor + 8])
        if block_type != BLOCK_TYPE_LZX:
            raise ValueError(f"Unsupported chunk block type {block_type}")
        cursor += 8
        chunk = blob[cursor : cursor + stored_size]
        output.extend(decode_chunk(chunk, total, len(output), chunk_size))
        # The next chunk's descriptor sits immediately after this chunk's
        # data, and the data it introduces starts on a 16-byte boundary -- so
        # align the position *after* the descriptor, not the position before
        # it.  Aligning the end of the data instead gives the same answer
        # whenever that end falls 1..8 bytes into a 16-byte unit, which is
        # every single-chunk resource and most two-chunk ones; it is 16 bytes
        # short otherwise, and an 11-chunk archive hits that case reliably.
        end_of_data = cursor + stored_size
        cursor = align_to(end_of_data + 8, 16) - 8
    if len(output) != total:
        raise ValueError(f"Decoded {len(output)} bytes, container declares {total}")
    return bytes(output)


def align_to(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def decode_chunk(chunk: bytes, total: int, produced: int, chunk_size: int) -> bytes:
    """Decode one chunk, framed or not.

    A chunk is a run of frames, each at most 32 KiB of output: ``FF`` and both
    sizes when the frame is short, a bare compressed length when it is full.
    A resource small enough to fit one frame is the ``FF`` case and nothing
    else, which is why reading only that case worked on every single-frame
    resource in the title update and on none of the databases.

    The decoder's state carries across the frames of a chunk but not between
    chunks -- each chunk restarts the window, which is what makes the container
    randomly addressable.
    """
    stream = LzxStream()
    remaining = min(total - produced, chunk_size)
    out = bytearray()
    for raw_size, offset, packed_size in split_frames(chunk):
        if remaining <= 0:
            break
        take = min(raw_size, remaining)
        out += stream.decode_frame(chunk[offset : offset + packed_size], take)
        remaining -= take
    return bytes(out)


def _unused_single_chunk_path(blob, chunk_count, total, stored_size):
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
