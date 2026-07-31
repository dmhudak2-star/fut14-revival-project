#!/usr/bin/env python3
"""Save plaintext buffers logged by fifa14_plain_recv_log_hook.py."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from fifa14_plain_recv_log_hook import (
    LOG_COUNTER,
    LOG_RECORD_COUNT,
    LOG_RECORD_SIZE,
    LOG_RING,
)
from fifa14_plain_send_hook import Xbdm


def u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_plain_receives"),
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    client = Xbdm(args.host)
    try:
        seen = u32(client.read(LOG_COUNTER, 4), 0)
        print(f"Initial receive sequence: {seen}", flush=True)
        print("PLAIN_RECV_LOG_READY", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            current = u32(client.read(LOG_COUNTER, 4), 0)
            if current != seen:
                first = seen + 1
                if current - seen > LOG_RECORD_COUNT:
                    first = current - LOG_RECORD_COUNT + 1
                    print(
                        f"Warning: receive ring overrun; sequence {first}",
                        flush=True,
                    )
                for sequence in range(first, current + 1):
                    slot = sequence & (LOG_RECORD_COUNT - 1)
                    record = client.read(
                        LOG_RING + slot * LOG_RECORD_SIZE,
                        LOG_RECORD_SIZE,
                    )
                    if u32(record, 0) != sequence:
                        print(f"recv seq={sequence}: slot changed", flush=True)
                        continue
                    address = u32(record, 4)
                    length = u32(record, 8)
                    socket_object = u32(record, 12)
                    data = b""
                    if address and 0 < length <= 0x10000:
                        data = client.read(address, length)
                    destination = args.output / (
                        f"recv_{sequence:06d}_{length:04d}.bin"
                    )
                    destination.write_bytes(data)
                    print(
                        f"recv seq={sequence} len={length} "
                        f"buf=0x{address:08X} "
                        f"socket=0x{socket_object:08X} "
                        f"data={data[:96].hex().upper()}",
                        flush=True,
                    )
                seen = current
            time.sleep(0.02)
        print(f"Final receive sequence: {seen}", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nReceive watcher stopped.")
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
