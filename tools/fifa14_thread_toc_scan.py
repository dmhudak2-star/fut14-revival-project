#!/usr/bin/env python3
"""List Xbox threads and the live PPC TOC values relevant to FIFA 14."""

from __future__ import annotations

import argparse
import re
import socket
import sys


class Xbdm:
    def __init__(self, host: str):
        self.sock = socket.create_connection((host, 730), timeout=8)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith("201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> str:
        raw = self.reader.readline()
        if not raw:
            raise EOFError("XBDM closed the connection")
        return raw.decode("ascii", "replace").rstrip("\r\n")

    def lines(self, command: str) -> list[str]:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        status = self.line()
        if not status.startswith("202-"):
            raise RuntimeError(f"{command}: {status}")
        result = []
        while True:
            line = self.line()
            if line == ".":
                return result
            result.append(line)

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def fields(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line):
            result[key.lower()] = value.strip('"')
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    xbdm = Xbdm(args.host)
    try:
        raw_threads = xbdm.lines("threads")
        for raw in raw_threads:
            thread = int(raw, 0) & 0xFFFFFFFF
            try:
                info = fields(xbdm.lines(f"threadinfo thread=0x{thread:08X}"))
                context = fields(
                    xbdm.lines(f"getcontext thread=0x{thread:08X} control int")
                )
            except Exception as error:
                print(f"0x{thread:08X} unavailable: {error}")
                continue

            print(
                f"thread=0x{thread:08X} "
                f"start={info.get('start', info.get('startaddr', '?'))} "
                f"iar={context.get('iar', '?')} "
                f"lr={context.get('lr', '?')} "
                f"r2={context.get('r2', '?')} "
                f"priority={info.get('priority', '?')}"
            )
    finally:
        xbdm.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
