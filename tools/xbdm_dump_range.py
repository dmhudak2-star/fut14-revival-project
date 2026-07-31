#!/usr/bin/env python3
"""Dump one mapped Xbox 360 memory range through XBDM getmem."""

from __future__ import annotations

import argparse
import socket
from pathlib import Path


class Xbdm:
    def __init__(self, host: str):
        self.sock = socket.create_connection((host, 730), timeout=10)
        self.sock.settimeout(10)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith(b"201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def read(self, address: int, length: int) -> bytes:
        command = f"getmem addr=0x{address:08X} length=0x{length:X}\r\n"
        self.sock.sendall(command.encode("ascii"))
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
    parser.add_argument("output", type=Path)
    parser.add_argument("--chunk-size", type=number, default=0x1000)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    completed = args.output.stat().st_size if args.output.exists() else 0
    if completed > args.size:
        raise RuntimeError("Existing output is larger than the requested range")

    client = Xbdm(args.host)
    with args.output.open("ab" if completed else "wb") as stream:
        offset = completed
        while offset < args.size:
            amount = min(args.chunk_size, args.size - offset)
            stream.write(client.read(args.address + offset, amount))
            stream.flush()
            offset += amount
            print(
                f"\r{offset:#010x}/{args.size:#010x} "
                f"({offset * 100.0 / args.size:6.2f}%)",
                end="",
                flush=True,
            )
    print(f"\nDump complete: {args.output}")


if __name__ == "__main__":
    main()
