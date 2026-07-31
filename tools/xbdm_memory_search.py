#!/usr/bin/env python3
"""Search mapped Xbox 360 memory without first writing a full dump."""

from __future__ import annotations

import argparse
import re
import socket


DEFAULT_PATTERNS = {
    "ea-ip-network": bytes.fromhex("9F99344B"),
    "ea-ip-reversed": bytes.fromhex("4B34999F"),
    "ea-ip-ascii": b"159.153.52.75",
}


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=10)
        self.sock.settimeout(15)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith(b"201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def multiline(self, command: str) -> list[bytes]:
        self.sock.sendall(command.encode("ascii") + b"\r\n")
        status = self.line()
        if not status.startswith(b"202-"):
            raise RuntimeError(status.decode("ascii", "replace"))
        result = []
        while True:
            line = self.line()
            if line == b".":
                return result
            result.append(line)

    def walkmem(self) -> list[tuple[int, int, int]]:
        regions = []
        for raw in self.multiline("walkmem"):
            line = raw.decode("ascii", "replace")
            match = re.search(
                r"base=0x([0-9a-f]+) size=0x([0-9a-f]+) "
                r"protect=0x([0-9a-f]+)",
                line,
                re.IGNORECASE,
            )
            if match:
                regions.append(tuple(int(value, 16) for value in match.groups()))
        return regions

    def read(self, address: int, length: int) -> bytes:
        lines = self.multiline(
            f"getmem addr=0x{address:08X} length=0x{length:X}"
        )
        encoded = b"".join(line.strip() for line in lines)
        if not re.fullmatch(rb"[0-9A-Fa-f]+", encoded):
            raise RuntimeError("non-hexadecimal getmem response")
        data = bytes.fromhex(encoded.decode("ascii"))
        if len(data) != length:
            raise RuntimeError(f"short read: {len(data):#x}/{length:#x}")
        return data


def number(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument(
        "--ascii",
        action="append",
        default=[],
        metavar="TEXT",
        help="search an ASCII string; may be repeated (defaults to EA endpoint patterns)",
    )
    parser.add_argument("--minimum", type=number, default=0)
    parser.add_argument("--maximum", type=number, default=0xA0000000)
    parser.add_argument("--max-region", type=number, default=0x02000000)
    parser.add_argument("--chunk-size", type=number, default=0x4000)
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list matching mapped regions without reading their contents",
    )
    args = parser.parse_args()

    patterns = (
        {f"ascii:{text}": text.encode("ascii") for text in args.ascii}
        if args.ascii
        else DEFAULT_PATTERNS
    )

    client = Xbdm(args.host)
    regions = [
        region
        for region in client.walkmem()
        if args.minimum <= region[0] < args.maximum
        and region[1] <= args.max_region
    ]
    total = sum(size for _, size, _ in regions)
    print(f"Scanning {len(regions)} regions ({total:#x} bytes).", flush=True)
    if args.list_only:
        for base, size, protection in regions:
            print(
                f"base=0x{base:08X} size=0x{size:08X} "
                f"protect=0x{protection:X}"
            )
        return 0

    overlap = max(len(pattern) for pattern in patterns.values()) - 1
    completed = 0
    hits = 0
    for base, size, protection in regions:
        tail = b""
        offset = 0
        while offset < size:
            amount = min(args.chunk_size, size - offset)
            try:
                data = client.read(base + offset, amount)
            except Exception as error:
                print(
                    f"\nSkip 0x{base + offset:08X}+0x{amount:X}: {error}",
                    flush=True,
                )
                tail = b""
                offset += amount
                completed += amount
                continue

            window = tail + data
            window_base = base + offset - len(tail)
            for name, pattern in patterns.items():
                start = 0
                while True:
                    found = window.find(pattern, start)
                    if found < 0:
                        break
                    address = window_base + found
                    if address >= base + offset - len(tail):
                        print(
                            f"\n{name}: 0x{address:08X} "
                            f"(region 0x{base:08X}, protect 0x{protection:X})",
                            flush=True,
                        )
                        hits += 1
                    start = found + 1
            tail = window[-overlap:]
            offset += amount
            completed += amount
            print(
                f"\r{completed:#010x}/{total:#010x} "
                f"({completed * 100.0 / total:6.2f}%) hits={hits}",
                end="",
                flush=True,
            )

    print(f"\nSearch complete: {hits} hit(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
