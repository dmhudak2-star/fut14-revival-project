#!/usr/bin/env python3
"""Keep redirected FIFA 14 Blaze TCP sockets open without replying."""

from __future__ import annotations

import argparse
import selectors
import signal
import socket
import time
from pathlib import Path
from typing import BinaryIO


DEFAULT_PORTS = (10041, 42126, 42127)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", required=True)
    parser.add_argument(
        "--ports",
        type=int,
        nargs="+",
        default=list(DEFAULT_PORTS),
    )
    parser.add_argument("--seconds", type=float, default=900.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_tcp_sink"),
    )
    args = parser.parse_args()

    ports = tuple(dict.fromkeys(args.ports))
    if not ports or any(not 0 < port <= 0xFFFF for port in ports):
        raise RuntimeError("Invalid TCP sink port list")
    if args.seconds <= 0:
        raise RuntimeError("Sink duration must be positive")
    socket.inet_aton(args.listen)
    args.output.mkdir(parents=True, exist_ok=True)

    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    selector = selectors.DefaultSelector()
    servers: list[socket.socket] = []
    clients: dict[socket.socket, tuple[BinaryIO, str]] = {}
    connection_index = 0
    try:
        for port in ports:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((args.listen, port))
            server.listen(16)
            server.setblocking(False)
            selector.register(server, selectors.EVENT_READ, ("server", port))
            servers.append(server)

        joined_ports = ",".join(str(port) for port in ports)
        print(
            f"TCP_SINK_READY {args.listen}:{joined_ports}",
            flush=True,
        )
        deadline = time.monotonic() + args.seconds
        while not stopping and time.monotonic() < deadline:
            timeout = min(0.5, max(0.0, deadline - time.monotonic()))
            for key, _events in selector.select(timeout):
                current = key.fileobj
                kind, value = key.data
                if kind == "server":
                    server = current
                    assert isinstance(server, socket.socket)
                    while True:
                        try:
                            client, peer = server.accept()
                        except BlockingIOError:
                            break
                        client.setblocking(False)
                        connection_index += 1
                        label = (
                            f"connection_{connection_index:03d}_"
                            f"port_{value}.bin"
                        )
                        output = (args.output / label).open("wb")
                        peer_label = f"{peer[0]}:{peer[1]} -> {value}"
                        clients[client] = (output, peer_label)
                        selector.register(
                            client,
                            selectors.EVENT_READ,
                            ("client", connection_index),
                        )
                        print(
                            f"TCP_SINK_ACCEPT {connection_index} "
                            f"{peer_label}",
                            flush=True,
                        )
                    continue

                client = current
                assert isinstance(client, socket.socket)
                output, peer_label = clients[client]
                try:
                    block = client.recv(0x10000)
                except BlockingIOError:
                    continue
                except ConnectionError as error:
                    print(
                        f"TCP_SINK_CLOSE {value} {peer_label}: {error}",
                        flush=True,
                    )
                    block = b""
                if block:
                    output.write(block)
                    output.flush()
                    print(
                        f"TCP_SINK_DATA {value} {len(block)} bytes",
                        flush=True,
                    )
                    continue
                selector.unregister(client)
                client.close()
                output.close()
                del clients[client]
                print(
                    f"TCP_SINK_CLOSE {value} {peer_label}",
                    flush=True,
                )
        print("TCP_SINK_STOPPED", flush=True)
        return 0
    finally:
        for client, (output, _peer_label) in list(clients.items()):
            try:
                selector.unregister(client)
            except Exception:
                pass
            client.close()
            output.close()
        for server in servers:
            try:
                selector.unregister(server)
            except Exception:
                pass
            server.close()
        selector.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", flush=True)
        raise SystemExit(1)
