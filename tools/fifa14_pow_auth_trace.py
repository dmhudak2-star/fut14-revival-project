#!/usr/bin/env python3
"""Trace the powdllzf FUT authentication function that builds `pow/auth`."""

from __future__ import annotations

import argparse
import re

from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    insn,
    lwz,
    stw,
)


SITE = 0x897381E8
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12
STUB = 0x897BF000
JOURNAL = 0x897BF080
JOURNAL_SIZE = 0x20


def verify_powdll(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="powdllzf\.xex\.dll"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None or "base=0x89700000" not in module.lower():
        raise RuntimeError(f"Unexpected or missing powdllzf module: {module}")


def build_stub() -> bytes:
    words = [
        addis(12, 0, 0x897C),
        addi(12, 12, -0x0F80),      # JOURNAL
        lwz(11, 12, 0x00),
        addi(11, 11, 1),
        stw(11, 12, 0x00),
        stw(3, 12, 0x04),           # POW service/auth object
        int.from_bytes(ORIGINAL, "big"),
        0,
    ]
    words[-1] = branch(STUB + (len(words) - 1) * 4, SITE + 4, False)
    return b"".join(insn(word) for word in words)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    args = parser.parse_args()

    stub = build_stub()
    patch = insn(branch(SITE, STUB, False))
    client = Xbdm(args.host)
    try:
        verify_powdll(client)
        current = client.read(SITE, 4)
        state = (
            "original"
            if current == ORIGINAL
            else "patched"
            if current == patch
            else f"unexpected:{current.hex().upper()}"
        )
        print(f"pow/auth trace: {state}")
        if args.action in ("status", "read"):
            raw = client.read(JOURNAL, JOURNAL_SIZE)
            print(f"invocation_count = {int.from_bytes(raw[0:4], 'big')}")
            print(
                f"auth_object      = "
                f"0x{int.from_bytes(raw[4:8], 'big'):08X}"
            )
            return 0
        if args.action == "apply":
            if state not in ("original", "patched"):
                raise RuntimeError("Unexpected pow/auth function entry")
            cave = client.read(STUB, len(stub))
            if cave not in (bytes(len(stub)), stub):
                raise RuntimeError("pow/auth trace cave is not free")
            client.write(JOURNAL, bytes(JOURNAL_SIZE))
            client.write(STUB, stub)
            client.write(SITE, patch)
            if client.read(SITE, 4) != patch:
                raise RuntimeError("pow/auth trace verification failed")
            print("Verified: CardsDLL pow/auth entry trace armed.")
            return 0
        if state == "patched":
            client.write(SITE, ORIGINAL)
        elif state != "original":
            raise RuntimeError("Unexpected pow/auth function entry")
        print("Verified: original pow/auth function entry restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
