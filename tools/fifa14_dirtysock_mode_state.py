#!/usr/bin/env python3
"""Read the live FIFA 14 DirtySock security-mode fields."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import Xbdm, verify_module


DIRTYSOCK_GLOBAL = 0x83DA3E94


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        instance = int.from_bytes(client.read(DIRTYSOCK_GLOBAL, 4), "big")
        if not instance:
            print("DirtySock instance: not initialized")
            return 0
        blob = client.read(instance, 0x230)
        nosecure_socket = blob[0x21A]
        mixed_security = blob[0x21D]
        security_mode = int.from_bytes(blob[0x22C:0x230], "big")
        print(f"DirtySock instance: 0x{instance:08X}")
        print(
            "mode fields: "
            f"+0x21A={nosecure_socket} "
            f"+0x21D={mixed_security} "
            f"+0x22C={security_mode}"
        )
        expected = (0, 1, 0)
        actual = (nosecure_socket, mixed_security, security_mode)
        print("mode: nosecure" if actual == expected else "mode: not-full-nosecure")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
