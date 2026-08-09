#!/usr/bin/env python3
"""Pull the game's own databases out of its BIG archives and decode them.

`cards_ng_db.db` holds the consumables -- every contract, fitness, healing,
training and chemistry-style card, with the asset id and subtype the game looks
them up by. Nothing else has them: they are not on any card site, and served
with invented ids they draw NOT FOUND art and apply nothing.

    python3 tools/extract_fifa_databases.py --game-dir ~/Downloads/fifa14 \
        --out runtime/db

Writes each database next to its meta XML, which is what names the tables:
the database itself carries only four-character shortnames.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "archive"))

import lzx_decode  # noqa: E402


WANTED = ("cards_ng_db", "fifa_ng_db")


def read_directory(stream) -> list[tuple[str, int, int]]:
    header = stream.read(16)
    if header[:4] not in (b"BIG4", b"BIGF"):
        raise ValueError("not an EA BIG4/BIGF archive")
    count = struct.unpack(">I", header[8:12])[0]
    entries = []
    for _ in range(count):
        offset, size = struct.unpack(">II", stream.read(8))
        name = bytearray()
        while True:
            byte = stream.read(1)
            if not byte:
                raise EOFError("truncated BIG directory")
            if byte == b"\0":
                break
            name += byte
        entries.append((name.decode("ascii"), offset, size))
    return entries


def extract(archive: Path, out: Path) -> list[Path]:
    written = []
    with archive.open("rb") as stream:
        for name, offset, size in read_directory(stream):
            base = name.rsplit("/", 1)[-1]
            if not any(base.startswith(want) for want in WANTED):
                continue
            stream.seek(offset)
            blob = stream.read(size)
            if blob[:8] in (lzx_decode.MAGIC, lzx_decode.UNCOMPRESSED_MAGIC):
                blob = lzx_decode.decode_container(blob)
            destination = out / base
            destination.write_bytes(blob)
            written.append(destination)
            print(f"  {name} -> {destination} ({len(blob)} bytes)")
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path.home() / "Downloads" / "fifa14-fut-stable",
        help="directory holding cards0.big and data0.big",
    )
    parser.add_argument("--out", type=Path, default=Path("runtime/db"))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    for archive in ("cards0.big", "data0.big"):
        path = args.game_dir / archive
        if not path.exists():
            print(f"  {path} not found, skipped")
            continue
        print(f"{archive}:")
        written += extract(path, args.out)

    if not written:
        print("nothing extracted")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
