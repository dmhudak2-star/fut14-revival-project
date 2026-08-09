#!/usr/bin/env python3
"""Read a FIFA 14 t3db database -- `cards_ng_db.db` and `fifa_ng_db.db`.

These are the game's own tables, and the reason to read them is consumables:
their asset ids and subtypes exist nowhere else. Served with invented ids they
draw NOT FOUND art, collapse to a single default type and apply nothing.

The file is a directory of tables, each a block of fixed-size records whose
fields are packed to the bit. Layout::

    0x00  2s    "DB"
    0x08  u32   total size, which matches the decompressed length exactly
    0x10  u32   table count
    0x18  ...   table count x (4s shortname, u32 offset from the directory end)

and each table::

    +0x00 u32   hash
    +0x08 u32   record size in bytes
    +0x0C u32   highest bit index in a record
    +0x14 u16   record count
    +0x1C u8    field count
    +0x28 ...   field count x (u32 type, u32 bit offset, 4s shortname, u32 depth)
    ...         the records themselves

Names are not in this file. Tables and fields carry four-character shortnames
and the matching `-meta.xml` maps them to real ones -- `igAa` is
`fcc_healingcards`, `IKbB` is `cardassetid`. The field descriptors are sorted
by shortname rather than kept in declaration order, so the meta file gives the
names and the descriptors give the layout; neither alone is enough.

Strings (type 13) are offsets into a Huffman-compressed pool that follows the
records, and are returned as raw offsets. The integer fields are what the
consumables need, and they decode without touching the pool.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path


FIELD_STRING = 13
HEADER_SIZE = 0x28
FIELD_SIZE = 0x10


class Database:
    """A t3db file, optionally named by its meta XML."""

    def __init__(self, data: bytes, meta: bytes | None = None) -> None:
        if data[:2] != b"DB":
            raise ValueError(f"not a t3db database: {data[:2]!r}")
        declared = struct.unpack_from(">I", data, 8)[0]
        if declared != len(data):
            raise ValueError(f"declares {declared} bytes, holds {len(data)}")
        self.data = data

        count = struct.unpack_from(">I", data, 0x10)[0]
        directory_end = 0x18 + 8 * count
        self.tables: dict[str, int] = {}
        for index in range(count):
            entry = 0x18 + 8 * index
            tag = data[entry : entry + 4].decode("latin1")
            self.tables[tag] = directory_end + struct.unpack_from(">I", data, entry + 4)[0]

        self.table_names: dict[str, str] = {}
        self.field_names: dict[str, dict[str, str]] = {}
        if meta:
            self._read_meta(meta.decode("utf-8", "replace"))

    def _read_meta(self, xml: str) -> None:
        pattern = re.compile(
            r'<table name="([^"]+)" shortname="([^"]+)">(.*?)</table>', re.S
        )
        for name, tag, body in (match.groups() for match in pattern.finditer(xml)):
            self.table_names[tag] = name
            self.field_names[tag] = {
                short: field
                for field, short in re.findall(
                    r'<field name="([^"]+)" shortname="(\w+)"', body
                )
            }

    def tag_for(self, name: str) -> str:
        for tag, table in self.table_names.items():
            if table == name:
                return tag
        raise KeyError(name)

    def read(self, name: str) -> list[dict[str, int]]:
        """Every record of a table, keyed by field name where one is known."""
        tag = self.tag_for(name) if name in self.table_names.values() else name
        data = self.data
        origin = self.tables[tag]
        record_size = struct.unpack_from(">I", data, origin + 8)[0]
        count = struct.unpack_from(">H", data, origin + 0x14)[0]
        field_count = data[origin + 0x1C]

        fields = []
        labels = self.field_names.get(tag, {})
        for index in range(field_count):
            cursor = origin + HEADER_SIZE + FIELD_SIZE * index
            kind, offset = struct.unpack_from(">II", data, cursor)
            short = data[cursor + 8 : cursor + 12].decode("latin1")
            depth = struct.unpack_from(">I", data, cursor + 12)[0]
            # A string field holds a 32-bit offset into the pool whatever
            # length the schema allows it to reach.
            width = 32 if kind == FIELD_STRING else depth
            fields.append((labels.get(short, short), offset, width))

        start = origin + HEADER_SIZE + FIELD_SIZE * field_count
        bits = record_size * 8
        rows = []
        for index in range(count):
            raw = data[start + index * record_size : start + (index + 1) * record_size]
            if len(raw) < record_size:
                break
            packed = int.from_bytes(raw, "big")
            rows.append(
                {
                    name: (packed >> (bits - offset - width)) & ((1 << width) - 1)
                    for name, offset, width in fields
                }
            )
        return rows


def load(db_path: Path, meta_path: Path | None = None) -> Database:
    meta = meta_path.read_bytes() if meta_path else None
    return Database(db_path.read_bytes(), meta)


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--table")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    database = load(args.database, args.meta)
    if not args.table:
        for tag, offset in sorted(database.tables.items(), key=lambda item: item[1]):
            print(f"  {tag}  {database.table_names.get(tag, '?')}")
        return 0

    rows = database.read(args.table)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
    for row in rows[: args.limit]:
        print("  ", row)
    print(f"  {len(rows)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
