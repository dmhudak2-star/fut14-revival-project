#!/usr/bin/env python3
"""Inject Redirector, Util/Ping, and QoS replies as separate recv events."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from blaze_tdf import Field, INTEGER, encode_field, encode_frame
from fifa14_plain_recv_hook import (
    MAX_PENDING_PAYLOAD,
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_PAYLOAD,
    PENDING_SOCKET,
)
from fifa14_plain_send_hook import COUNTER, RECORD_COUNT, RECORD_SIZE, RING
from fifa14_plain_send_watch import Xbdm, u32


REDIRECTOR_ROUTE = bytes.fromhex("00050001")
PING_ROUTE = bytes.fromhex("00090002")
QOS_ROUTE = bytes.fromhex("78020014")


def make_empty_reply(request: bytes) -> bytes:
    if len(request) < 12:
        raise RuntimeError("Blaze request header is truncated")
    # 0 payload, same component/command, error 0, response type 1,
    # and the exact 24-bit transaction id from the request.
    return (
        b"\0\0"
        + request[2:6]
        + b"\0\0"
        + b"\x10"
        + request[9:12]
    )


def make_ping_reply(request: bytes) -> bytes:
    if len(request) < 12:
        raise RuntimeError("Blaze Ping request header is truncated")
    transaction = (
        ((request[9] & 0x0F) << 16)
        | int.from_bytes(request[10:12], "big")
    )
    payload = encode_field(Field("STIM", INTEGER, int(time.time())))
    return encode_frame(9, 2, 0, 1, transaction, payload)


def make_ping_qos_bundle(request: bytes) -> tuple[bytes, int]:
    ping = make_ping_reply(request)
    ping_transaction = (
        ((request[9] & 0x0F) << 16)
        | int.from_bytes(request[10:12], "big")
    )
    qos_transaction = (ping_transaction + 1) & 0xFFFFF
    qos = encode_frame(0x7802, 20, 0, 1, qos_transaction, b"")
    return ping + qos, qos_transaction


def match_transaction(response: bytes, request: bytes) -> bytes:
    if len(response) < 12 or len(request) < 12:
        raise RuntimeError("Blaze frame header is truncated")
    patched = bytearray(response)
    patched[9:12] = request[9:12]
    return bytes(patched)


def wait_pending_empty(
    client: Xbdm, timeout: float, label: str
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = u32(client.read(PENDING_LENGTH, 4), 0)
        if pending == 0:
            return
        time.sleep(0.01)
    pending = u32(client.read(PENDING_LENGTH, 4), 0)
    raise RuntimeError(
        f"Timed out waiting to inject {label}; {pending} bytes remain"
    )


def queue_reply(
    client: Xbdm,
    response: bytes,
    socket_value: int,
    label: str,
) -> None:
    if not 0 < len(response) <= MAX_PENDING_PAYLOAD:
        raise RuntimeError(f"Invalid {label} response length")
    if not socket_value:
        raise RuntimeError(f"Cannot queue {label} without a socket owner")
    wait_pending_empty(client, 10.0, label)
    client.write(PENDING_PAYLOAD, response)
    client.write(PENDING_CURSOR, PENDING_PAYLOAD.to_bytes(4, "big"))
    if socket_value:
        client.write(PENDING_SOCKET, socket_value.to_bytes(4, "big"))
    client.write(PENDING_LENGTH, len(response).to_bytes(4, "big"))
    print(
        f"  queued {label}: {len(response)} bytes "
        f"txn={int.from_bytes(response[9:12], 'big')} "
        f"socket=0x{socket_value:08X}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument(
        "--redirector-response",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_postauth_router_test"),
    )
    parser.add_argument(
        "--followups-in-xbox",
        action="store_true",
        help=(
            "Let the Xbox flow hook answer Ping; QoS is also delegated unless "
            "--qos-delay is non-negative"
        ),
    )
    parser.add_argument(
        "--redirector-in-xbox",
        action="store_true",
        help="Only observe Redirector because the Xbox flow hook injects it",
    )
    parser.add_argument(
        "--redirector-delay",
        type=float,
        default=0.0,
        help="Delay the Mac-side Redirector reply after observing its request",
    )
    parser.add_argument(
        "--qos-delay",
        type=float,
        default=-1.0,
        help=(
            "Queue QoS on the Mac after this delay; a negative value keeps "
            "--followups-in-xbox delegation"
        ),
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="Process records from sequence 1 even if sends precede startup",
    )
    args = parser.parse_args()

    redirector = args.redirector_response.read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)

    client = Xbdm(args.host)
    try:
        current_at_start = u32(client.read(COUNTER, 4), 0)
        seen = 0 if args.from_start else current_at_start
        print(f"Initial sequence: {seen}", flush=True)
        print("POSTAUTH_ROUTER_READY", flush=True)
        deadline = time.monotonic() + args.seconds
        bundled_qos_transactions: set[tuple[int, int]] = set()

        while time.monotonic() < deadline:
            current = u32(client.read(COUNTER, 4), 0)
            if current == seen:
                time.sleep(0.01)
                continue
            first = seen + 1
            if current - seen > RECORD_COUNT:
                first = current - RECORD_COUNT + 1
            for sequence in range(first, current + 1):
                slot = sequence & (RECORD_COUNT - 1)
                record = client.read(
                    RING + slot * RECORD_SIZE, RECORD_SIZE
                )
                if u32(record, 0) != sequence:
                    continue
                address = u32(record, 4)
                length = u32(record, 8)
                socket_value = u32(record, 20)
                if not address or not 12 <= length <= 0x10000:
                    continue
                # The game reuses its send buffer almost immediately.  The
                # hook's in-ring snapshot is authoritative for routing and
                # transaction matching; a later getmem of `address` may
                # already contain the following request.
                snapshot = record[0x20 : 0x20 + min(length, 0x40)]
                request = snapshot
                if length > len(snapshot):
                    try:
                        request = snapshot + client.read(
                            address + len(snapshot), length - len(snapshot)
                        )
                    except Exception:
                        request = snapshot
                (args.output / f"send_{sequence:06d}_{length:04d}.bin").write_bytes(
                    request
                )
                route = snapshot[2:6]
                txn = (
                    ((snapshot[9] & 0x0F) << 16)
                    | int.from_bytes(snapshot[10:12], "big")
                )
                print(
                    f"seq={sequence} route={route.hex().upper()} "
                    f"txn={txn} len={length}",
                    flush=True,
                )
                if route == REDIRECTOR_ROUTE:
                    if args.redirector_in_xbox:
                        print(
                            "  Redirector reply delegated to Xbox flow hook",
                            flush=True,
                        )
                        continue
                    if args.redirector_delay > 0:
                        time.sleep(args.redirector_delay)
                    queue_reply(
                        client,
                        match_transaction(redirector, snapshot),
                        socket_value,
                        "Redirector",
                    )
                elif route == PING_ROUTE:
                    if args.followups_in_xbox:
                        print(
                            "  Ping reply delegated to Xbox flow hook",
                            flush=True,
                        )
                        continue
                    bundle, qos_transaction = make_ping_qos_bundle(snapshot)
                    queue_reply(
                        client,
                        bundle,
                        socket_value,
                        "Util/Ping+STIM + QoS bundle",
                    )
                    bundled_qos_transactions.add(
                        (qos_transaction, socket_value)
                    )
                elif route == QOS_ROUTE:
                    if args.followups_in_xbox and args.qos_delay < 0:
                        print(
                            "  QoS reply delegated to Xbox flow hook",
                            flush=True,
                        )
                    elif (txn, socket_value) in bundled_qos_transactions:
                        print(
                            "  QoS reply was already included with Ping",
                            flush=True,
                        )
                        bundled_qos_transactions.remove(
                            (txn, socket_value)
                        )
                    else:
                        if args.qos_delay > 0:
                            time.sleep(args.qos_delay)
                        queue_reply(
                            client,
                            make_empty_reply(snapshot),
                            socket_value,
                            "QoS",
                        )
            seen = current
        print("Capture window ended.", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}", flush=True)
        raise SystemExit(1)
