from __future__ import annotations

import sys
from pathlib import Path


ARCHIVE = Path(__file__).resolve().parents[1] / "tools" / "archive"
sys.path.insert(0, str(ARCHIVE))

import stfs


def test_the_first_block_sits_after_one_hash_table() -> None:
    # Data starts at 0xC000 and one level-0 table always precedes block 0, so
    # the first payload block is one block further in. Getting this wrong
    # shifts every read by 4 KiB and yields plausible-looking rubbish.
    assert stfs.block_offset(0, 0) == stfs.DATA_START + stfs.BLOCK_SIZE


def test_blocks_advance_one_block_at_a_time_inside_a_table() -> None:
    first = stfs.block_offset(0, 0)
    for block in range(1, 0xAA):
        assert stfs.block_offset(block, 0) - first == block * stfs.BLOCK_SIZE


def test_a_second_table_appears_after_0xAA_blocks() -> None:
    # One level-0 table covers 0xAA blocks, so crossing that boundary costs an
    # extra table -- plus the level-1 table that first appears there.
    step = stfs.block_offset(0xAA, 0) - stfs.block_offset(0xA9, 0)
    assert step > stfs.BLOCK_SIZE


def test_the_table_shift_widens_the_gap() -> None:
    # A shift of 1 doubles the space each table occupies, so the same block
    # sits further into the file.
    assert stfs.block_offset(0xAA, 1) > stfs.block_offset(0xAA, 0)


def test_offsets_never_go_backwards() -> None:
    previous = -1
    for block in range(0, 0x1000):
        offset = stfs.block_offset(block, 0)
        assert offset > previous
        previous = offset


def test_a_foreign_file_is_refused_by_magic() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "not-a-package.bin"
        path.write_bytes(b"RIFF" + bytes(0x400))
        try:
            stfs.Package(path)
        except ValueError as error:
            assert "not an STFS package" in str(error)
        else:
            raise AssertionError("expected a refusal")
