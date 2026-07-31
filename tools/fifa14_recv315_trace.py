#!/usr/bin/env python3
"""Capture FIFA 14's 315-byte EA recvfrom payload and caller through XBDM."""

from __future__ import annotations

import argparse
import re
import select
import socket
import struct
import sys
import time
from pathlib import Path


BREAKPOINT = 0x81746C00
TARGET_LENGTH = 315


class Connection:
    def __init__(self, host: str, timeout: float = 5.0):
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.sock.settimeout(timeout)
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
        response = self.line()
        if not response.startswith("202-"):
            raise RuntimeError(f"{command}: {response}")
        lines = []
        while True:
            line = self.line()
            if line == ".":
                return lines
            lines.append(line)

    def read(self, address: int, length: int) -> bytes:
        lines = self.multiline(
            f"getmem addr=0x{address:08X} length=0x{length:X}"
        )
        data = bytes.fromhex("".join(lines))
        if len(data) != length:
            raise RuntimeError(
                f"Short memory read at 0x{address:08X}: {len(data)}/{length}"
            )
        return data

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def fields(line: str) -> dict[str, str]:
    return {
        key.lower(): value.strip('"')
        for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line)
    }


def context_map(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip().lower()] = value.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "Desktop" / "fifa14_recv315.bin",
    )
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    armed = False
    try:
        notify.command(
            'debugger connect override name="FUTRecvTrace" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        notify.sock.settimeout(None)

        control = Connection(args.host)
        control.command(f"break addr=0x{BREAKPOINT:08X}")
        armed = True
        print(f"recvfrom return breakpoint armed at 0x{BREAKPOINT:08X}.")
        print("Open Ultimate Team now.", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([notify.sock], [], [], min(1.0, remaining))
            if not readable:
                continue
            event = notify.line()
            if not event.lower().startswith("break "):
                continue
            event_fields = fields(event)
            if int(event_fields.get("addr", "0"), 0) != BREAKPOINT:
                continue

            thread = int(event_fields["thread"], 0)
            ctx = context_map(
                control.multiline(
                    f"getcontext thread=0x{thread:08X} control int fp"
                )
            )
            stack = int(ctx["r1"], 0)
            frame = control.read(stack + 0x70, 0x60)
            received = struct.unpack_from(">i", frame, 0)[0]
            buffer_address = struct.unpack_from(">I", frame, 0x0C)[0]
            from_address = struct.unpack_from(">I", frame, 0x5C)[0]
            caller = ctx.get("lr", "unknown")

            if received > 0:
                print(
                    f"recvfrom len={received} buffer=0x{buffer_address:08X} "
                    f"caller={caller}",
                    flush=True,
                )

            if received == TARGET_LENGTH:
                payload = control.read(buffer_address, received)
                sockaddr = (
                    control.read(from_address, 16)
                    if from_address != 0
                    else b""
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(payload)
                print(f"Captured payload: {args.output}")
                print(f"SHA-256 input bytes: {payload[:16].hex().upper()}...")
                if sockaddr:
                    print(f"sockaddr: {sockaddr.hex().upper()}")
                print(f"caller LR: {caller}")
                return 0

            control.command("go")

        raise TimeoutError("No 315-byte recvfrom was observed")
    finally:
        if control is not None:
            if armed:
                try:
                    control.command(f"break addr=0x{BREAKPOINT:08X} clear")
                    print("Breakpoint cleared.")
                except Exception as error:
                    print(f"Warning: cleanup failed: {error}", file=sys.stderr)
            try:
                control.command("go")
                print("Execution resumed.")
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
