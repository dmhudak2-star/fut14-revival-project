#!/usr/bin/env python3
"""Trace FIFA's own recvfrom import thunk and dump post-XNet payloads."""

from __future__ import annotations

import argparse
import hashlib
import re
import select
import socket
import sys
import time
from pathlib import Path


ENTRY = 0x83C7DBF4


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
        if not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
            raise RuntimeError(f"Invalid memory data at 0x{address:08X}")
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


def register(registers: dict[str, str], name: str) -> int:
    value = registers[name.lower()]
    if value.startswith("0q"):
        return int(value[2:], 16) & 0xFFFFFFFF
    return int(value, 0) & 0xFFFFFFFF


def signed(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def wait_for_break(
    notify: Connection, address: int, deadline: float
) -> tuple[int, str]:
    while time.monotonic() < deadline:
        readable, _, _ = select.select([notify.sock], [], [], 1)
        if not readable:
            continue
        event = notify.line()
        values = fields(event)
        if (
            event.lower().startswith("break ")
            and int(values.get("addr", "0"), 0) == address
        ):
            return int(values["thread"], 0), event
    raise TimeoutError(f"Breakpoint 0x{address:08X} was not hit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_recvfrom_payloads"),
    )
    parser.add_argument("--minimum", type=int, default=1)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    notify = Connection(args.host)
    control: Connection | None = None
    active_break: int | None = None
    counter = 0
    deadline = time.monotonic() + args.timeout
    try:
        notify.command(
            'debugger connect override name="FIFATitleRecv" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)
        control.command(f"break addr=0x{ENTRY:08X}")
        active_break = ENTRY
        print(f"FIFA recvfrom thunk armed at 0x{ENTRY:08X}.", flush=True)

        while time.monotonic() < deadline:
            thread, _ = wait_for_break(notify, ENTRY, deadline)
            entry_ctx = context(
                control.multiline(
                    f"getcontext thread=0x{thread:08X} control int"
                )
            )
            return_address = register(entry_ctx, "lr")
            buffer_address = register(entry_ctx, "gpr5")
            capacity = register(entry_ctx, "gpr6")
            socket_handle = register(entry_ctx, "gpr4")

            control.command(f"break addr=0x{ENTRY:08X} clear")
            active_break = None
            control.command(f"break addr=0x{return_address:08X}")
            active_break = return_address
            control.command("go")

            return_thread, _ = wait_for_break(
                notify, return_address, deadline
            )
            return_ctx = context(
                control.multiline(
                    f"getcontext thread=0x{return_thread:08X} control int"
                )
            )
            received = signed(register(return_ctx, "gpr3"))

            control.command(f"break addr=0x{return_address:08X} clear")
            active_break = None

            if received >= args.minimum and received <= capacity:
                payload = control.read(buffer_address, received)
                counter += 1
                destination = args.output / (
                    f"recv_{counter:03d}_{received:04d}.bin"
                )
                destination.write_bytes(payload)
                print(
                    f"recvfrom socket=0x{socket_handle:X} "
                    f"caller=0x{return_address:08X} "
                    f"buffer=0x{buffer_address:08X} length={received}",
                    flush=True,
                )
                print(
                    f"  {destination} "
                    f"sha256={hashlib.sha256(payload).hexdigest()}",
                    flush=True,
                )
                print(f"  first={payload[:48].hex()}", flush=True)

            control.command(f"break addr=0x{ENTRY:08X}")
            active_break = ENTRY
            control.command("go")

        print(f"Capture complete: {counter} payload(s).")
        return 0
    finally:
        if control is not None:
            if active_break is not None:
                try:
                    control.command(
                        f"break addr=0x{active_break:08X} clear"
                    )
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
