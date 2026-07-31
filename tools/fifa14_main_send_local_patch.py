#!/usr/bin/env python3
"""Upgrade the live send logger to acknowledge only the local FUT socket."""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import PENDING_LENGTH
from fifa14_plain_send_hook import (
    LOGGER_SEND_STUB_BYTES,
    ORIGINAL_SEND_CALL,
    PATCHED_SEND_CALL,
    SEND_CALLSITE,
    SEND_STUB,
    SEND_STUB_BYTES,
    Xbdm,
    verify_module,
)


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x80):
        client.write(address + offset, data[offset : offset + 0x80])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        call = client.read(SEND_CALLSITE, 4)
        cave = client.read(SEND_STUB, len(SEND_STUB_BYTES))
        if cave == SEND_STUB_BYTES:
            stub_state = "local-ack"
        elif (
            cave[: len(LOGGER_SEND_STUB_BYTES)] == LOGGER_SEND_STUB_BYTES
            and not any(cave[len(LOGGER_SEND_STUB_BYTES) :])
        ):
            stub_state = "logger"
        else:
            stub_state = "unexpected"
        call_state = (
            "hooked"
            if call == PATCHED_SEND_CALL
            else "original"
            if call == ORIGINAL_SEND_CALL
            else f"unexpected:{call.hex().upper()}"
        )
        print(f"Send callsite: {call_state}; stub: {stub_state}")
        if args.action == "status":
            return 0
        if call_state not in ("hooked", "original") or stub_state not in (
            "logger",
            "local-ack",
        ):
            raise RuntimeError("Refusing to replace unexpected send code")
        if stub_state == "local-ack":
            print("Already upgraded.")
            return 0

        # Unpublish before rewriting the live code cave.
        client.write(SEND_CALLSITE, ORIGINAL_SEND_CALL)
        try:
            write_chunks(client, SEND_STUB, SEND_STUB_BYTES)
            if client.read(SEND_STUB, len(SEND_STUB_BYTES)) != SEND_STUB_BYTES:
                raise RuntimeError("Local send stub verification failed")
            client.write(PENDING_LENGTH, bytes(4))
            client.write(SEND_CALLSITE, PATCHED_SEND_CALL)
            if client.read(SEND_CALLSITE, 4) != PATCHED_SEND_CALL:
                raise RuntimeError("Local send hook publication failed")
        except Exception:
            try:
                client.write(SEND_CALLSITE, ORIGINAL_SEND_CALL)
                write_chunks(client, SEND_STUB, LOGGER_SEND_STUB_BYTES)
                client.write(SEND_CALLSITE, PATCHED_SEND_CALL)
            except Exception:
                pass
            raise
        print(
            "Verified: sends from the captured FUT socket are acknowledged "
            "locally; other sockets remain real."
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
