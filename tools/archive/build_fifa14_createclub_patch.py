#!/usr/bin/env python3
"""Build a reversible FIFA 14 data1 patch that opens FUT club creation."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    index: int
    table_offset: int
    data_offset: int
    size: int
    name: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_entries(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    with path.open("rb") as stream:
        header = stream.read(16)
        if header[:4] != b"BIG4":
            raise RuntimeError("Source is not a BIG4 archive")
        count = struct.unpack(">I", header[8:12])[0]
        for index in range(count):
            table_offset = stream.tell()
            data_offset, size = struct.unpack(">II", stream.read(8))
            raw_name = bytearray()
            while True:
                byte = stream.read(1)
                if not byte:
                    raise EOFError("Unexpected end of BIG directory")
                if byte == b"\0":
                    break
                raw_name += byte
            entries.append(
                Entry(
                    index,
                    table_offset,
                    data_offset,
                    size,
                    raw_name.decode("ascii"),
                )
            )
    return entries


def patch_entry(
    big: Path,
    bh: Path,
    entries: list[Entry],
    wanted: str,
    payload_path: Path,
) -> None:
    entry = next(
        entry for entry in entries if entry.name.lower() == wanted.lower()
    )
    ordered = sorted(entries, key=lambda item: item.data_offset)
    position = ordered.index(entry)
    next_offset = ordered[position + 1].data_offset
    capacity = next_offset - entry.data_offset
    payload = payload_path.read_bytes()
    if len(payload) > capacity:
        raise RuntimeError(
            f"{wanted}: payload {len(payload):#x} exceeds slot {capacity:#x}"
        )

    with big.open("r+b") as stream:
        stream.seek(entry.data_offset)
        stream.write(payload)
        stream.write(bytes(capacity - len(payload)))
        stream.seek(entry.table_offset)
        stream.write(struct.pack(">II", entry.data_offset, len(payload)))

    bh_record = 16 + entry.index * 20
    with bh.open("r+b") as stream:
        stream.seek(bh_record)
        old_offset, old_size = struct.unpack(">II", stream.read(8))
        if (old_offset, old_size) != (entry.data_offset, entry.size):
            raise RuntimeError(
                f"{wanted}: unexpected BH record "
                f"{old_offset:#x}/{old_size:#x}"
            )
        stream.seek(bh_record)
        stream.write(struct.pack(">II", entry.data_offset, len(payload)))

    print(
        f"{wanted}: index={entry.index}, table={entry.table_offset:#x}, "
        f"slot={entry.data_offset:#x}/{capacity:#x}, payload={len(payload):#x}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_big", type=Path)
    parser.add_argument("source_bh", type=Path)
    parser.add_argument("mainfeflow", type=Path)
    parser.add_argument("futloginflow", type=Path)
    parser.add_argument("output_big", type=Path)
    parser.add_argument("output_bh", type=Path)
    args = parser.parse_args()

    shutil.copyfile(args.source_big, args.output_big)
    shutil.copyfile(args.source_bh, args.output_bh)
    entries = read_entries(args.source_big)
    patch_entry(
        args.output_big,
        args.output_bh,
        entries,
        "data/ui/nav/mainfeflow.nav",
        args.mainfeflow,
    )
    patch_entry(
        args.output_big,
        args.output_bh,
        entries,
        "data/ui/nav/fut/futloginflow.nav",
        args.futloginflow,
    )

    print(f"BIG SHA-256: {sha256(args.output_big)}")
    print(f"BH SHA-256:  {sha256(args.output_bh)}")


if __name__ == "__main__":
    main()
