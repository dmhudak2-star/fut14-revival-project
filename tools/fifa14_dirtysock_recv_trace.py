#!/usr/bin/env python3
"""Capture the 315-byte FUT datagram at FIFA 14's DirtySock receive loop."""

from __future__ import annotations

import argparse
import hashlib
import re
import select
import socket
import sys
import time
from pathlib import Path


BREAKPOINT = 0x82D69ACC
TARGET_LENGTH = 315


class Connection:
    def __init__(self, host: str, timeout: float = 8):
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
        result = []
        while True:
            line = self.line()
            if line == ".":
                return result
            result.append(line)

    def read(self, address: int, length: int) -> bytes:
        encoded = "".join(
            self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        )
        data = bytes.fromhex(encoded)
        if len(data) != length:
            raise RuntimeError(f"Short read at 0x{address:08X}")
        return data

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def fields(line: str) -> dict[str, str]:
    return {
        key.lower(): value.strip('"')
        for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line)
    }


def context(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip().lower()] = value.strip()
    return result


def register(value: str) -> int:
    if value.startswith("0q"):
        return int(value[2:], 16) & 0xFFFFFFFF
    return int(value, 0) & 0xFFFFFFFF


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=120)
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
            'debugger connect override name="DirtySockRecv" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)

        control = Connection(args.host)
        control.command(f"break addr=0x{BREAKPOINT:08X}")
        armed = True
        print(f"DirtySock receive return armed at 0x{BREAKPOINT:08X}.")
        print("Open Ultimate Team now.", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            values = fields(event)
            if (
                not event.lower().startswith("break ")
                or int(values.get("addr", "0"), 0) != BREAKPOINT
            ):
                continue

            thread = int(values["thread"], 0)
            registers = context(
                control.multiline(
                    f"getcontext thread=0x{thread:08X} control int"
                )
            )
            received = register(registers["gpr3"])
            if received & 0x80000000:
                received -= 0x100000000

            if received > 0:
                print(f"DirtySock recv length={received}", flush=True)

            if received == TARGET_LENGTH:
                socket_object = register(registers["gpr31"])
                buffer_address = socket_object + 0x58
                payload = control.read(buffer_address, received)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(payload)
                print(f"Captured: {args.output}")
                print(f"SHA-256: {hashlib.sha256(payload).hexdigest()}")
                print(f"First 32 bytes: {payload[:32].hex().upper()}")
                return 0

            control.command("go")

        raise TimeoutError("No 315-byte DirtySock receive was observed")
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
