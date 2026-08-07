#!/usr/bin/env python3
"""Encode the LZX streams FIFA 14 stores its resources in.

``lzx_decode`` made the archives readable; this makes them writable. It exists
because a patched resource has to go back into its own slot: relocating one to
the end of the archive and repointing its directory record boots the title to a
black screen, so the patched bytes must re-compress to no more than the slot
they came from.

The output is a single verbatim block -- literals and matches through the main
tree, long lengths through the length tree, both trees delta-coded through a
pretree, exactly the shapes ``lzx_decode`` reads. Aligned-offset blocks and the
E8 call translation are not emitted; neither is needed to fit, and every byte
of this encoder has to be verifiable against the decoder.

Positions 0-2 of the position-slot space are the repeated-offset slots. A real
distance encodes as ``distance + 2``, which is always at least 3, so those
slots are never selected and the repeated-offset state never has to be
predicted here.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lzx_decode import (
    DEFAULT_WINDOW_BITS,
    EXTRA_BITS,
    LENGTH_TREE_ELEMENTS,
    MAIN_TREE_ELEMENTS,
    MIN_MATCH,
    POSITION_BASE,
    PRETREE_ELEMENTS,
    VERBATIM,
    position_slots,
)


MAX_CODE_LENGTH = 16
MAX_MATCH = LENGTH_TREE_ELEMENTS - 1 + 7 + MIN_MATCH
# Matches shorter than this cost more than the literals they replace once the
# position slot and its footer bits are paid for.
MIN_USEFUL_MATCH = 3


class BitWriter:
    """MSB-first bit writer over 16-bit little-endian words.

    The mirror of ``lzx_decode.BitReader``: bits fill each word from the top,
    and the words themselves go out little-endian.
    """

    def __init__(self) -> None:
        self.words: list[int] = []
        self.bits = 0
        self.count = 0

    def write(self, value: int, count: int) -> None:
        if count == 0:
            return
        self.bits = (self.bits << count) | (value & ((1 << count) - 1))
        self.count += count
        while self.count >= 16:
            self.count -= 16
            self.words.append((self.bits >> self.count) & 0xFFFF)

    def finish(self) -> bytes:
        if self.count:
            self.write(0, 16 - self.count)
        return b"".join(struct.pack("<H", word) for word in self.words)


def code_lengths(frequencies: list[int]) -> list[int]:
    """Canonical Huffman code lengths, none longer than the format allows.

    Depth is limited by flattening the tree over progressively smoothed
    frequencies. Real inputs here never need it, but a 17-bit code would be
    silently unreadable, so it cannot be left to chance.
    """
    used = [index for index, count in enumerate(frequencies) if count]
    if not used:
        return [0] * len(frequencies)
    if len(used) == 1:
        lengths = [0] * len(frequencies)
        lengths[used[0]] = 1
        return lengths

    weights = list(frequencies)
    while True:
        nodes = [(weights[index], index, None, None) for index in used]
        heap = sorted(nodes)
        while len(heap) > 1:
            left, right = heap.pop(0), heap.pop(0)
            heap.append((left[0] + right[0], -1, left, right))
            heap.sort(key=lambda node: node[0])
        lengths = [0] * len(frequencies)

        def walk(node, depth: int) -> None:
            if node[1] >= 0:
                lengths[node[1]] = max(depth, 1)
                return
            walk(node[2], depth + 1)
            walk(node[3], depth + 1)

        walk(heap[0], 0)
        if max(lengths) <= MAX_CODE_LENGTH:
            return lengths
        weights = [(count + 1) // 2 if count else 0 for count in weights]


def canonical_codes(lengths: list[int]) -> dict[int, tuple[int, int]]:
    """Map each symbol to (code, length), assigned the way the decoder reads."""
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    for length in range(1, (max(lengths) if lengths else 0) + 1):
        for symbol, value in enumerate(lengths):
            if value == length:
                codes[symbol] = (code, length)
                code += 1
        code <<= 1
    return codes


def plan_tree(lengths: list[int], previous: list[int]) -> list[tuple]:
    """Choose pretree items for a tree, collapsing runs of unused symbols.

    Most of the main tree is unused -- a position-slot space of 34 gives 528
    elements and a payload this size touches a fraction of them -- so sending
    one symbol per element spends more on the tree than on the data. Codes 17
    and 18 exist for exactly that: they set a run of elements to length zero,
    which is what an unused symbol is.
    """
    items: list[tuple] = []
    index = 0
    total = len(lengths)
    while index < total:
        if lengths[index] == 0:
            run = 0
            while index + run < total and lengths[index + run] == 0:
                run += 1
            while run >= 20:
                take = min(run, 51)
                items.append((18, take - 20, take))
                index += take
                run -= take
            while run >= 4:
                take = min(run, 19)
                items.append((17, take - 4, take))
                index += take
                run -= take
            for _ in range(run):
                items.append(((previous[index] - 0) % 17, None, 1))
                index += 1
            continue
        items.append(((previous[index] - lengths[index]) % 17, None, 1))
        index += 1
    return items


def encode_tree(writer: BitWriter, lengths: list[int], previous: list[int]) -> None:
    """Emit a tree as pretree-coded deltas from the previous tree."""
    items = plan_tree(lengths, previous)
    frequencies = [0] * PRETREE_ELEMENTS
    for symbol, _extra, _span in items:
        frequencies[symbol] += 1
    pretree = code_lengths(frequencies)
    if max(pretree) > 15:
        raise AssertionError("pretree code length does not fit its 4-bit field")
    for length in pretree:
        writer.write(length, 4)
    codes = canonical_codes(pretree)
    for symbol, extra, _span in items:
        code, length = codes[symbol]
        writer.write(code, length)
        if symbol == 17:
            writer.write(extra, 4)
        elif symbol == 18:
            writer.write(extra, 5)


def _best_match(
    data: bytes, positions: dict, index: int, window: int, limit: int
) -> tuple[int, int]:
    if index + MIN_USEFUL_MATCH > limit:
        return 0, 0
    key = data[index : index + MIN_USEFUL_MATCH]
    best_length, best_distance = 0, 0
    for candidate in reversed(positions.get(key, ())):
        distance = index - candidate
        if distance > window:
            break
        length = MIN_USEFUL_MATCH
        while (
            length < MAX_MATCH
            and index + length < limit
            and data[candidate + length] == data[index + length]
        ):
            length += 1
        if length > best_length:
            best_length, best_distance = length, distance
            if length == MAX_MATCH:
                break
    return best_length, best_distance


def find_matches(data: bytes, window: int) -> list[tuple[int, int]]:
    """Lazy-matching parse into (literal, -1) and (length, distance) tokens.

    Taking the longest match at every position is not the shortest encoding:
    emitting one literal often exposes a longer match at the next byte. Only
    a strictly longer follow-up is worth the literal, which keeps this a small
    rule rather than a search.
    """
    positions: dict[bytes, list[int]] = {}
    tokens: list[tuple[int, int]] = []
    index = 0
    limit = len(data)

    def remember(at: int) -> None:
        if at + MIN_USEFUL_MATCH <= limit:
            positions.setdefault(data[at : at + MIN_USEFUL_MATCH], []).append(at)

    while index < limit:
        length, distance = _best_match(data, positions, index, window, limit)
        if length >= MIN_USEFUL_MATCH and length < MAX_MATCH:
            remember(index)
            ahead_length, _ahead_distance = _best_match(
                data, positions, index + 1, window, limit
            )
            if ahead_length > length:
                tokens.append((data[index], -1))
                index += 1
                continue
            for step in range(1, length):
                remember(index + step)
        elif length >= MIN_USEFUL_MATCH:
            for step in range(length):
                remember(index + step)
        if length >= MIN_USEFUL_MATCH:
            tokens.append((length, distance))
            index += length
        else:
            remember(index)
            tokens.append((data[index], -1))
            index += 1
    return tokens


def slot_for(formatted_offset: int) -> tuple[int, int]:
    for slot in range(len(POSITION_BASE) - 1, -1, -1):
        if POSITION_BASE[slot] <= formatted_offset:
            return slot, formatted_offset - POSITION_BASE[slot]
    raise AssertionError("no position slot covers this offset")


def all_matches(data: bytes, window: int) -> list[list[tuple[int, int]]]:
    """For every position, the best match at each reachable length."""
    positions: dict[bytes, list[int]] = {}
    limit = len(data)
    best: list[list[tuple[int, int]]] = [[] for _ in range(limit)]
    for index in range(limit):
        if index + MIN_USEFUL_MATCH <= limit:
            key = data[index : index + MIN_USEFUL_MATCH]
            longest = 0
            for candidate in reversed(positions.get(key, ())):
                distance = index - candidate
                if distance > window:
                    break
                length = MIN_USEFUL_MATCH
                while (
                    length < MAX_MATCH
                    and index + length < limit
                    and data[candidate + length] == data[index + length]
                ):
                    length += 1
                if length > longest:
                    longest = length
                    best[index].append((length, distance))
                    if length == MAX_MATCH:
                        break
            positions.setdefault(key, []).append(index)
    return best


def _token_cost(
    length: int, distance: int, main_bits: list[int], length_bits: list[int]
) -> int:
    slot, _extra = slot_for(distance + 2)
    header = min(length - MIN_MATCH, 7)
    symbol = MAIN_TREE_ELEMENTS + slot * 8 + header
    cost = main_bits[symbol] or 24
    if header == 7:
        secondary = length - 7 - MIN_MATCH
        cost += length_bits[secondary] or 24
    return cost + EXTRA_BITS[slot]


def optimal_parse(
    data: bytes, window: int, main_bits: list[int], length_bits: list[int]
) -> list[tuple[int, int]]:
    """Re-parse against measured bit costs instead of match length alone.

    A longest-match parse optimises the wrong quantity: what matters is total
    encoded bits, and a shorter match that lands on a cheap symbol can beat a
    longer one. With the costs from a first pass this becomes a shortest-path
    problem over the input, which is exact for those costs.
    """
    limit = len(data)
    candidates = all_matches(data, window)
    cost = [0] * (limit + 1)
    choice: list[tuple[int, int]] = [(0, 0)] * (limit + 1)
    for index in range(limit - 1, -1, -1):
        literal = (main_bits[data[index]] or 24) + cost[index + 1]
        best, taken = literal, (data[index], -1)
        for length, distance in candidates[index]:
            for actual in {length, min(length, MIN_USEFUL_MATCH)}:
                if actual < MIN_USEFUL_MATCH or index + actual > limit:
                    continue
                total = _token_cost(actual, distance, main_bits, length_bits)
                total += cost[index + actual]
                if total < best:
                    best, taken = total, (actual, distance)
        cost[index] = best
        choice[index] = taken
    tokens: list[tuple[int, int]] = []
    index = 0
    while index < limit:
        value, distance = choice[index]
        tokens.append((value, distance))
        index += value if distance >= 0 else 1
    return tokens


def price_symbols(
    tokens: list[tuple[int, int]], main_elements: int
) -> tuple[list[int], list[int]]:
    """Code lengths a parse would produce, used to price the next one."""
    main_frequencies = [0] * main_elements
    length_frequencies = [0] * LENGTH_TREE_ELEMENTS
    for value, distance in tokens:
        if distance < 0:
            main_frequencies[value] += 1
            continue
        slot, _extra = slot_for(distance + 2)
        header = min(value - MIN_MATCH, 7)
        main_frequencies[MAIN_TREE_ELEMENTS + slot * 8 + header] += 1
        if header == 7:
            length_frequencies[value - 7 - MIN_MATCH] += 1
    return code_lengths(main_frequencies), code_lengths(length_frequencies)


def encode_block(
    data: bytes, window_bits: int = DEFAULT_WINDOW_BITS, refine: bool = True
) -> bytes:
    """Compress one block into an LZX bitstream ``lzx_decode`` can read."""
    slots = position_slots(window_bits)
    main_elements = MAIN_TREE_ELEMENTS + slots * 8
    tokens = find_matches(data, 1 << window_bits)
    if refine:
        # A first pass only exists to price the symbols; the parse that is
        # actually emitted is then chosen against those prices.
        main_bits, length_bits = price_symbols(tokens, main_elements)
        tokens = optimal_parse(data, 1 << window_bits, main_bits, length_bits)

    main_frequencies = [0] * main_elements
    length_frequencies = [0] * LENGTH_TREE_ELEMENTS
    encoded: list[tuple] = []
    # Slots 0-2 name the three most recent distances and carry no footer bits
    # at all, so reusing one is markedly cheaper than spelling the distance
    # out.  The decoder rotates this state on every match, so the encoder has
    # to rotate it identically or every later offset decodes wrong.
    recent = [1, 1, 1]
    for value, distance in tokens:
        if distance < 0:
            main_frequencies[value] += 1
            encoded.append(("literal", value))
            continue
        if distance == recent[0]:
            slot, extra, footer = 0, 0, 0
        elif distance == recent[1]:
            slot, extra, footer = 1, 0, 0
            recent[1] = recent[0]
            recent[0] = distance
        elif distance == recent[2]:
            slot, extra, footer = 2, 0, 0
            recent[2] = recent[0]
            recent[0] = distance
        else:
            slot, extra = slot_for(distance + 2)
            footer = EXTRA_BITS[slot]
            recent = [distance, recent[0], recent[1]]
        header = min(value - MIN_MATCH, 7)
        symbol = MAIN_TREE_ELEMENTS + slot * 8 + header
        main_frequencies[symbol] += 1
        secondary = -1
        if header == 7:
            secondary = value - 7 - MIN_MATCH
            length_frequencies[secondary] += 1
        encoded.append(("match", symbol, secondary, extra, footer))

    main_lengths = code_lengths(main_frequencies)
    length_lengths = code_lengths(length_frequencies)
    main_codes = canonical_codes(main_lengths)
    length_codes = canonical_codes(length_lengths)

    writer = BitWriter()
    writer.write(0, 1)  # no E8 call translation
    writer.write(VERBATIM, 3)
    writer.write((len(data) >> 16) & 0xFF, 8)
    writer.write((len(data) >> 8) & 0xFF, 8)
    writer.write(len(data) & 0xFF, 8)
    encode_tree(writer, main_lengths[:256], [0] * 256)
    encode_tree(writer, main_lengths[256:], [0] * (main_elements - 256))
    encode_tree(writer, length_lengths, [0] * LENGTH_TREE_ELEMENTS)

    for item in encoded:
        if item[0] == "literal":
            code, length = main_codes[item[1]]
            writer.write(code, length)
            continue
        _kind, symbol, secondary, extra, footer = item
        code, length = main_codes[symbol]
        writer.write(code, length)
        if secondary >= 0:
            code, length = length_codes[secondary]
            writer.write(code, length)
        writer.write(extra, footer)
    return writer.finish()


def encode_container(payload: bytes, window_bits: int = DEFAULT_WINDOW_BITS) -> bytes:
    """Wrap compressed data in the ``chunklzx`` container the archives use."""
    if len(payload) > 0xFFFF:
        raise ValueError("This encoder emits a single frame; payload is too large")
    stream = encode_block(payload, window_bits)
    if len(stream) > 0xFFFF:
        raise ValueError("Compressed frame does not fit its 16-bit length")
    frame = b"\xff" + struct.pack(">HH", len(payload), len(stream)) + stream
    return (
        bytes(4)
        + b"chunklzx"
        + struct.pack(
            ">10I", 2, len(payload), 0x40000, 1, 0x10, 0, 0, 0, len(frame), 3
        )
        + frame
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    payload = args.source.read_bytes()
    container = encode_container(payload)
    args.destination.write_bytes(container)
    print(f"Encoded {len(payload)} bytes into a {len(container)}-byte container")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
