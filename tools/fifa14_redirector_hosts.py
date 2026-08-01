#!/usr/bin/env python3
"""Inspect, apply or restore FIFA 14 Redirector host strings in memory."""

from __future__ import annotations

import argparse
import ipaddress

from fifa14_early_redirector_patch import HOSTS
from fifa14_plain_send_hook import Xbdm, verify_module


def replacement_for(original: bytes, local_ip: str) -> bytes:
    target = local_ip.encode("ascii") + b"\0"
    if len(target) > len(original):
        raise RuntimeError("Local IPv4 does not fit the Redirector hostname slot")
    return target + b"\0" * (len(original) - len(target))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    parser.add_argument("--local-ip", required=True)
    args = parser.parse_args()
    local_ip = str(ipaddress.IPv4Address(args.local_ip))

    client = Xbdm(args.host)
    try:
        verify_module(client)
        for address, original in HOSTS:
            replacement = replacement_for(original, local_ip)
            current = client.read(address, len(original))
            current_state = (
                "original"
                if current == original
                else "local"
                if current == replacement
                else "unexpected"
            )
            print(f"0x{address:08X}: {current_state}")
            if args.action == "status":
                continue
            expected_source = original if args.action == "apply" else replacement
            target = replacement if args.action == "apply" else original
            if current == target:
                continue
            if current != expected_source:
                raise RuntimeError(
                    f"Refusing unexpected hostname bytes at 0x{address:08X}"
                )
            client.write(address, target)
            if client.read(address, len(target)) != target:
                raise RuntimeError(f"Verification failed at 0x{address:08X}")
        if args.action != "status":
            verb = "patched" if args.action == "apply" else "restored"
            print(f"Verified: Redirector host strings {verb}.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
