#!/usr/bin/env python3
"""Identify which resolved XAM receive export FIFA 14 actually uses."""

from __future__ import annotations

import argparse
import re
import select
import socket
import sys
import time


THUNKS = {
    0x81741C78: "WSAGetOverlappedResult",
    0x81741CB8: "recv",
    0x81741CD8: "WSARecv",
    0x81741D30: "recvfrom",
    0x81741D58: "WSARecvFrom",
}


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    armed: set[int] = set()
    try:
        notify.command(
            'debugger connect override name="FIFAReceiveProbe" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)
        for address in THUNKS:
            control.command(f"break addr=0x{address:08X}")
            armed.add(address)
        print("FIFA receive thunks armed:", flush=True)
        for address, name in THUNKS.items():
            print(f"  {name:11s} 0x{address:08X}", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            values = fields(event)
            if not event.lower().startswith("break "):
                continue
            address = int(values.get("addr", "0"), 0)
            if address not in THUNKS:
                continue

            thread = int(values["thread"], 0)
            registers = context(
                control.multiline(
                    f"getcontext thread=0x{thread:08X} control int"
                )
            )
            print(
                f"Hit: {THUNKS[address]} at 0x{address:08X} "
                f"thread=0x{thread:08X}",
                flush=True,
            )
            for name in (
                "lr", "gpr3", "gpr4", "gpr5", "gpr6",
                "gpr7", "gpr8", "gpr9", "gpr10",
            ):
                print(f"{name.upper()}={registers.get(name, '?')}")
            return 0

        raise TimeoutError("No FIFA receive thunk was called")
    finally:
        if control is not None:
            for address in list(armed):
                try:
                    control.command(f"break addr=0x{address:08X} clear")
                except Exception as error:
                    print(
                        f"Warning: failed to clear 0x{address:08X}: {error}",
                        file=sys.stderr,
                    )
            try:
                control.command("go")
                print("Breakpoints cleared; execution resumed.")
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
