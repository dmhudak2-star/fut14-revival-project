#!/usr/bin/env python3
"""Mark only the captured FUT Blaze socket writable in DirtySock's poll."""

from __future__ import annotations

import argparse

from fifa14_connect_bypass import CONNECT_LOG
from fifa14_plain_recv_hook import conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    insn,
    lwz,
    stw,
    verify_module,
)


SELECT_SITE = 0x82D6B944
SELECT_ORIGINAL = bytes.fromhex("4B75EB4D")  # bl 0x824CA490
ERROR_SELECT_SITE = 0x82D6B9B0
ERROR_SELECT_ORIGINAL = bytes.fromhex("4B75EAE1")  # bl 0x824CA490
SELECT_WRAPPER = 0x824CA490
SELECT_STUB = 0x83C8E880
ERROR_SELECT_STUB = 0x83C8E9C0


def cmpw(ra: int, rb: int) -> int:
    return 0x7C000000 | (ra << 16) | (rb << 11)


def build_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),       # -> CONNECT_LOG
        lwz(10, 12, 0x04),           # handle from the latest FUT connect
        lwz(11, 5, 0x04),            # handle in write fd_set
        cmpw(10, 11),
        0,                            # bne fallback
        addi(11, 0, 0),
        stw(11, 6, 0x00),            # clear except fd_set
        addi(11, 0, 1),
        stw(11, 5, 0x00),            # one writable socket
        addi(3, 0, 1),                # select result = one ready socket
        0x4E800020,                   # blr
    ]
    fallback = len(words)
    high = (SELECT_WRAPPER + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, SELECT_WRAPPER & 0xFFFF),
            0x7D6903A6,               # mtctr r11
            0x4E800420,               # bctr; keep callsite LR
        ]
    )
    words[5] = conditional_branch(
        SELECT_STUB + 5 * 4,
        SELECT_STUB + fallback * 4,
        4,
        2,
    )                                 # bne
    return b"".join(insn(word) for word in words)


SELECT_STUB_BYTES = build_stub()
SELECT_PATCH = insn(
    # Preserve link semantics because the stub returns with blr.
    0x48000001 | ((SELECT_STUB - SELECT_SITE) & 0x03FFFFFC)
)


def build_error_stub() -> bytes:
    words = [
        addis(12, 0, 0x83C9),
        addi(12, 12, -0x1900),       # -> CONNECT_LOG
        lwz(10, 12, 0x04),           # latest FUT connect handle
        lwz(11, 4, 0x04),            # handle in read fd_set
        cmpw(10, 11),
        0,                            # bne fallback
        addi(11, 0, 0),
        stw(11, 6, 0x00),            # no exception socket
        addi(11, 0, 1),
        stw(11, 4, 0x00),            # status readable for this handle
        addi(3, 0, 1),
        0x4E800020,                   # blr
    ]
    fallback = len(words)
    high = (SELECT_WRAPPER + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, SELECT_WRAPPER & 0xFFFF),
            0x7D6903A6,
            0x4E800420,
        ]
    )
    words[5] = conditional_branch(
        ERROR_SELECT_STUB + 5 * 4,
        ERROR_SELECT_STUB + fallback * 4,
        4,
        2,
    )
    return b"".join(insn(word) for word in words)


ERROR_SELECT_STUB_BYTES = build_error_stub()
ERROR_SELECT_PATCH = insn(
    0x48000001
    | ((ERROR_SELECT_STUB - ERROR_SELECT_SITE) & 0x03FFFFFC)
)


def site_state(
    client: Xbdm, site: int, original: bytes, patch: bytes
) -> str:
    value = client.read(site, 4)
    if value == original:
        return "original"
    if value == patch:
        return "patched"
    return f"unexpected:{value.hex().upper()}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = (
            site_state(client, SELECT_SITE, SELECT_ORIGINAL, SELECT_PATCH),
            site_state(
                client,
                ERROR_SELECT_SITE,
                ERROR_SELECT_ORIGINAL,
                ERROR_SELECT_PATCH,
            ),
        )
        print(
            f"Connect-ready select sites: writable={states[0]}, "
            f"error={states[1]}"
        )
        if args.action == "status":
            return 0
        if args.action == "apply":
            if states == ("patched", "patched"):
                # Allow the live stub semantics to be upgraded without
                # republishing either callsite.
                client.write(SELECT_STUB, SELECT_STUB_BYTES)
                client.write(ERROR_SELECT_STUB, ERROR_SELECT_STUB_BYTES)
                if (
                    client.read(SELECT_STUB, len(SELECT_STUB_BYTES))
                    != SELECT_STUB_BYTES
                    or client.read(ERROR_SELECT_STUB, len(ERROR_SELECT_STUB_BYTES))
                    != ERROR_SELECT_STUB_BYTES
                ):
                    raise RuntimeError("Live select stub upgrade failed")
                print("Verified: live connect-ready stubs upgraded.")
                return 0
            if any(value not in ("original", "patched") for value in states):
                raise RuntimeError("Refusing to overwrite unexpected code")
            for address, stub in (
                (SELECT_STUB, SELECT_STUB_BYTES),
                (ERROR_SELECT_STUB, ERROR_SELECT_STUB_BYTES),
            ):
                cave = client.read(address, len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError(
                        f"Select code cave 0x{address:08X} is not free"
                    )
                client.write(address, stub)
                if client.read(address, len(stub)) != stub:
                    raise RuntimeError(
                        f"Select stub verification failed at 0x{address:08X}"
                    )
            if states[0] == "original":
                client.write(SELECT_SITE, SELECT_PATCH)
            if states[1] == "original":
                client.write(ERROR_SELECT_SITE, ERROR_SELECT_PATCH)
            verified = (
                site_state(
                    client, SELECT_SITE, SELECT_ORIGINAL, SELECT_PATCH
                ),
                site_state(
                    client,
                    ERROR_SELECT_SITE,
                    ERROR_SELECT_ORIGINAL,
                    ERROR_SELECT_PATCH,
                ),
            )
            if verified != ("patched", "patched"):
                client.write(SELECT_SITE, SELECT_ORIGINAL)
                client.write(ERROR_SELECT_SITE, ERROR_SELECT_ORIGINAL)
                raise RuntimeError("Select patch verification failed")
            print(
                "Verified: captured FUT socket polls writable without error."
            )
            return 0
        if states == ("original", "original"):
            print("Already restored.")
            return 0
        if any(value not in ("original", "patched") for value in states):
            raise RuntimeError("Refusing to restore unexpected code")
        if states[0] == "patched":
            client.write(SELECT_SITE, SELECT_ORIGINAL)
        if states[1] == "patched":
            client.write(ERROR_SELECT_SITE, ERROR_SELECT_ORIGINAL)
        restored = (
            site_state(client, SELECT_SITE, SELECT_ORIGINAL, SELECT_PATCH),
            site_state(
                client,
                ERROR_SELECT_SITE,
                ERROR_SELECT_ORIGINAL,
                ERROR_SELECT_PATCH,
            ),
        )
        if restored != ("original", "original"):
            raise RuntimeError("Select restore verification failed")
        print("Verified: original select poll restored.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
