#!/usr/bin/env python3
"""Search an Xbox 360 memory range for ASCII/hex byte patterns via XBDM."""

from __future__ import annotations

import argparse
import socket


class Xbdm:
    def __init__(self, host: str):
        self.sock = socket.create_connection((host, 730), timeout=10)
        self.sock.settimeout(20)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith(b"201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def read(self, address: int, length: int) -> bytes:
        self.sock.sendall(
            f"getmem addr=0x{address:08X} length=0x{length:X}\r\n".encode("ascii")
        )
        status = self.line()
        if not status.startswith(b"202-"):
            raise RuntimeError(status.decode("ascii", "replace"))
        encoded = bytearray()
        while True:
            line = self.line()
            if line == b".":
                break
            encoded.extend(line.strip())
        data = bytes.fromhex(encoded.decode("ascii"))
        if len(data) != length:
            raise RuntimeError(f"Expected {length} bytes, received {len(data)}")
        return data


def number(value: str) -> int:
    return int(value, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("address", type=number)
    parser.add_argument("size", type=number)
    parser.add_argument("patterns", nargs="+")
    parser.add_argument("--chunk-size", type=number, default=0x20000)
    args = parser.parse_args()

    needles = [p.encode("ascii") for p in args.patterns]
    overlap_size = max(map(len, needles)) - 1
    previous = b""
    client = Xbdm(args.host)
    hits = 0

    offset = 0
    while offset < args.size:
        amount = min(args.chunk_size, args.size - offset)
        block = client.read(args.address + offset, amount)
        searchable = previous + block
        searchable_base = args.address + offset - len(previous)
        for pattern, needle in zip(args.patterns, needles):
            start = 0
            while True:
                found = searchable.find(needle, start)
                if found < 0:
                    break
                absolute = searchable_base + found
                # Avoid reporting an overlap hit twice.
                if absolute + len(needle) > args.address + offset:
                    print(f"{pattern}: 0x{absolute:08X}", flush=True)
                    hits += 1
                start = found + 1
        previous = searchable[-overlap_size:] if overlap_size else b""
        offset += amount
        if offset == args.size or offset % 0x1000000 == 0:
            print(f"Scanned {offset:#010x}/{args.size:#010x}", flush=True)
    print(f"\nFound {hits} matches")


if __name__ == "__main__":
    main()
