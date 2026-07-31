#!/usr/bin/env python3
"""Apply or restore the reversible FIFA 14 EnterFUT2 gate patch through XBDM."""

from __future__ import annotations

import argparse
import socket
import sys
import time


ADDRESS = 0x82835220
ORIGINAL = bytes.fromhex("2B160000")  # cmplwi cr6,r22,0
LEGACY_PATCHED = bytes.fromhex("48000040")  # skipped the native start call
PATCHED = bytes.fromhex("48000020")   # b 0x82835240 -> native EnterFUT2


class Xbdm:
    def __init__(self, host: str, timeout: float = 5.0):
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.sock.settimeout(timeout)
        self.reader = self.sock.makefile("rb")
        banner = self._line()
        if not banner.startswith(b"201-"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner!r}")

    def _line(self) -> bytes:
        line = self.reader.readline()
        if not line:
            raise EOFError("XBDM closed the connection")
        return line.rstrip(b"\r\n")

    def read(self, address: int, length: int) -> bytes:
        self.sock.sendall(
            f"getmem addr=0x{address:08X} length=0x{length:X}\r\n".encode("ascii")
        )
        status = self._line()
        if not status.startswith(b"202-"):
            raise RuntimeError(f"getmem failed: {status.decode('ascii', 'replace')}")
        encoded = bytearray()
        while True:
            line = self._line()
            if line == b".":
                break
            encoded.extend(line.strip())
        data = bytes.fromhex(encoded.decode("ascii"))
        if len(data) != length:
            raise RuntimeError(f"Short read: expected {length}, received {len(data)}")
        return data

    def write(self, address: int, data: bytes) -> None:
        command = f"setmem addr=0x{address:08X} data={data.hex().upper()}\r\n"
        self.sock.sendall(command.encode("ascii"))
        status = self._line()
        if not status.startswith(b"200-"):
            raise RuntimeError(f"setmem failed: {status.decode('ascii', 'replace')}")

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def describe(data: bytes) -> str:
    if data == ORIGINAL:
        return "original"
    if data == PATCHED:
        return "patched"
    if data == LEGACY_PATCHED:
        return "legacy patched"
    return f"unknown ({data.hex().upper()})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox 360 IP address")
    parser.add_argument("action", choices=("status", "apply", "restore", "wait-apply"))
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()

    if args.action == "wait-apply":
        deadline = time.monotonic() + args.wait_seconds
        print("Waiting for the FIFA 14 code to be mapped...", flush=True)
        while time.monotonic() < deadline:
            client = None
            try:
                client = Xbdm(args.host, timeout=2.0)
                before = client.read(ADDRESS, 4)
                if before == PATCHED:
                    print(f"0x{ADDRESS:08X}: already patched")
                    return 0
                if before in (ORIGINAL, LEGACY_PATCHED):
                    client.write(ADDRESS, PATCHED)
                    after = client.read(ADDRESS, 4)
                    if after != PATCHED:
                        raise RuntimeError(
                            f"Verification failed: read {after.hex().upper()}"
                        )
                    print(f"0x{ADDRESS:08X}: original")
                    print("Verified: patched during startup")
                    return 0
            except (ConnectionError, EOFError, OSError, RuntimeError, ValueError):
                pass
            finally:
                if client is not None:
                    try:
                        client.close()
                    except OSError:
                        pass
            time.sleep(0.25)
        raise RuntimeError(
            f"FIFA 14 was not detected within {args.wait_seconds} seconds"
        )

    client = Xbdm(args.host)
    try:
        before = client.read(ADDRESS, 4)
        print(f"0x{ADDRESS:08X}: {describe(before)}")

        if args.action == "status":
            return 0
        if args.action == "apply" and before == PATCHED:
            print("Already patched.")
            return 0
        if args.action == "restore" and before == ORIGINAL:
            print("Already restored.")
            return 0

        expected = (
            (ORIGINAL, LEGACY_PATCHED)
            if args.action == "apply"
            else (PATCHED, LEGACY_PATCHED)
        )
        replacement = PATCHED if args.action == "apply" else ORIGINAL
        if before not in expected:
            raise RuntimeError(
                f"Refusing {args.action}: unexpected source, "
                f"found {before.hex().upper()}"
            )

        client.write(ADDRESS, replacement)
        after = client.read(ADDRESS, 4)
        if after != replacement:
            raise RuntimeError(
                f"Verification failed: expected {replacement.hex().upper()}, "
                f"read {after.hex().upper()}"
            )
        print(f"Verified: {describe(after)}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
