#!/usr/bin/env python3
"""Build the first local FIFA 14 Blaze Redirector reply."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path

from blaze_tdf import (
    INTEGER,
    STRING,
    STRUCT,
    UNION,
    Field,
    decode_frame,
    encode_fields,
    encode_frame,
)


def build(
    host: str,
    port: int,
    secure: int,
    address_mode: str = "host",
    service_id: int = 0,
    site: str = "",
) -> bytes:
    address_fields: list[Field] = []
    active_member = 0
    if address_mode == "host":
        address_fields.append(Field("HOST", STRING, host))
        address_fields.append(Field("IP", INTEGER, 0))
    elif address_mode in ("ip", "host-ip", "network-ip"):
        if address_mode == "network-ip":
            active_member = 3
        if address_mode == "host-ip":
            address_fields.append(Field("HOST", STRING, host))
        address_fields.append(
            Field(
                "IP",
                INTEGER,
                int(ipaddress.IPv4Address(host)),
            )
        )
    elif address_mode == "xbox":
        active_member = 1
        address_fields.extend(
            [
                Field("PORT", INTEGER, port),
                Field("SID", INTEGER, service_id),
                Field("SITE", STRING, site or host),
            ]
        )
    else:
        raise ValueError(f"Unsupported address mode: {address_mode}")
    if address_mode != "xbox":
        address_fields.append(Field("PORT", INTEGER, port))
    payload = encode_fields(
        [
            Field(
                "ADDR",
                UNION,
                (
                    active_member,
                    Field(
                        "VALU",
                        STRUCT,
                        address_fields,
                    ),
                ),
            ),
            Field("SECU", INTEGER, secure),
            Field("XDNS", INTEGER, 0),
        ]
    )
    return encode_frame(
        component=5,
        command=1,
        error=0,
        message_type=1,
        message_number=1,
        payload=payload,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.0.2.35")
    parser.add_argument("--port", type=int, default=10041)
    parser.add_argument("--secure", type=int, choices=(0, 1), default=0)
    parser.add_argument(
        "--address-mode",
        choices=("host", "ip", "host-ip", "network-ip", "xbox"),
        default="host",
        help=(
            "Use XboxClient HOST+IP=0/IP+PORT/HOST+IP+PORT, generic "
            "NetworkAddress IpAddress (member 3), or XboxServer "
            "PORT+SID+SITE"
        ),
    )
    parser.add_argument(
        "--service-id",
        type=lambda value: int(value, 0),
        default=0,
        help="Xbox service ID for --address-mode xbox",
    )
    parser.add_argument(
        "--site",
        default="",
        help="Xbox site name for --address-mode xbox (defaults to --host)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_redirector_local_reply.bin"),
    )
    args = parser.parse_args()
    frame = build(
        args.host,
        args.port,
        args.secure,
        args.address_mode,
        args.service_id,
        args.site,
    )
    # A round-trip decode also validates the frame length and TDF structure.
    decoded = decode_frame(frame)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(frame)
    print(
        f"Wrote {len(frame)} bytes to {args.output}: "
        f"component={decoded['component']} command={decoded['command']} "
        f"type={decoded['message_type']} msg={decoded['message_number']}"
    )
    print(frame.hex().upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
