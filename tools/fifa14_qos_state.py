#!/usr/bin/env python3
"""Inspect the live FIFA 14 QoS manager captured by the send stack trace."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import Xbdm, verify_module
from fifa14_qos_send_stack_trace import JOURNAL, QOS_OBJECT_OFFSET


def u8(raw: bytes, offset: int) -> int:
    return raw[offset]


def u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        qos = int.from_bytes(
            client.read(JOURNAL + QOS_OBJECT_OFFSET, 4), "big"
        )
        if not 0x30000000 <= qos < 0xE0000000:
            raise RuntimeError(f"Invalid captured QoS object: 0x{qos:08X}")
        raw = client.read(qos, 0x1C0)
        print(f"qos_object       = 0x{qos:08X}")
        for offset in (0xB0, 0xB4, 0xB8):
            print(f"qos_{offset:04X}         = 0x{u32(raw, offset):08X}")
        for offset in range(0xBC, 0xC4):
            print(f"qos_{offset:04X}         = 0x{u8(raw, offset):02X}")
        for offset in (0x110, 0x114, 0x118, 0x11C, 0x120):
            print(f"qos_{offset:04X}         = 0x{u32(raw, offset):08X}")
        for offset in (0x1A8, 0x1AC, 0x1B0, 0x1B4, 0x1B8):
            print(f"qos_{offset:04X}         = 0x{u32(raw, offset):08X}")
        parent = u32(raw, 0x1B4)
        if 0x30000000 <= parent < 0xE0000000:
            print(f"callback_parent  = 0x{parent:08X}")
            for signal_offset in (0xA8C, 0xB0C):
                signal = client.read(parent + signal_offset, 0x44)
                begin = u32(signal, 0x04)
                end = u32(signal, 0x08)
                print(
                    f"signal_{signal_offset:03X}       = "
                    f"begin=0x{begin:08X} end=0x{end:08X} "
                    f"count={(end - begin) // 4 if end >= begin else -1} "
                    f"depth={u32(signal, 0x40)}"
                )
                if begin and end > begin and end - begin <= 0x100:
                    entries = client.read(begin, end - begin)
                    print(
                        f"signal_{signal_offset:03X}_entries = "
                        f"{entries.hex().upper()}"
                    )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
