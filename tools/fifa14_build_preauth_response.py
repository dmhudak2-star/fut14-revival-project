#!/usr/bin/env python3
"""Build a FIFA 14 Blaze Util.PreAuth response for the local revival flow."""

from __future__ import annotations

import argparse
from pathlib import Path

from blaze_tdf import (
    INTEGER,
    LIST,
    MAP,
    STRING,
    STRUCT,
    Field,
    decode_frame,
    encode_fields,
    encode_frame,
)


COMPONENT_IDS = [
    1,
    25,
    4,
    27,
    28,
    6,
    7,
    9,
    10,
    11,
    30720,
    30721,
    30722,
    30723,
    20,
    30725,
    30726,
    2000,
]


def ping_site(host: str) -> list[Field]:
    return [
        Field("PSA", STRING, host),
        Field("PSP", INTEGER, 17502),
        Field("SNA", STRING, "ams"),
    ]


def build(host: str, service: str) -> bytes:
    payload = encode_fields(
        [
            Field("ANON", INTEGER, 0),
            Field("ASRC", STRING, "300294"),
            Field("CIDS", LIST, (INTEGER, COMPONENT_IDS)),
            Field("CNGN", STRING, ""),
            Field(
                "CONF",
                STRUCT,
                [
                    Field(
                        "CONF",
                        MAP,
                        (
                            STRING,
                            STRING,
                            [
                                ("connIdleTimeout", "90s"),
                                ("defaultRequestTimeout", "80s"),
                                ("pingPeriod", "20s"),
                                ("voipHeadsetUpdateRate", "1000"),
                                ("xlspConnectionIdleTimeout", "300"),
                            ],
                        ),
                    )
                ],
            ),
            Field("EEFA", INTEGER, 1),
            Field("ESRC", STRING, service),
            Field("INST", STRING, service),
            Field("MINR", INTEGER, 0),
            Field("NASP", STRING, "cem_ea_id"),
            Field("PILD", STRING, service),
            Field("PLAT", STRING, "xbox360"),
            Field("PTAG", STRING, ""),
            Field(
                "QOSS",
                STRUCT,
                [
                    Field("BWPS", STRUCT, ping_site(host)),
                    Field("LNP", INTEGER, 10),
                    Field(
                        "LTPS",
                        MAP,
                        (
                            STRING,
                            STRUCT,
                            [("ams", ping_site(host))],
                        ),
                    ),
                    Field("SVID", INTEGER, 1161889797),
                ],
            ),
            Field("RSRC", STRING, "300294"),
            Field("SVER", STRING, "Blaze 3.15.08.0 (CL# 1060080)"),
        ]
    )
    return encode_frame(
        component=9,
        command=7,
        error=0,
        message_type=1,
        message_number=1,
        payload=payload,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.0.2.35")
    parser.add_argument("--service", default="fifa-2014-xbl2")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/fifa14_preauth_local_reply.bin"),
    )
    args = parser.parse_args()
    frame = build(args.host, args.service)
    decoded = decode_frame(frame)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(frame)
    print(
        f"Wrote {len(frame)} bytes to {args.output}: "
        f"component={decoded['component']} command={decoded['command']} "
        f"type={decoded['message_type']}"
    )
    print(frame.hex().upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
