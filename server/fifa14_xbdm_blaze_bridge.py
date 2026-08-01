#!/usr/bin/env python3
"""Bridge FIFA 14's plaintext Blaze stream to the local protocol server.

Retail Xbox 360 XNet encapsulates the title traffic before it reaches the LAN.
For development on an owned RGH/JTAG console, the existing volatile DirtySock
send/recv hooks expose the exact pre-XNet byte stream.  This bridge reads each
outgoing frame through XBDM, dispatches it through ``Fifa14Protocol`` and queues
the resulting reply on the same title socket.  It does not patch game state or
front-end events; it only supplies the server side of the protocol boundary.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
TOOLS = REPOSITORY / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from fifa14_plain_recv_hook import (  # noqa: E402
    MAX_PENDING_PAYLOAD,
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_PAYLOAD,
    PENDING_SOCKET,
)
from fifa14_plain_send_hook import (  # noqa: E402
    COUNTER,
    RECORD_COUNT,
    RECORD_SIZE,
    RING,
)
from fifa14_plain_send_watch import Xbdm, u32  # noqa: E402
from fifa14_blaze_server import (  # noqa: E402
    ClientState,
    Fifa14Protocol,
    Journal,
    normal_header_size,
)


def wait_pending_empty(client: Xbdm, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if u32(client.read(PENDING_LENGTH, 4), 0) == 0:
            return
        time.sleep(0.005)
    pending = u32(client.read(PENDING_LENGTH, 4), 0)
    raise TimeoutError(f"previous response still has {pending} queued bytes")


def queue_responses(
    client: Xbdm,
    responses: list[bytes],
    socket_value: int,
) -> None:
    payload = b"".join(responses)
    if not socket_value:
        raise RuntimeError("outgoing Blaze record has no DirtySock owner")
    if not 0 < len(payload) <= MAX_PENDING_PAYLOAD:
        raise RuntimeError(
            f"response bundle is {len(payload)} bytes; queue limit is "
            f"{MAX_PENDING_PAYLOAD}"
        )
    wait_pending_empty(client, 10.0)
    client.write(PENDING_PAYLOAD, payload)
    client.write(PENDING_CURSOR, PENDING_PAYLOAD.to_bytes(4, "big"))
    client.write(PENDING_SOCKET, socket_value.to_bytes(4, "big"))
    # Publication barrier is provided by the final aligned setmem store.
    client.write(PENDING_LENGTH, len(payload).to_bytes(4, "big"))


def request_from_record(client: Xbdm, record: bytes) -> bytes:
    address = u32(record, 4)
    length = u32(record, 8)
    if not address or not 12 <= length <= 0x10000:
        raise ValueError(f"invalid send buffer 0x{address:08X}+0x{length:X}")
    snapshot = record[0x20 : 0x20 + min(length, 0x40)]
    if length <= len(snapshot):
        return snapshot
    # The ring snapshot protects the routing header.  Read the remaining TDF
    # payload immediately; the bridge polls faster than the title reuses its
    # normal request buffer in the observed synchronous Blaze flow.
    return snapshot + client.read(address + len(snapshot), length - len(snapshot))


def frame_route(request: bytes) -> tuple[int, int, int]:
    header_size = normal_header_size(request)
    declared = int.from_bytes(request[:2], "big")
    if len(request) != header_size + declared:
        raise ValueError(
            f"truncated frame {len(request)} != {header_size}+{declared}"
        )
    component = int.from_bytes(request[2:4], "big")
    command = int.from_bytes(request[4:6], "big")
    transaction = ((request[9] & 0x0F) << 16) | int.from_bytes(
        request[10:12], "big"
    )
    return component, command, transaction


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox IP address")
    parser.add_argument("--advertise", required=True)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument(
        "--journal",
        type=Path,
        default=REPOSITORY / "runtime" / "xbdm-blaze-bridge.jsonl",
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="consume records already present when the bridge starts",
    )
    args = parser.parse_args()

    journal = Journal(args.journal)
    protocol = Fifa14Protocol(args.advertise, 10041, journal)
    states: dict[int, ClientState] = {}
    client = Xbdm(args.host)
    try:
        current = u32(client.read(COUNTER, 4), 0)
        seen = 0 if args.from_start else current
        journal.event("bridge_ready", xbox=args.host, sequence=seen)
        print("XBDM_BLAZE_BRIDGE_READY", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            current = u32(client.read(COUNTER, 4), 0)
            if current == seen:
                time.sleep(0.005)
                continue
            first = seen + 1
            if current - seen > RECORD_COUNT:
                first = current - RECORD_COUNT + 1
                journal.event(
                    "ring_overrun", expected=seen + 1, resumed=first
                )
            for sequence in range(first, current + 1):
                slot = sequence & (RECORD_COUNT - 1)
                record = client.read(RING + slot * RECORD_SIZE, RECORD_SIZE)
                if u32(record, 0) != sequence:
                    journal.event("record_race", sequence=sequence)
                    continue
                socket_value = u32(record, 20)
                try:
                    request = request_from_record(client, record)
                    try:
                        component, command, transaction = frame_route(request)
                    except ValueError as error:
                        # The DirtySock hook observes every title send, not only
                        # Blaze.  HTTP, telemetry and other sockets must not
                        # terminate the local protocol bridge.
                        journal.event(
                            "bridge_non_blaze_ignored",
                            sequence=sequence,
                            socket=f"0x{socket_value:08X}",
                            bytes=len(request),
                            prefix=request[:32].hex().upper(),
                            reason=str(error),
                        )
                        continue
                    state = states.setdefault(
                        socket_value,
                        ClientState(
                            connection_id=socket_value,
                            peer=(args.host, 0),
                            local_port=0,
                        ),
                    )
                    state.request_count += 1
                    journal.frame("request", state, request)
                    responses = protocol.handle(request, state)
                    for response in responses:
                        journal.frame("response", state, response)
                    queue_responses(client, responses, socket_value)
                    journal.event(
                        "bridge_reply_queued",
                        sequence=sequence,
                        socket=f"0x{socket_value:08X}",
                        component=component,
                        command=command,
                        transaction=transaction,
                        bytes=sum(len(item) for item in responses),
                    )
                except Exception as error:
                    journal.event(
                        "bridge_error",
                        sequence=sequence,
                        socket=f"0x{socket_value:08X}",
                        error=f"{type(error).__name__}: {error}",
                    )
                    raise
            seen = current
        journal.event("bridge_window_ended", sequence=seen)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nBridge interrupted.", flush=True)
        raise SystemExit(130)
    except Exception as error:
        print(f"Bridge error: {error}", flush=True)
        raise SystemExit(1)
