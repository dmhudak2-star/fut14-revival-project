#!/usr/bin/env python3
"""Print the current plaintext-send ring without waiting for new traffic."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import (
    COUNTER,
    RECORD_COUNT,
    RECORD_SIZE,
    RING,
    Xbdm,
    verify_module,
)


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = u32(client.read(COUNTER, 4), 0)
        first = max(1, current - RECORD_COUNT + 1)
        print(f"sequence_range = {first}..{current}")
        for sequence in range(first, current + 1):
            slot = sequence & (RECORD_COUNT - 1)
            record = client.read(RING + slot * RECORD_SIZE, RECORD_SIZE)
            if u32(record, 0) != sequence:
                continue
            length = u32(record, 8)
            snapshot = record[0x20 : 0x20 + min(length, 0x40)]
            route = snapshot[2:6].hex().upper() if len(snapshot) >= 6 else "-"
            txn = int.from_bytes(snapshot[9:12], "big") if len(snapshot) >= 12 else 0
            print(
                f"seq={sequence:6} route={route:8} txn={txn:7} "
                f"len={length:4} socket=0x{u32(record, 20):08X}"
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
