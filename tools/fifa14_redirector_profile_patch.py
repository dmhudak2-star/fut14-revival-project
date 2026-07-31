#!/usr/bin/env python3
"""Volatile FIFA 14 Redirector profile substitution for the known Xenon build."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import Xbdm, verify_module


PROFILE_POINTER = 0x83CEFF1C
XBOX360_SECURE = 0x82134334
STANDARD_SECURE = 0x8213435C
STANDARD_INSECURE = 0x82134370


def encoded(value: int) -> bytes:
    return value.to_bytes(4, "big")


def state(value: int) -> str:
    if value == XBOX360_SECURE:
        return "xbox360Secure_v3"
    if value == STANDARD_SECURE:
        return "standardSecure_v3"
    if value == STANDARD_INSECURE:
        return "standardInsecure_v3"
    return f"unexpected:0x{value:08X}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument(
        "action",
        choices=(
            "status",
            "apply",
            "apply-secure",
            "apply-insecure",
            "restore",
        ),
    )
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        current = int.from_bytes(client.read(PROFILE_POINTER, 4), "big")
        print(f"Redirector profile pointer: {state(current)}")

        if args.action == "status":
            return 0

        if args.action in ("apply", "apply-secure", "apply-insecure"):
            target = (
                STANDARD_INSECURE
                if args.action == "apply-insecure"
                else STANDARD_SECURE
            )
            if current == target:
                print("Already patched.")
                return 0
            if current not in (
                XBOX360_SECURE,
                STANDARD_SECURE,
                STANDARD_INSECURE,
            ):
                raise RuntimeError("Refusing to overwrite an unknown pointer")
            client.write(PROFILE_POINTER, encoded(target))
            expected = target
        else:
            if current == XBOX360_SECURE:
                print("Already restored.")
                return 0
            if current not in (STANDARD_SECURE, STANDARD_INSECURE):
                raise RuntimeError("Refusing to restore an unknown pointer")
            client.write(PROFILE_POINTER, encoded(XBOX360_SECURE))
            expected = XBOX360_SECURE

        verified = int.from_bytes(client.read(PROFILE_POINTER, 4), "big")
        if verified != expected:
            raise RuntimeError("Profile pointer verification failed")
        print(f"Verified: {state(verified)}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
