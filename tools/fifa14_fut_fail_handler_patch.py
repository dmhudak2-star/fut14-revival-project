#!/usr/bin/env python3
"""Temporarily neutralize FIFA 14's FUTFailLoginRecovery handler."""

from __future__ import annotations

import argparse
import sys

from fifa14_fut_gate_patch import Xbdm


ADDRESS = 0x82DABCF0
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
PATCHED = bytes.fromhex("4E800020")   # blr


def describe(data: bytes) -> str:
    if data == ORIGINAL:
        return "original"
    if data == PATCHED:
        return "neutralized"
    return f"unknown ({data.hex().upper()})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        before = client.read(ADDRESS, 4)
        print(f"0x{ADDRESS:08X}: {describe(before)}")
        if args.action == "status":
            return 0

        expected = ORIGINAL if args.action == "apply" else PATCHED
        replacement = PATCHED if args.action == "apply" else ORIGINAL
        if before != expected:
            raise RuntimeError(
                f"Refusing {args.action}: expected {expected.hex().upper()}, "
                f"found {before.hex().upper()}"
            )

        client.write(ADDRESS, replacement)
        after = client.read(ADDRESS, 4)
        if after != replacement:
            raise RuntimeError(f"Verification failed: {after.hex().upper()}")
        print(f"Verified: {describe(after)}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
