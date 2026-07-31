#!/usr/bin/env python3
"""Convert the prior append-patched archive into an in-place LZX patch.

The original mainfeflow payload remains intact at ORIGINAL_OFFSET in the
append-patched BIG.  The newly encoded payload has exactly the same stored size,
so it can safely replace that slot and the archive can be restored to its
original length.
"""

from pathlib import Path
import shutil
import struct
import sys

sys.path.insert(0, str(Path(__file__).parent))
from patch_fifa_big_entry import find_entry

ENTRY = "data/ui/nav/mainfeflow.nav"
ORIGINAL_OFFSET = 0x13D40500
ORIGINAL_SIZE = 0x0A60
ORIGINAL_ARCHIVE_SIZE = 0x1412B9F2


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    outdir = root / "outputs/fifa14_data1_archive_patch"
    source_big = outdir / "data1.patched.big"
    source_bh = outdir / "data1.patched.bh"
    payload_path = outdir / "mainfeflow.nav.chunklzx"
    output_big = outdir / "data1.lzx.patched.big"
    output_bh = outdir / "data1.lzx.patched.bh"

    payload = payload_path.read_bytes()
    if len(payload) != ORIGINAL_SIZE:
        raise RuntimeError(
            f"replacement is {len(payload):#x}, expected {ORIGINAL_SIZE:#x}"
        )

    shutil.copyfile(source_big, output_big)
    shutil.copyfile(source_bh, output_bh)
    index, table_offset, _, _ = find_entry(output_big, ENTRY)

    with output_big.open("r+b") as f:
        f.seek(ORIGINAL_OFFSET)
        f.write(payload)
        f.seek(table_offset)
        f.write(struct.pack(">II", ORIGINAL_OFFSET, ORIGINAL_SIZE))
        f.truncate(ORIGINAL_ARCHIVE_SIZE)
        f.seek(4)
        f.write(struct.pack("<I", ORIGINAL_ARCHIVE_SIZE))

    bh_record = 16 + index * 20
    with output_bh.open("r+b") as f:
        f.seek(bh_record)
        f.write(struct.pack(">II", ORIGINAL_OFFSET, ORIGINAL_SIZE))

    check = find_entry(output_big, ENTRY)
    if check != (index, table_offset, ORIGINAL_OFFSET, ORIGINAL_SIZE):
        raise RuntimeError(f"BIG verification failed: {check}")
    with output_big.open("rb") as f:
        f.seek(ORIGINAL_OFFSET)
        if f.read(ORIGINAL_SIZE) != payload:
            raise RuntimeError("payload verification failed")

    print(f"Entry index: {index}")
    print(f"BIG table: {table_offset:#x}")
    print(f"BH record: {bh_record:#x}")
    print(f"Payload: {ORIGINAL_OFFSET:#x}/{ORIGINAL_SIZE:#x}")
    print(f"Archive size: {output_big.stat().st_size:#x}")
    print(output_big)
    print(output_bh)


if __name__ == "__main__":
    main()
