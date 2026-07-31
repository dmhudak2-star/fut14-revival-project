#!/usr/bin/env python3
"""List an Xbox directory through XBDM's read-only dirlist command."""

from __future__ import annotations

import argparse
import socket


def line(reader) -> str:
    data = reader.readline()
    if not data:
        raise EOFError("XBDM closed the connection")
    return data.decode("ascii", "replace").rstrip("\r\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("path")
    parser.add_argument("--contains", default="")
    args = parser.parse_args()

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(20)
        reader = sock.makefile("rb")
        greeting = line(reader)
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        sock.sendall(f'dirlist name="{args.path}"\r\n'.encode("ascii"))
        response = line(reader)
        if not response.startswith("202"):
            raise RuntimeError(response)
        needle = args.contains.casefold()
        count = 0
        while True:
            entry = line(reader)
            if entry == ".":
                break
            if not needle or needle in entry.casefold():
                print(entry)
                count += 1
    print(f"Matched entries: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
