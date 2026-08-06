#!/usr/bin/env python3
"""List or extract files from the Type-0 STFS packages used by Xbox 360 TUs.

This is intentionally a small read-only extractor.  It implements the STFS
block mapping and hash-chain traversal needed for FIFA 14's Title Update 3;
it does not modify, rehash, or resign the package.
"""

from __future__ import annotations

import argparse
import dataclasses
import struct
from pathlib import Path


BLOCK_SIZE = 0x1000
LEVEL0_BLOCKS = 0xAA
LEVEL1_BLOCKS = 0x70E4
STFS_END = 0xFFFFFF


def u24le(data: bytes) -> int:
    return data[0] | data[1] << 8 | data[2] << 16


@dataclasses.dataclass(frozen=True)
class Entry:
    index: int
    name: str
    folder: bool
    block_count: int
    start_block: int
    parent: int
    size: int


class Type0Stfs:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.data = source.read_bytes()
        if self.data[:4] not in (b"CON ", b"LIVE", b"PIRS"):
            raise ValueError("not an STFS package")
        if len(self.data) < 0x400:
            raise ValueError("truncated STFS header")

        header_size = struct.unpack_from(">I", self.data, 0x340)[0]
        self.base = (header_size + 0xFFF) & ~0xFFF
        descriptor = self.data[0x379 : 0x39D]
        if len(descriptor) != 0x24 or descriptor[0:2] != b"\x24\x00":
            raise ValueError("invalid STFS volume descriptor")
        separation = descriptor[2] & 3
        if self.base != 0xB000 or separation != 1:
            raise ValueError(
                "only Type-0 STFS packages are supported "
                f"(base=0x{self.base:X}, separation={separation})"
            )
        self.directory_block_count = int.from_bytes(descriptor[3:5], "little")
        self.directory_start_block = u24le(descriptor[5:8])
        self.allocated_blocks = struct.unpack_from(">I", descriptor, 0x1C)[0]
        if not self.directory_block_count:
            raise ValueError("empty STFS directory")

    def data_offset(self, block: int) -> int:
        if not 0 <= block < self.allocated_blocks:
            raise ValueError(f"invalid data block 0x{block:X}")
        physical = block + (block // LEVEL0_BLOCKS) + 1
        if block >= LEVEL0_BLOCKS:
            physical += (block // LEVEL1_BLOCKS) + 1
            if block >= LEVEL1_BLOCKS:
                physical += 1
        return self.base + physical * BLOCK_SIZE

    def level0_hash_offset(self, block: int) -> int:
        physical = (block // LEVEL0_BLOCKS) * 0xAB
        if block >= LEVEL0_BLOCKS:
            physical += (block // LEVEL1_BLOCKS) + 1
            if block >= LEVEL1_BLOCKS:
                physical += 1
        return self.base + physical * BLOCK_SIZE + 0x18 * (block % LEVEL0_BLOCKS)

    def next_block(self, block: int) -> int:
        offset = self.level0_hash_offset(block) + 0x14
        if offset + 4 > len(self.data):
            raise ValueError(f"hash record for block 0x{block:X} is truncated")
        return struct.unpack_from(">I", self.data, offset)[0] & STFS_END

    def chain(self, start: int, count: int) -> list[int]:
        blocks: list[int] = []
        seen: set[int] = set()
        current = start
        for _ in range(count):
            if current in seen:
                raise ValueError(f"block-chain loop at 0x{current:X}")
            if not 0 <= current < self.allocated_blocks:
                raise ValueError(f"block-chain target 0x{current:X} is invalid")
            seen.add(current)
            blocks.append(current)
            following = self.next_block(current)
            if following == STFS_END:
                break
            current = following
        if len(blocks) != count:
            raise ValueError(f"short block chain: {len(blocks)}/{count}")
        return blocks

    def read_chain(self, start: int, count: int, size: int) -> bytes:
        output = bytearray()
        for block in self.chain(start, count):
            offset = self.data_offset(block)
            chunk = self.data[offset : offset + BLOCK_SIZE]
            if len(chunk) != BLOCK_SIZE:
                raise ValueError(f"data block 0x{block:X} is truncated")
            output.extend(chunk)
        return bytes(output[:size])

    def entries(self) -> list[Entry]:
        directory = self.read_chain(
            self.directory_start_block,
            self.directory_block_count,
            self.directory_block_count * BLOCK_SIZE,
        )
        result: list[Entry] = []
        for slot in range(len(directory) // 0x40):
            raw = directory[slot * 0x40 : (slot + 1) * 0x40]
            name_length = raw[0x28] & 0x3F
            if not name_length:
                continue
            name = raw[:name_length].decode("ascii", "strict")
            size = struct.unpack_from(">I", raw, 0x34)[0]
            result.append(
                Entry(
                    index=len(result),
                    name=name,
                    folder=bool(raw[0x28] & 0x80),
                    block_count=u24le(raw[0x29:0x2C]),
                    start_block=u24le(raw[0x2F:0x32]),
                    parent=struct.unpack_from(">H", raw, 0x32)[0],
                    size=size,
                )
            )
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--extract", action="append", default=[], metavar="NAME")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    package = Type0Stfs(args.package)
    entries = package.entries()
    print(
        f"Type-0 STFS: base=0x{package.base:X}, "
        f"allocated=0x{package.allocated_blocks:X}, entries={len(entries)}"
    )
    for entry in entries:
        kind = "dir " if entry.folder else "file"
        print(
            f"[{entry.index:03d}] {kind} size=0x{entry.size:X} "
            f"blocks=0x{entry.block_count:X} start=0x{entry.start_block:X} "
            f"parent=0x{entry.parent:04X} {entry.name}"
        )

    if args.extract:
        if args.output is None:
            parser.error("--output is required with --extract")
        args.output.mkdir(parents=True, exist_ok=True)
        by_name = {entry.name: entry for entry in entries if not entry.folder}
        for name in args.extract:
            entry = by_name.get(name)
            if entry is None:
                raise ValueError(f"file not found in root directory: {name}")
            expected_blocks = (entry.size + BLOCK_SIZE - 1) // BLOCK_SIZE
            if entry.block_count != expected_blocks:
                raise ValueError(
                    f"{name}: block count {entry.block_count} != {expected_blocks}"
                )
            destination = args.output / name
            destination.write_bytes(
                package.read_chain(entry.start_block, entry.block_count, entry.size)
            )
            print(f"Extracted {name}: {entry.size} bytes -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
