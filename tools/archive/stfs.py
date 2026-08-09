#!/usr/bin/env python3
"""Read an Xbox 360 STFS package: list its files and extract one.

FIFA 14's Title Update 3 is not a loose file. It lives inside
`Content\\0000000000000000\\454109C3\\000B0000\\tu00000003_00000000`, an STFS
container of 157 MB, and `patch.big` -- the archive holding the
`helperFunctions` APT this project patches at runtime -- is a file inside it.
Baking that patch into the game rather than applying it every launch means
reaching into this container.

The format, as far as reading goes:

* the header names the package kind in its first four bytes: `CON `, `LIVE` or
  `PIRS`. Only the signature block differs between them; everything below is
  identical.
* payload blocks are 0x1000 bytes and begin at 0xC000.
* hash tables are interleaved with the data. One level-0 table covers 0xAA
  blocks, one level-1 table covers 0xAA of those, and so on. Reading a block
  therefore means counting how many tables precede it -- which is the whole
  difficulty of the format and the only part worth testing on its own.
* the volume descriptor at 0x379 gives the file-table block and its length.
* each file entry is 0x40 bytes: a 0x28-byte name, flags carrying the name
  length and whether the entry is a directory, the block count, the starting
  block, the parent index and the size.

The tables are not read here. Extraction follows the block chain from the file
entry, which is enough to pull a file out; verifying or rebuilding the hashes
is a separate problem and belongs to writing, not reading.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


BLOCK_SIZE = 0x1000
DATA_START = 0xC000
MAGICS = (b"CON ", b"LIVE", b"PIRS")


@dataclass(frozen=True)
class Entry:
    """One file or directory in the package."""

    name: str
    directory: bool
    blocks: int
    start_block: int
    parent: int
    size: int
    index: int


def _tables_per_level(table_size_shift: int) -> tuple[int, int, int]:
    """How many blocks a table at each level covers."""
    return 0xAA, 0xAA * 0xAA, 0xAA * 0xAA * 0xAA


def block_offset(block: int, table_size_shift: int) -> int:
    """Byte offset of a payload block, counting the hash tables before it.

    A level-0 table sits before every 0xAA blocks, a level-1 table before every
    0xAA of those, and a level-2 table before every 0xAA of *those*. The shift
    is 0 or 1 depending on how the package was written, and doubles the space
    each table occupies.
    """
    shift = 1 if table_size_shift else 0
    tables = block // 0xAA + 1
    if block >= 0xAA:
        tables += (block // 0x70E + 1) << shift
    if block >= 0x70E:
        tables += (block // 0x4AF00 + 1) << shift
    return DATA_START + (block + tables) * BLOCK_SIZE


class Package:
    """An STFS package opened for reading."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.handle = self.path.open("rb")
        magic = self.handle.read(4)
        if magic not in MAGICS:
            raise ValueError(f"not an STFS package: {magic!r}")
        self.magic = magic

        self.handle.seek(0x379)
        descriptor = self.handle.read(0x24)
        # byte 0 is the descriptor length, byte 1 carries the table shift
        self.table_size_shift = descriptor[1] & 1
        self.file_table_blocks = struct.unpack_from("<H", descriptor, 2)[0]
        self.file_table_block = int.from_bytes(descriptor[4:7], "little")

    def close(self) -> None:
        self.handle.close()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read_block(self, block: int) -> bytes:
        self.handle.seek(block_offset(block, self.table_size_shift))
        return self.handle.read(BLOCK_SIZE)

    def entries(self) -> list[Entry]:
        raw = b"".join(
            self.read_block(self.file_table_block + index)
            for index in range(self.file_table_blocks)
        )
        found: list[Entry] = []
        for index in range(len(raw) // 0x40):
            record = raw[index * 0x40 : (index + 1) * 0x40]
            flags = record[0x28]
            length = flags & 0x3F
            if not length:
                # A blank name ends the table for practical purposes: the rest
                # is padding, not deleted entries worth walking.
                continue
            name = record[:length].decode("ascii", "replace")
            blocks = int.from_bytes(record[0x29:0x2C], "little")
            start = int.from_bytes(record[0x2F:0x32], "little")
            parent = struct.unpack_from("<h", record, 0x32)[0]
            size = struct.unpack_from(">I", record, 0x34)[0]
            found.append(
                Entry(
                    name=name,
                    directory=bool(flags & 0x80),
                    blocks=blocks,
                    start_block=start,
                    parent=parent,
                    size=size,
                    index=index,
                )
            )
        return found

    def find(self, name: str) -> Entry | None:
        lowered = name.lower()
        for entry in self.entries():
            if entry.name.lower() == lowered:
                return entry
        return None

    def extract(self, entry: Entry) -> bytes:
        """Pull a file out, following its block chain.

        The chain is not contiguous: each block's successor is recorded in the
        level-0 hash table entry for that block, so the blocks have to be
        walked rather than read in a run.
        """
        out = bytearray()
        block = entry.start_block
        for _ in range(entry.blocks):
            out += self.read_block(block)
            block = self._next_block(block)
            if block in (0xFFFFFF, -1):
                break
        return bytes(out[: entry.size])

    def _next_block(self, block: int) -> int:
        """The successor recorded in this block's level-0 hash entry."""
        table = self._hash_table_offset(block)
        self.handle.seek(table + (block % 0xAA) * 0x18 + 0x15)
        return int.from_bytes(self.handle.read(3), "big")

    def _hash_table_offset(self, block: int) -> int:
        shift = 1 if self.table_size_shift else 0
        base = block // 0xAA
        offset = DATA_START + (base * 0xAB) * BLOCK_SIZE
        if block >= 0xAA:
            offset += ((block // 0x70E) + 1) << (shift + 12)
        if block >= 0x70E:
            offset += ((block // 0x4AF00) + 1) << (shift + 12)
        return offset


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--extract")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    with Package(args.package) as package:
        print(f"{package.magic.decode()}  table shift {package.table_size_shift}")
        if not args.extract:
            for entry in package.entries():
                kind = "dir " if entry.directory else "file"
                print(f"  {kind} {entry.name:<34} {entry.size:>12} bytes")
            return 0
        entry = package.find(args.extract)
        if entry is None:
            print(f"not found: {args.extract}")
            return 1
        data = package.extract(entry)
        if args.out:
            args.out.write_bytes(data)
        print(f"{entry.name}: {len(data)} bytes of {entry.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
