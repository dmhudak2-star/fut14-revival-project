#!/usr/bin/env python3
"""Resolve the loaded XAM network exports through JRPC2 without calling them."""

from __future__ import annotations

import argparse
import re
import socket


EXPORTS = (
    (0x0F, "NetDll_select"),
    (0x10, "NetDll_WSAGetOverlappedResult"),
    (0x11, "NetDll_WSACancelOverlappedIO"),
    (0x12, "NetDll_recv"),
    (0x13, "NetDll_WSARecv"),
    (0x14, "NetDll_recvfrom"),
    (0x15, "NetDll_WSARecvFrom"),
    (0x16, "NetDll_send"),
    (0x17, "NetDll_WSASend"),
    (0x18, "NetDll_sendto"),
    (0x19, "NetDll_WSASendTo"),
    (0x3A, "XNetServerToInAddr"),
    (0x3B, "XNetTsAddrToInAddr"),
    (0x3C, "XNetInAddrToXnAddr"),
    (0x3D, "XNetInAddrToServer"),
    (0x41, "XNetConnect"),
    (0x42, "XNetGetConnectStatus"),
    (0x43, "XNetDnsLookup"),
    (0x47, "XNetQosServiceLookup"),
    (0x210, "XamUserGetSigninState"),
    (0x212, "XamUserCheckPrivilege"),
    (0x221, "XamUserIsOnlineEnabled"),
    (0x227, "XamUserGetSigninInfo"),
)


def resolve(sock_file, ordinal: int) -> tuple[str, int | None]:
    module = "xam.xex"
    command = (
        'consolefeatures ver=2 type=9 params="'
        f"A\\0\\A\\2\\2/{len(module)}\\{module.encode().hex().upper()}"
        f"\\1\\{ordinal}\\\""
    )
    sock_file.write(command.encode("ascii") + b"\r\n")
    response = sock_file.readline().decode("ascii", "replace").strip()
    matches = re.findall(r"(?:0x)?([0-9A-Fa-f]{8})", response)
    return response, int(matches[-1], 16) if response.startswith("200") and matches else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    with socket.create_connection((args.host, 730), timeout=5) as sock:
        sock_file = sock.makefile("rwb", buffering=0)
        greeting = sock_file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        for ordinal, name in EXPORTS:
            response, address = resolve(sock_file, ordinal)
            if address is None:
                print(f"0x{ordinal:02X} {name}: unresolved ({response})")
            else:
                print(f"0x{ordinal:02X} {name}: 0x{address:08X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
