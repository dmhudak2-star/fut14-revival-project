#!/usr/bin/env python3
"""Read plaintext DirtySock sends captured by fifa14_plain_send_hook.py."""

from __future__ import annotations

import argparse
import re
import socket
import time
from pathlib import Path

from fifa14_plain_send_hook import COUNTER, RECORD_COUNT, RECORD_SIZE, RING
from fifa14_plain_recv_hook import (
    MAX_PENDING_PAYLOAD,
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_PAYLOAD,
    PENDING_SOCKET,
)
from fifa14_connect_bypass import (
    CONNECT_CALLSITE,
    CONNECT_LOG,
    ORIGINAL_CONNECT_CALL,
    PATCHED_CONNECT_CALL,
)


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=5)
        self.file = self.sock.makefile("rwb", buffering=0)
        banner = self.file.readline().decode("ascii", "replace").strip()
        if not banner.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner}")

    def close(self) -> None:
        self.file.close()
        self.sock.close()

    def command(self, command: str) -> str:
        self.file.write(command.encode("ascii") + b"\r\n")
        return self.file.readline().decode("ascii", "replace").strip()

    def call_void(self, address: int, poll_seconds: float = 3.0) -> str:
        response = self.command(
            'consolefeatures ver=2 type=0 as=0 '
            f'params="A\\{address:X}\\A\\0\\"'
        )
        deadline = time.monotonic() + poll_seconds
        while time.monotonic() < deadline:
            match = re.search(
                r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response
            )
            if not match:
                return response
            time.sleep(0.025)
            response = self.command(
                "consolefeatures ver=2 "
                f"buf_addr=0x{int(match.group(1), 16):X}"
            )
        return response

    def read(self, address: int, length: int) -> bytes:
        command = f"getmem addr=0x{address:08X} length=0x{length:X}\r\n"
        self.file.write(command.encode("ascii"))
        status = self.file.readline().decode("ascii", "replace").strip()
        if not status.startswith("202"):
            raise RuntimeError(f"getmem failed: {status}")
        lines: list[str] = []
        while True:
            line = self.file.readline().decode("ascii", "replace").strip()
            if line == ".":
                break
            lines.append(line)
        encoded = "".join(lines)
        if not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
            raise RuntimeError("Invalid XBDM memory response")
        return bytes.fromhex(encoded)

    def write(self, address: int, data: bytes) -> None:
        command = (
            f"setmem addr=0x{address:08X} data={data.hex().upper()}\r\n"
        )
        self.file.write(command.encode("ascii"))
        status = self.file.readline().decode("ascii", "replace").strip()
        if not status.startswith("200"):
            raise RuntimeError(f"setmem failed at 0x{address:08X}: {status}")


def u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/fifa14_plain_sends")
    )
    parser.add_argument(
        "--inject-response",
        type=Path,
        help="Queue this ProtoFire frame for the hooked DirtySock recv",
    )
    parser.add_argument(
        "--inject-preauth-response",
        type=Path,
        help="Queue this response after a Util.PreAuth (9/7) request",
    )
    parser.add_argument(
        "--bypass-connect-after-inject",
        action="store_true",
        help="Make subsequent DirtySock connect calls return success",
    )
    parser.add_argument(
        "--revive-preauth-owner",
        action="store_true",
        help="Restore the captured connect handle into a closed PreAuth owner",
    )
    parser.add_argument(
        "--reinsert-preauth-owner",
        action="store_true",
        help="Publish the revived PreAuth owner in DirtySock's active list",
    )
    parser.add_argument(
        "--pump-blaze-after-preauth",
        action="store_true",
        help="Run the top-level Blaze receive/dispatch pump after queuing PreAuth",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    responses = {
        bytes.fromhex("00050001"): args.inject_response,
        bytes.fromhex("00090007"): args.inject_preauth_response,
    }

    client = Xbdm(args.host)
    try:
        seen = u32(client.read(COUNTER, 4), 0)
        print(f"Initial sequence: {seen}", flush=True)
        print("PLAIN_SEND_WATCH_READY", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            current = u32(client.read(COUNTER, 4), 0)
            if current != seen:
                first = seen + 1
                if current - seen > RECORD_COUNT:
                    first = current - RECORD_COUNT + 1
                    print(
                        f"Warning: ring overrun; resuming at sequence {first}",
                        flush=True,
                    )
                for sequence in range(first, current + 1):
                    slot = sequence & (RECORD_COUNT - 1)
                    record = client.read(
                        RING + slot * RECORD_SIZE, RECORD_SIZE
                    )
                    stored_sequence = u32(record, 0)
                    if stored_sequence != sequence:
                        print(
                            f"Sequence {sequence}: slot changed before read",
                            flush=True,
                        )
                        continue
                    buffer_address = u32(record, 4)
                    length = u32(record, 8)
                    destination_address = u32(record, 12)
                    destination_length = u32(record, 16)
                    socket_object = u32(record, 20)
                    copied = record[0x20 : 0x20 + min(length, 0x40)]
                    sockaddr = record[0x60:0x70]
                    ip = ".".join(str(octet) for octet in sockaddr[4:8])
                    port = int.from_bytes(sockaddr[2:4], "big")
                    destination = args.output / (
                        f"send_{sequence:06d}_{length:04d}.bin"
                    )
                    full = copied
                    if 0 < length <= 0x10000 and buffer_address:
                        try:
                            full = client.read(buffer_address, length)
                        except Exception as error:
                            print(
                                f"  full buffer read failed: {error}",
                                flush=True,
                            )
                    destination.write_bytes(full)
                    print(
                        f"seq={sequence} len={length} "
                        f"buf=0x{buffer_address:08X} "
                        f"socket=0x{socket_object:08X} "
                        f"to=0x{destination_address:08X}/"
                        f"{destination_length} {ip}:{port} "
                        f"data={copied.hex().upper()}",
                        flush=True,
                    )
                    route = full[2:6] if len(full) >= 6 else b""
                    response_path = responses.get(route)
                    if response_path is not None and socket_object:
                        owner_state = None
                        try:
                            owner_state = client.read(socket_object, 0xB8)
                            print(
                                "  owner state "
                                f"mode={int.from_bytes(owner_state[0x0C:0x10], 'big')} "
                                f"handle=0x{int.from_bytes(owner_state[0x18:0x1C], 'big'):08X} "
                                f"connected={owner_state[0x43]} "
                                f"recv_pending={owner_state[0xA0]} "
                                f"recv_length={int.from_bytes(owner_state[0xB4:0xB8], 'big')}",
                                flush=True,
                            )
                        except Exception as error:
                            print(
                                f"  owner state read failed: {error}",
                                flush=True,
                            )
                        if (
                            args.revive_preauth_owner
                            and route == bytes.fromhex("00090007")
                            and owner_state is not None
                            and owner_state[0x18:0x1C] == bytes.fromhex("FFFFFFFF")
                        ):
                            connect_handle = client.read(CONNECT_LOG + 4, 4)
                            if connect_handle not in (
                                bytes(4),
                                bytes.fromhex("FFFFFFFF"),
                            ):
                                # Re-enable only the captured FUT owner.  All
                                # network I/O for it remains intercepted by the
                                # send/recv memory transport.
                                client.write(socket_object + 0x18, connect_handle)
                                client.write(socket_object + 0x1C, bytes(4))
                                client.write(socket_object + 0x14, b"\x01")
                                client.write(socket_object + 0x43, b"\x01")
                                print(
                                    "  revived PreAuth owner with handle "
                                    f"0x{int.from_bytes(connect_handle, 'big'):08X}",
                                    flush=True,
                                )
                                if args.reinsert_preauth_owner:
                                    global_state = int.from_bytes(
                                        client.read(0x83DA3E90, 4), "big"
                                    )
                                    if not global_state:
                                        raise RuntimeError(
                                            "DirtySock global state is null"
                                        )
                                    current_head = client.read(global_state, 4)
                                    if int.from_bytes(current_head, "big") != socket_object:
                                        # Initialize linkage first and publish
                                        # the new head last.
                                        client.write(socket_object, current_head)
                                        client.write(
                                            global_state,
                                            socket_object.to_bytes(4, "big"),
                                        )
                                    print(
                                        "  reinserted PreAuth owner into "
                                        "DirtySock active list",
                                        flush=True,
                                    )
                        response = bytearray(
                            response_path.read_bytes()
                        )
                        if len(response) > MAX_PENDING_PAYLOAD:
                            raise RuntimeError(
                                "Synthetic response exceeds DirtySock queue"
                            )
                        if len(response) < 12:
                            raise RuntimeError(
                                "Synthetic ProtoFire response is truncated"
                            )
                        # Match the full 20-bit transaction number from the
                        # request. This also makes retries safe.
                        response[9:12] = full[9:12]
                        client.write(PENDING_LENGTH, bytes(4))
                        # XBDM rejects long setmem command lines on some
                        # builds. Stage larger ProtoFire frames in small
                        # verified-safe command chunks.
                        for offset in range(0, len(response), 0x80):
                            client.write(
                                PENDING_PAYLOAD + offset,
                                bytes(response[offset : offset + 0x80]),
                            )
                        client.write(
                            PENDING_SOCKET,
                            socket_object.to_bytes(4, "big"),
                        )
                        client.write(
                            PENDING_CURSOR,
                            PENDING_PAYLOAD.to_bytes(4, "big"),
                        )
                        # Publish only after payload and socket are visible.
                        client.write(
                            PENDING_LENGTH,
                            len(response).to_bytes(4, "big"),
                        )
                        print(
                            f"  queued {len(response)} bytes for direct recv "
                            f"on socket 0x{socket_object:08X}",
                            flush=True,
                        )
                        if (
                            args.pump_blaze_after_preauth
                            and route == bytes.fromhex("00090007")
                        ):
                            pump_response = client.call_void(0x83AC83F0)
                            print(
                                "  Blaze receive pump: "
                                f"{pump_response}",
                                flush=True,
                            )
                        if args.bypass_connect_after_inject:
                            connect_call = client.read(CONNECT_CALLSITE, 4)
                            if connect_call not in (
                                ORIGINAL_CONNECT_CALL,
                                PATCHED_CONNECT_CALL,
                            ):
                                raise RuntimeError(
                                    "Unexpected DirtySock connect callsite"
                                )
                            if connect_call == ORIGINAL_CONNECT_CALL:
                                client.write(
                                    CONNECT_CALLSITE,
                                    PATCHED_CONNECT_CALL,
                                )
                            print(
                                "  subsequent DirtySock connect calls "
                                "will return success",
                                flush=True,
                            )
                        responses[route] = None
                seen = current
            time.sleep(0.05)
        print(f"Final sequence: {seen}", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nWatcher stopped.")
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
