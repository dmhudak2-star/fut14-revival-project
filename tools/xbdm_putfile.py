#!/usr/bin/env python3
"""Upload one file to an Xbox 360 through the XBDM text protocol."""

import argparse
import socket
from pathlib import Path


def recv_line(sock: socket.socket) -> str:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("XBDM closed the connection")
        data += chunk
    return data.decode("ascii", "replace").rstrip("\r\n")


def connect(host: str, port: int = 730) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=10)
    greeting = recv_line(sock)
    if not greeting.startswith("201"):
        sock.close()
        raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
    return sock


def command(host: str, text: str) -> str:
    with connect(host) as sock:
        sock.sendall(text.encode("ascii") + b"\r\n")
        return recv_line(sock)


def ensure_directory(host: str, xbox_path: str) -> None:
    response = command(host, f'mkdir name="{xbox_path}"')
    # 200: created. 410/402 variants are commonly returned if it exists.
    if not response.startswith(("200", "402", "410")):
        raise RuntimeError(f"mkdir {xbox_path}: {response}")


def upload(host: str, source: Path, destination: str) -> None:
    length = source.stat().st_size
    with connect(host) as sock:
        sock.settimeout(300)
        sock.sendall(
            f'sendfile name="{destination}" length=0x{length:x}\r\n'.encode(
                "ascii"
            )
        )
        response = recv_line(sock)
        if not response.startswith("204"):
            raise RuntimeError(f"sendfile rejected: {response}")
        sent = 0
        with source.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                sock.sendall(block)
                sent += len(block)
                print(
                    f"\r{sent:#010x}/{length:#010x} "
                    f"({sent * 100.0 / length:6.2f}%)",
                    end="",
                    flush=True,
                )
        print()
        response = recv_line(sock)
        if not response.startswith("200"):
            raise RuntimeError(f"upload failed: {response}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination")
    parser.add_argument("--mkdir", action="append", default=[])
    args = parser.parse_args()

    if not args.source.is_file():
        raise SystemExit(f"Source file does not exist: {args.source}")
    for directory in args.mkdir:
        ensure_directory(args.host, directory)
        print(f"Directory ready: {directory}")
    upload(args.host, args.source, args.destination)
    print(f"Uploaded {args.source.stat().st_size} bytes to {args.destination}")


if __name__ == "__main__":
    main()
