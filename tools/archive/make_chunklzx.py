#!/usr/bin/env python3
"""Wrap one raw XMem-compatible LZX stream in FIFA's chunklzx container."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("uncompressed", type=Path)
    parser.add_argument("raw_lzx", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = args.uncompressed.read_bytes()
    compressed = args.raw_lzx.read_bytes()
    if len(source) > 0xFFFF or len(compressed) > 0xFFFF:
        raise SystemExit("Single-block XMem framing requires 16-bit sizes")

    xmem = (
        b"\xFF"
        + struct.pack(">HH", len(source), len(compressed))
        + compressed
    )
    stored_size = (len(xmem) + 0xF) & ~0xF
    xmem += b"\0" * (stored_size - len(xmem))

    header = (
        b"chunklzx"
        + struct.pack(
            ">10I",
            2,
            len(source),
            0x40000,
            1,
            0x10,
            0,
            0,
            0,
            stored_size,
            3,
        )
    )
    args.destination.write_bytes(header + xmem)
    print(
        f"Wrote {len(header) + len(xmem)} bytes "
        f"(raw LZX {len(compressed)}, output {len(source)})"
    )


if __name__ == "__main__":
    main()
