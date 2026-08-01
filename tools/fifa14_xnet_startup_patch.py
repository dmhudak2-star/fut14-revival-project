#!/usr/bin/env python3
"""Force FIFA 14's built-in XNet ``-nosecure`` startup path.

The supported title builds a 13-byte XNetStartupParams structure at
0x82D6DCF8.  Its existing ``-nosecure`` branch stores 1 in cfgFlags; replacing
the conditional branch at 0x82D6DD00 with a NOP makes that store unconditional.
The patch is title-local and volatile.
"""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import Xbdm, verify_module


NOSECURE_MODE_BRANCH = 0x82D6DBFC
NOSECURE_MODE_ORIGINAL = bytes.fromhex("41820014")
NOSECURE_MODE_PATCHED = bytes.fromhex("60000000")
XNET_BYPASS_BRANCH = 0x82D6DD00
XNET_BYPASS_ORIGINAL = bytes.fromhex("409A0008")
XNET_BYPASS_PATCHED = bytes.fromhex("60000000")


def state(client: Xbdm) -> str:
    mode = client.read(NOSECURE_MODE_BRANCH, 4)
    startup = client.read(XNET_BYPASS_BRANCH, 4)
    if mode == NOSECURE_MODE_ORIGINAL and startup == XNET_BYPASS_ORIGINAL:
        return "retail"
    if mode == NOSECURE_MODE_PATCHED and startup == XNET_BYPASS_PATCHED:
        return "nosecure"
    return f"unexpected:{mode.hex().upper()}/{startup.hex().upper()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = state(client)
        print(f"FIFA XNetStartup mode: {current}")
        if args.action == "status":
            return 0
        if args.action == "apply":
            if current == "nosecure":
                return 0
            if current != "retail":
                raise RuntimeError("Refusing to patch an unknown XNetStartup branch")
            client.write(NOSECURE_MODE_BRANCH, NOSECURE_MODE_PATCHED)
            client.write(XNET_BYPASS_BRANCH, XNET_BYPASS_PATCHED)
            if state(client) != "nosecure":
                client.write(NOSECURE_MODE_BRANCH, NOSECURE_MODE_ORIGINAL)
                client.write(XNET_BYPASS_BRANCH, XNET_BYPASS_ORIGINAL)
                raise RuntimeError("Full nosecure mode verification failed")
            print("Verified: FIFA will take its complete -nosecure path.")
            return 0
        if current == "retail":
            return 0
        if current != "nosecure":
            raise RuntimeError("Refusing to restore an unknown XNetStartup branch")
        client.write(NOSECURE_MODE_BRANCH, NOSECURE_MODE_ORIGINAL)
        client.write(XNET_BYPASS_BRANCH, XNET_BYPASS_ORIGINAL)
        if state(client) != "retail":
            raise RuntimeError("XNetStartup restore verification failed")
        print("Verified: retail XNetStartup branch restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
