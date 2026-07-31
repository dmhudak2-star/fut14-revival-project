#!/usr/bin/env python3
"""Wrap a loose EA resource in the uncompressed chunk container."""

import argparse
import struct
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    payload = args.source.read_bytes()
    header = (
        b"chunkunc"
        + struct.pack(
            ">10I",
            2,              # container version
            len(payload),   # full uncompressed size
            0x40000,        # logical chunk size
            1,              # chunk count
            0x10,
            0,
            0,
            0,
            len(payload),   # stored size
            4,              # uncompressed block
        )
    )
    args.destination.write_bytes(header + payload)
    print(f"Wrote {len(header) + len(payload)} bytes")


if __name__ == "__main__":
    main()
