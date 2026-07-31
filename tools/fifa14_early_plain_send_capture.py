#!/usr/bin/env python3
"""Install the plaintext send logger on modload and capture from title startup."""

from __future__ import annotations

import argparse
import re
import select
import socket
import sys
import time
from pathlib import Path

from fifa14_plain_send_hook import (
    COUNTER,
    ORIGINAL_SEND_CALL,
    ORIGINAL_SENDTO_CALL,
    PATCHED_SEND_CALL,
    PATCHED_SENDTO_CALL,
    RECORD_COUNT,
    RECORD_SIZE,
    RING,
    SEND_CALLSITE,
    SEND_STUB,
    SEND_STUB_BYTES,
    SENDTO_CALLSITE,
    SENDTO_STUB,
    SENDTO_STUB_BYTES,
)
from fifa14_plain_recv_hook import (
    MAX_PENDING_PAYLOAD,
    ORIGINAL_RECV_ENTRY,
    PATCHED_RECV_ENTRY,
    PENDING_CURSOR,
    PENDING_LENGTH,
    PENDING_PAYLOAD,
    PENDING_SOCKET,
    RECV_ENTRY,
    RECV_STUB,
    RECV_STUB_BYTES,
)


class Connection:
    def __init__(self, host: str, timeout: float = 8) -> None:
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith("201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> str:
        raw = self.reader.readline()
        if not raw:
            raise EOFError("XBDM closed the connection")
        return raw.decode("ascii", "replace").rstrip("\r\n")

    def command(self, command: str, expected: int = 200) -> str:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        while True:
            response = self.line()
            match = re.match(r"^(\d{3})[- ]", response)
            if match:
                if int(match.group(1)) != expected:
                    raise RuntimeError(f"{command}: {response}")
                return response

    def multiline(self, command: str) -> list[str]:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        status = self.line()
        if not status.startswith("202-"):
            raise RuntimeError(f"{command}: {status}")
        lines: list[str] = []
        while True:
            line = self.line()
            if line == ".":
                return lines
            lines.append(line)

    def read(self, address: int, length: int) -> bytes:
        encoded = "".join(
            self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        )
        if not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
            raise RuntimeError(f"Invalid memory at 0x{address:08X}")
        data = bytes.fromhex(encoded)
        if len(data) != length:
            raise RuntimeError(f"Short read at 0x{address:08X}")
        return data

    def write(self, address: int, data: bytes) -> None:
        self.command(
            f"setmem addr=0x{address:08X} data={data.hex().upper()}"
        )

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def install(control: Connection, install_recv: bool) -> None:
    if control.read(SEND_CALLSITE, 4) != ORIGINAL_SEND_CALL:
        raise RuntimeError("Unexpected DirtySock send callsite")
    if control.read(SENDTO_CALLSITE, 4) != ORIGINAL_SENDTO_CALL:
        raise RuntimeError("Unexpected DirtySock sendto callsite")
    for address, stub in (
        (SEND_STUB, SEND_STUB_BYTES),
        (SENDTO_STUB, SENDTO_STUB_BYTES),
    ):
        cave = control.read(address, len(stub))
        if cave not in (bytes(len(stub)), stub):
            raise RuntimeError(f"Code cave 0x{address:08X} is not free")

    control.write(COUNTER, bytes(4))
    control.write(SEND_STUB, SEND_STUB_BYTES)
    control.write(SENDTO_STUB, SENDTO_STUB_BYTES)
    if control.read(SEND_STUB, len(SEND_STUB_BYTES)) != SEND_STUB_BYTES:
        raise RuntimeError("DirtySock send stub verification failed")
    if control.read(SENDTO_STUB, len(SENDTO_STUB_BYTES)) != SENDTO_STUB_BYTES:
        raise RuntimeError("DirtySock sendto stub verification failed")

    control.write(SEND_CALLSITE, PATCHED_SEND_CALL)
    control.write(SENDTO_CALLSITE, PATCHED_SENDTO_CALL)
    if control.read(SEND_CALLSITE, 4) != PATCHED_SEND_CALL:
        raise RuntimeError("DirtySock send hook verification failed")
    if control.read(SENDTO_CALLSITE, 4) != PATCHED_SENDTO_CALL:
        raise RuntimeError("DirtySock sendto hook verification failed")
    if install_recv:
        if control.read(RECV_ENTRY, 4) != ORIGINAL_RECV_ENTRY:
            raise RuntimeError("Unexpected DirtySock recv entry")
        cave = control.read(RECV_STUB, len(RECV_STUB_BYTES))
        if cave not in (bytes(len(RECV_STUB_BYTES)), RECV_STUB_BYTES):
            raise RuntimeError(f"Code cave 0x{RECV_STUB:08X} is not free")
        control.write(PENDING_LENGTH, bytes(8))
        control.write(RECV_STUB, RECV_STUB_BYTES)
        if control.read(RECV_STUB, len(RECV_STUB_BYTES)) != RECV_STUB_BYTES:
            raise RuntimeError("DirtySock recv stub verification failed")
        control.write(RECV_ENTRY, PATCHED_RECV_ENTRY)
        if control.read(RECV_ENTRY, 4) != PATCHED_RECV_ENTRY:
            raise RuntimeError("DirtySock recv hook verification failed")


def capture(
    control: Connection,
    seconds: float,
    output: Path,
    inject_response: Path | None,
) -> int:
    output.mkdir(parents=True, exist_ok=True)
    seen = 0
    captured = 0
    deadline = time.monotonic() + seconds
    print("EARLY_PLAIN_CAPTURE_READY", flush=True)
    while time.monotonic() < deadline:
        current = u32(control.read(COUNTER, 4), 0)
        if current != seen:
            first = seen + 1
            if current - seen > RECORD_COUNT:
                first = current - RECORD_COUNT + 1
                print(
                    f"Warning: ring overrun; resuming at {first}",
                    flush=True,
                )
            for sequence in range(first, current + 1):
                slot = sequence & (RECORD_COUNT - 1)
                record = control.read(
                    RING + slot * RECORD_SIZE, RECORD_SIZE
                )
                if u32(record, 0) != sequence:
                    continue
                buffer_address = u32(record, 4)
                length = u32(record, 8)
                socket_object = u32(record, 20)
                copied = record[0x20 : 0x20 + min(length, 0x40)]
                full = copied
                if 0 < length <= 0x10000 and buffer_address:
                    try:
                        full = control.read(buffer_address, length)
                    except Exception as error:
                        print(
                            f"  full buffer read failed: {error}",
                            flush=True,
                        )
                sockaddr = record[0x60:0x70]
                ip = ".".join(str(octet) for octet in sockaddr[4:8])
                port = int.from_bytes(sockaddr[2:4], "big")
                destination = output / (
                    f"send_{sequence:06d}_{length:04d}.bin"
                )
                destination.write_bytes(full)
                captured += 1
                print(
                    f"seq={sequence} len={length} to={ip}:{port} "
                    f"data={copied.hex().upper()}",
                    flush=True,
                )
                if (
                    inject_response is not None
                    and socket_object
                    and len(full) >= 12
                    and full[2:6] == bytes.fromhex("00050001")
                ):
                    response = bytearray(inject_response.read_bytes())
                    if len(response) > MAX_PENDING_PAYLOAD:
                        raise RuntimeError(
                            "Synthetic response exceeds DirtySock queue"
                        )
                    if len(response) < 12:
                        raise RuntimeError(
                            "Synthetic ProtoFire response is truncated"
                        )
                    response[9:12] = full[9:12]
                    control.write(PENDING_LENGTH, bytes(4))
                    control.write(PENDING_PAYLOAD, bytes(response))
                    control.write(
                        PENDING_SOCKET,
                        socket_object.to_bytes(4, "big"),
                    )
                    control.write(
                        PENDING_CURSOR,
                        PENDING_PAYLOAD.to_bytes(4, "big"),
                    )
                    control.write(
                        PENDING_LENGTH,
                        len(response).to_bytes(4, "big"),
                    )
                    print(
                        f"  queued {len(response)} response bytes "
                        f"on socket 0x{socket_object:08X}",
                        flush=True,
                    )
                    inject_response = None
            seen = current
        time.sleep(0.05)
    print(f"Capture finished: {captured} record(s), sequence={seen}.")
    return captured


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--load-timeout", type=float, default=180)
    parser.add_argument("--capture-seconds", type=float, default=300)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_plain_sends_early"),
    )
    parser.add_argument(
        "--inject-response",
        type=Path,
        help="Queue this ProtoFire response after the Redirector request",
    )
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    stopped = False
    try:
        notify.command(
            'debugger connect override name="FIFAEarlyPlain" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)
        print(
            "EARLY_HOOK_ARMED - launch FIFA 14 from the dashboard now.",
            flush=True,
        )
        deadline = time.monotonic() + args.load_timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            lowered = event.lower()
            if "modload" not in lowered or 'name="default.xex"' not in lowered:
                continue
            print(f"Module event: {event}", flush=True)
            control.command("stop")
            stopped = True
            install(control, args.inject_response is not None)
            print(
                "Verified: network hooks installed before title execution."
            )
            control.command("go")
            stopped = False
            print(
                "Execution resumed. Wait for the FIFA menu, then open FUT.",
                flush=True,
            )
            capture(
                control,
                args.capture_seconds,
                args.output,
                args.inject_response,
            )
            return 0
        raise TimeoutError("default.xex modload event was not observed")
    finally:
        if control is not None:
            if stopped:
                try:
                    control.command("go")
                    print("Execution resumed during cleanup.")
                except Exception:
                    pass
            control.close()
        notify.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; cleanup attempted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)
