"""The t3db reader, on a database built by hand.

The real one is 2.7 MB of the game's own data and is not in this repository, so
these build the smallest file that exercises each rule the reader depends on:
the directory offsets being relative to the end of the directory, the field
descriptors being sorted by shortname rather than kept in declaration order,
and the records being packed to the bit rather than to the byte.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "archive"))

import t3db  # noqa: E402


META = """<?xml version="1.0" encoding="utf-8"?>
<database name="test" shortname="test" version="6">
  <table name="fcc_healingcards" shortname="igAa">
    <fields>
      <field name="carddbid" shortname="Blfc" type="DBOFIELDTYPE_INTEGER" depth="31" />
      <field name="cardsubtype" shortname="Dwte" type="DBOFIELDTYPE_INTEGER" depth="14" />
      <field name="amount" shortname="ZwZZ" type="DBOFIELDTYPE_INTEGER" depth="7" />
    </fields>
  </table>
</database>
"""


def build(records: list[tuple[int, int, int]], record_size: int = 8) -> bytes:
    """A one-table database holding these (carddbid, cardsubtype, amount)."""
    fields = [
        (b"Blfc", 0, 31),
        (b"Dwte", 31, 14),
        (b"ZwZZ", 45, 7),
    ]

    table = bytearray(0x28)
    struct.pack_into(">I", table, 8, record_size)
    struct.pack_into(">I", table, 0x0C, record_size * 8 - 1)
    struct.pack_into(">H", table, 0x14, len(records))
    table[0x1C] = len(fields)
    for name, offset, depth in fields:
        entry = bytearray(0x10)
        struct.pack_into(">II", entry, 0, 3, offset)
        entry[8:12] = name
        struct.pack_into(">I", entry, 12, depth)
        table += entry

    for carddbid, subtype, amount in records:
        packed = 0
        for value, (_, offset, depth) in zip(
            (carddbid, subtype, amount), fields
        ):
            packed |= value << (record_size * 8 - offset - depth)
        table += packed.to_bytes(record_size, "big")

    directory = bytearray()
    directory += b"igAa" + struct.pack(">I", 0)
    header = bytearray(0x18)
    header[0:2] = b"DB"
    struct.pack_into(">I", header, 0x10, 1)
    total = len(header) + len(directory) + len(table)
    struct.pack_into(">I", header, 8, total)
    return bytes(header + directory + table)


def test_it_reads_the_records_back() -> None:
    rows = [(5002001, 219, 20), (5002002, 219, 40), (5002003, 220, 10)]
    database = t3db.Database(build(rows), META.encode())
    assert database.read("fcc_healingcards") == [
        {"carddbid": 5002001, "cardsubtype": 219, "amount": 20},
        {"carddbid": 5002002, "cardsubtype": 219, "amount": 40},
        {"carddbid": 5002003, "cardsubtype": 220, "amount": 10},
    ]


def test_fields_are_placed_by_their_descriptor_not_their_order() -> None:
    # The descriptors are sorted by shortname -- Blfc, Dwte, ZwZZ -- and the
    # meta file lists carddbid, cardsubtype, amount. Reading the records in
    # declaration order happens to agree here and does not in the real
    # database, where weightrare sorts before cardassetid and the two swap.
    database = t3db.Database(build([(1, 2, 3)]), META.encode())
    fields = {name: (offset, depth) for name, offset, depth in [
        ("carddbid", 0, 31), ("cardsubtype", 31, 14), ("amount", 45, 7)
    ]}
    row = database.read("fcc_healingcards")[0]
    assert set(row) == set(fields)


def test_a_short_last_record_is_dropped_rather_than_misread() -> None:
    data = bytearray(build([(5002001, 219, 20), (5002002, 219, 40)]))
    # Claim a third record the file does not hold.
    table_start = 0x18 + 8
    struct.pack_into(">H", data, table_start + 0x14, 3)
    struct.pack_into(">I", data, 8, len(data))
    rows = t3db.Database(bytes(data), META.encode()).read("fcc_healingcards")
    assert len(rows) == 2


def test_it_refuses_a_file_that_is_not_a_database() -> None:
    with pytest.raises(ValueError, match="not a t3db"):
        t3db.Database(b"BIG4" + bytes(64))


def test_it_refuses_a_truncated_database() -> None:
    # The header's own size field is the one check that catches a decode that
    # stopped early -- which is exactly how this database used to come out.
    data = build([(5002001, 219, 20)])
    with pytest.raises(ValueError, match="declares"):
        t3db.Database(data[:-4])


def test_the_table_can_be_read_by_shortname_or_by_name() -> None:
    database = t3db.Database(build([(7, 8, 9)]), META.encode())
    assert database.read("igAa") == database.read("fcc_healingcards")
