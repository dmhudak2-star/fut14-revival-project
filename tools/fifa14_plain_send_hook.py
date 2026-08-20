#!/usr/bin/env python3
"""Install a volatile, breakpoint-free DirtySock sendto logger in FIFA 14."""

from __future__ import annotations

import argparse
import re
import socket
import struct
import time


MODULE_BASE = 0x82000000
MODULE_TIMESTAMP = 0x534C8977

SEND_CALLSITE = 0x82D6A07C
ORIGINAL_SEND_CALL = bytes.fromhex("4B76050D")  # bl 0x824CA588
SEND_WRAPPER = 0x824CA588

SENDTO_CALLSITE = 0x82D6A0B4
ORIGINAL_SENDTO_CALL = bytes.fromhex("4B7604ED")  # bl 0x824CA5A0
SENDTO_WRAPPER = 0x824CA5A0

SEND_STUB = 0x83C8F000
SENDTO_STUB = 0x83C8F100
COUNTER = 0x83C8F200
RING = 0x83C8F300
RECORD_SIZE = 0x80
RECORD_COUNT = 16


def insn(value: int) -> bytes:
    return struct.pack(">I", value & 0xFFFFFFFF)


def addi(rt: int, ra: int, immediate: int) -> int:
    return 0x38000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def addis(rt: int, ra: int, immediate: int) -> int:
    return 0x3C000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def lwz(rt: int, ra: int, displacement: int) -> int:
    return 0x80000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stw(rs: int, ra: int, displacement: int) -> int:
    return 0x90000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def andi_dot(ra: int, rs: int, immediate: int) -> int:
    return 0x70000000 | (rs << 21) | (ra << 16) | (immediate & 0xFFFF)


def rlwinm(ra: int, rs: int, shift: int, begin: int, end: int) -> int:
    return (
        0x54000000
        | (rs << 21)
        | (ra << 16)
        | (shift << 11)
        | (begin << 6)
        | (end << 1)
    )


def add(rt: int, ra: int, rb: int) -> int:
    return 0x7C000214 | (rt << 21) | (ra << 16) | (rb << 11)


def cmpw(ra: int, rb: int) -> int:
    return 0x7C000000 | (ra << 16) | (rb << 11)


def lhz(rt: int, ra: int, displacement: int) -> int:
    return 0xA0000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def lbz(rt: int, ra: int, displacement: int) -> int:
    return 0x88000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stb(rs: int, ra: int, displacement: int) -> int:
    return 0x98000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def cmpwi(ra: int, immediate: int) -> int:
    return 0x2C000000 | (ra << 16) | (immediate & 0xFFFF)


def cmplwi(ra: int, immediate: int) -> int:
    return 0x28000000 | (ra << 16) | (immediate & 0xFFFF)


def or_register(ra: int, rs: int, rb: int) -> int:
    return 0x7C000378 | (rs << 21) | (ra << 16) | (rb << 11)


def conditional_branch(
    source: int, target: int, branch_option: int, condition_bit: int
) -> int:
    displacement = target - source
    if displacement % 4 or not -(1 << 15) <= displacement < (1 << 15):
        raise ValueError("PowerPC conditional branch target is out of range")
    return (
        0x40000000
        | (branch_option << 21)
        | (condition_bit << 16)
        | (displacement & 0xFFFC)
    )


def branch(source: int, target: int, link: bool) -> int:
    displacement = target - source
    if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("PowerPC branch target is out of range")
    return 0x48000000 | (displacement & 0x03FFFFFC) | int(link)


def build_stub(
    stub_address: int,
    wrapper: int,
    copy_destination: bool,
    bypass_main_socket: bool = False,
    *,
    bounded_copy: bool = True,
) -> bytes:
    words = [
        addis(12, 0, 0x83C9),       # lis  r12,0x83C9
        addi(12, 12, -0xE00),       # addi r12,r12,-0xE00 -> COUNTER
        lwz(10, 12, 0),             # lwz  r10,0(r12)
        addi(10, 10, 1),            # addi r10,r10,1
        andi_dot(9, 10, 0xF),       # andi. r9,r10,15
        rlwinm(9, 9, 7, 0, 24),     # slwi r9,r9,7
        addis(11, 0, 0x83C9),       # lis  r11,0x83C9
        addi(11, 11, -0xD00),       # addi r11,r11,-0xD00 -> RING
        add(11, 11, 9),             # add  r11,r11,r9
        stw(10, 11, 0x00),          # sequence
        stw(4, 11, 0x04),           # source buffer pointer
        stw(5, 11, 0x08),           # source length
        stw(7, 11, 0x0C),           # destination pointer
        stw(8, 11, 0x10),           # destination length
        stw(31, 11, 0x14),          # owning DirtySock socket object
    ]

    # Copy the first min(source length, 64) bytes before XNet transforms
    # them.  The former fixed 16-lwz sequence read beyond short buffers and
    # could fault when a buffer ended near an unmapped page.
    if bounded_copy:
        words.extend((cmpwi(5, 0), 0))
        empty_copy_branch = len(words) - 1
        words.extend(
            (
                or_register(9, 5, 5),    # mr r9,r5
                cmplwi(9, 0x40),
                0,                       # ble copy_count_ready
                addi(9, 0, 0x40),
            )
        )
        capped_copy_branch = len(words) - 2
        copy_count_ready = len(words)
        words.extend(
            (
                0x7D2903A6,              # mtctr r9
                or_register(10, 4, 4),   # mr r10,r4 (source)
                addi(9, 11, 0x20),       # snapshot destination
            )
        )
        copy_loop = len(words)
        words.extend(
            (
                lbz(0, 10, 0),
                stb(0, 9, 0),
                addi(10, 10, 1),
                addi(9, 9, 1),
                0,                       # bdnz copy_loop
            )
        )
        copy_done = len(words)
        words.append(lwz(10, 11, 0x00))  # reload sequence for publication
        words[empty_copy_branch] = conditional_branch(
            stub_address + empty_copy_branch * 4,
            stub_address + copy_done * 4,
            4,
            1,
        )                                # ble
        words[capped_copy_branch] = conditional_branch(
            stub_address + capped_copy_branch * 4,
            stub_address + copy_count_ready * 4,
            4,
            1,
        )                                # ble
        copy_loop_branch = copy_loop + 4
        words[copy_loop_branch] = conditional_branch(
            stub_address + copy_loop_branch * 4,
            stub_address + copy_loop * 4,
            16,
            0,
        )                                # bdnz
    else:
        # Historical image retained only for safe live-stub migration.
        for offset in range(0, 0x40, 4):
            words.append(lwz(9, 4, offset))
            words.append(stw(9, 11, 0x20 + offset))

    if copy_destination:
        # Copy the IPv4 sockaddr while it is still on the stack.  A malformed
        # or short destination must not make the diagnostic hook read beyond
        # the caller's buffer; leave a zero snapshot in that case.
        if bounded_copy:
            words.append(cmpwi(7, 0))
            null_destination_branch = len(words)
            words.append(0)               # beq zero_destination
            words.append(cmpwi(8, 0x10))
            short_destination_branch = len(words)
            words.append(0)               # blt zero_destination
            for offset in range(0, 0x10, 4):
                words.append(lwz(9, 7, offset))
                words.append(stw(9, 11, 0x60 + offset))
            valid_destination_branch = len(words)
            words.append(0)               # b destination_done
            zero_destination = len(words)
            words.append(addi(9, 0, 0))
            for offset in range(0, 0x10, 4):
                words.append(stw(9, 11, 0x60 + offset))
            destination_done = len(words)
            words[null_destination_branch] = conditional_branch(
                stub_address + null_destination_branch * 4,
                stub_address + zero_destination * 4,
                12,
                2,
            )                              # beq
            words[short_destination_branch] = conditional_branch(
                stub_address + short_destination_branch * 4,
                stub_address + zero_destination * 4,
                12,
                0,
            )                              # blt
            words[valid_destination_branch] = branch(
                stub_address + valid_destination_branch * 4,
                stub_address + destination_done * 4,
                link=False,
            )
        else:
            # Exact historical layout, retained only for migration matching.
            for offset in range(0, 0x10, 4):
                words.append(lwz(9, 7, offset))
                words.append(stw(9, 11, 0x60 + offset))
    else:
        # A connected socket has no destination pointer on this call.
        words.append(addi(9, 0, 0))
        for offset in range(0, 0x10, 4):
            words.append(stw(9, 11, 0x60 + offset))

    # Publish the sequence only after the complete record is globally visible.
    # The Mac-side reader runs concurrently with Xenon cores; without a
    # barrier it could observe COUNTER before the preceding ring stores.
    if bounded_copy:
        words.append(0x7C0004AC)     # sync
    words.append(stw(10, 12, 0))
    if bypass_main_socket:
        if bounded_copy:
            words.extend(
                (
                    addis(12, 0, 0x83C9),
                    addi(12, 12, -0x1900),  # connect log 0x83C8E700
                    lwz(11, 12, 0x20),      # FUT DirtySock owner
                    cmpw(31, 11),
                )
            )
            owner_branch = len(words)
            words.append(0)                 # beq local_ack
            words.append(cmpwi(5, 0x0C))    # complete Blaze header
            short_branch = len(words)
            words.append(0)                 # blt real_send
            words.append(lhz(9, 4, 0x02))   # Blaze component
            words.append(cmplwi(9, 5))
            component_branch = len(words)
            words.append(0)                 # bne real_send
            words.append(lhz(9, 4, 0x04))   # Blaze command
            words.append(cmplwi(9, 1))
            command_branch = len(words)
            words.append(0)                 # bne real_send
            local_ack = len(words)
            words.extend(
                (
                    0x7CA32B78,            # mr r3,r5 (all bytes sent)
                    0x4E800020,            # blr
                )
            )
            real_send = len(words)
            words[owner_branch] = conditional_branch(
                stub_address + owner_branch * 4,
                stub_address + local_ack * 4,
                12,
                2,
            )                              # beq
            words[short_branch] = conditional_branch(
                stub_address + short_branch * 4,
                stub_address + real_send * 4,
                12,
                0,
            )                              # blt
            words[component_branch] = conditional_branch(
                stub_address + component_branch * 4,
                stub_address + real_send * 4,
                4,
                2,
            )                              # bne
            words[command_branch] = conditional_branch(
                stub_address + command_branch * 4,
                stub_address + real_send * 4,
                4,
                2,
            )                              # bne
        else:
            # Exact former layout, used only to recognize/migrate it.
            words.extend(
                [
                    lhz(9, 4, 0x02),
                    cmplwi(9, 5),
                    0,
                ]
            )
            check_owner = len(words)
            words.extend(
                [
                    addis(12, 0, 0x83C9),
                    addi(12, 12, -0x1900),
                    lwz(11, 12, 0x20),
                    cmpw(31, 11),
                    0,
                ]
            )
            local_ack = len(words)
            words.extend((0x7CA32B78, 0x4E800020))
            real_send = len(words)
            redirector_component_branch = check_owner - 1
            owner_branch = local_ack - 1
            words[redirector_component_branch] = conditional_branch(
                stub_address + redirector_component_branch * 4,
                stub_address + local_ack * 4,
                12,
                2,
            )
            words[owner_branch] = conditional_branch(
                stub_address + owner_branch * 4,
                stub_address + real_send * 4,
                4,
                2,
            )
    tail_address = stub_address + len(words) * 4
    words.append(branch(tail_address, wrapper, link=False))
    return b"".join(insn(word) for word in words)


LOGGER_SEND_STUB_BYTES = build_stub(SEND_STUB, SEND_WRAPPER, False)
SEND_STUB_BYTES = build_stub(
    SEND_STUB, SEND_WRAPPER, False, bypass_main_socket=True
)
SENDTO_STUB_BYTES = build_stub(SENDTO_STUB, SENDTO_WRAPPER, True)
LEGACY_LOGGER_SEND_STUB_BYTES = build_stub(
    SEND_STUB,
    SEND_WRAPPER,
    False,
    bounded_copy=False,
)
LEGACY_SEND_STUB_BYTES = build_stub(
    SEND_STUB,
    SEND_WRAPPER,
    False,
    bypass_main_socket=True,
    bounded_copy=False,
)
LEGACY_SENDTO_STUB_BYTES = build_stub(
    SENDTO_STUB,
    SENDTO_WRAPPER,
    True,
    bounded_copy=False,
)
PATCHED_SEND_CALL = insn(branch(SEND_CALLSITE, SEND_STUB, link=True))
PATCHED_SENDTO_CALL = insn(
    branch(SENDTO_CALLSITE, SENDTO_STUB, link=True)
)


class Xbdm:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 730), timeout=5)
        self.file = self.sock.makefile("rwb", buffering=0)
        banner = self.file.readline().decode("ascii", "replace").strip()
        if not banner.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner}")

    def close(self) -> None:
        self.file.close()
        self.sock.close()

    def command(self, command: str) -> str:
        self.file.write(command.encode("ascii") + b"\r\n")
        return self.file.readline().decode("ascii", "replace").strip()

    def multiline(self, command: str) -> list[str]:
        status = self.command(command)
        if not status.startswith("202"):
            raise RuntimeError(f"{command}: {status}")
        lines: list[str] = []
        while True:
            line = self.file.readline().decode("ascii", "replace").strip()
            if line == ".":
                return lines
            lines.append(line)

    def read(self, address: int, length: int) -> bytes:
        encoded = "".join(
            self.multiline(f"getmem addr=0x{address:08X} length=0x{length:X}")
        )
        if not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
            raise RuntimeError(f"Invalid memory at 0x{address:08X}")
        data = bytes.fromhex(encoded)
        if len(data) != length:
            raise RuntimeError(f"Short memory read at 0x{address:08X}")
        return data

    # XBDM parses a command line into a fixed buffer, and `setmem` spends two
    # hex characters per byte, so a long enough write is rejected outright --
    # `446-`, with no hint that length is the problem.
    #
    # This bit on 20 August: the connect stub grew from 236 bytes to 252 when
    # two ports were added to its filter, the whole launch patch failed, and
    # what it looked like was a bad address or a wrong build. The limit had
    # been sitting eight bytes away the entire time.
    #
    # So the splitting lives here rather than in a helper a caller has to
    # remember. A write of 128 bytes or less is one setmem exactly as before;
    # only longer ones split, and every caller that had a longer one either
    # already called `write_chunks` or was one edit from this failure. The
    # caves are written before anything branches into them, so a cave that is
    # briefly half-written is never a cave that is executing.
    CHUNK = 0x80

    def write(self, address: int, data: bytes) -> None:
        for offset in range(0, max(len(data), 1), self.CHUNK):
            piece = data[offset : offset + self.CHUNK]
            status = self.command(
                f"setmem addr=0x{address + offset:08X} data={piece.hex().upper()}"
            )
            if not status.startswith("200"):
                raise RuntimeError(f"setmem at 0x{address + offset:08X}: {status}")


def verify_module(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="default\.xex"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None:
        raise RuntimeError("FIFA 14 default.xex is not loaded")
    base = re.search(r"\bbase=0x([0-9A-Fa-f]+)", module)
    timestamp = re.search(r"\btimestamp=0x([0-9A-Fa-f]+)", module)
    if (
        base is None
        or timestamp is None
        or int(base.group(1), 16) != MODULE_BASE
        or int(timestamp.group(1), 16) != MODULE_TIMESTAMP
    ):
        raise RuntimeError(f"Unexpected FIFA build: {module}")


def site_state(
    client: Xbdm, address: int, original: bytes, patched: bytes
) -> str:
    call = client.read(address, 4)
    if call == original:
        return "original"
    if call == patched:
        return "hooked"
    return f"unexpected:{call.hex().upper()}"


def write_chunks(client: Xbdm, address: int, data: bytes) -> None:
    """Kept for its callers; `Xbdm.write` splits by itself now."""
    client.write(address, data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        send_state = site_state(
            client, SEND_CALLSITE, ORIGINAL_SEND_CALL, PATCHED_SEND_CALL
        )
        sendto_state = site_state(
            client,
            SENDTO_CALLSITE,
            ORIGINAL_SENDTO_CALL,
            PATCHED_SENDTO_CALL,
        )
        print(f"Send callsite: {send_state}")
        print(f"Sendto callsite: {sendto_state}")
        if args.action == "status":
            print(f"Logger counter: {int.from_bytes(client.read(COUNTER, 4), 'big')}")
            return 0

        if args.action == "apply":
            if send_state not in ("original", "hooked"):
                raise RuntimeError("Refusing to overwrite unknown send callsite")
            if sendto_state not in ("original", "hooked"):
                raise RuntimeError("Refusing to overwrite unknown sendto callsite")

            send_span = SENDTO_STUB - SEND_STUB
            sendto_span = COUNTER - SENDTO_STUB
            send_image = SEND_STUB_BYTES.ljust(send_span, b"\0")
            sendto_image = SENDTO_STUB_BYTES.ljust(sendto_span, b"\0")
            logger_image = LOGGER_SEND_STUB_BYTES.ljust(send_span, b"\0")
            legacy_logger_image = LEGACY_LOGGER_SEND_STUB_BYTES.ljust(
                send_span, b"\0"
            )
            legacy_send_image = LEGACY_SEND_STUB_BYTES.ljust(
                send_span, b"\0"
            )
            legacy_sendto_image = LEGACY_SENDTO_STUB_BYTES.ljust(
                sendto_span, b"\0"
            )
            stubs = (
                (
                    SEND_STUB,
                    send_image,
                    (
                        bytes(send_span),
                        send_image,
                        logger_image,
                        legacy_logger_image,
                        legacy_send_image,
                    ),
                ),
                (
                    SENDTO_STUB,
                    sendto_image,
                    (
                        bytes(sendto_span),
                        sendto_image,
                        legacy_sendto_image,
                    ),
                ),
            )
            cave_images: list[bytes] = []
            for address, _, allowed in stubs:
                cave = client.read(address, len(allowed[0]))
                cave_images.append(cave)
                if cave not in allowed:
                    raise RuntimeError(
                        f"Logger code cave 0x{address:08X} is incompatible"
                    )
            if (
                send_state == sendto_state == "hooked"
                and cave_images == [send_image, sendto_image]
            ):
                print("Already hooked with bounded snapshots.")
                return 0

            # Unpublish both entries before replacing either shared image.
            # A short drain window keeps a caller that entered immediately
            # before unpublication from executing bytes as they are rewritten.
            if send_state == "hooked":
                client.write(SEND_CALLSITE, ORIGINAL_SEND_CALL)
            if sendto_state == "hooked":
                client.write(SENDTO_CALLSITE, ORIGINAL_SENDTO_CALL)
            if "hooked" in (send_state, sendto_state):
                time.sleep(0.02)
            try:
                client.write(COUNTER, bytes(4))
                for address, image, _ in stubs:
                    write_chunks(client, address, image)
                    if client.read(address, len(image)) != image:
                        raise RuntimeError(
                            "Logger stub verification failed at "
                            f"0x{address:08X}"
                        )
                client.write(SEND_CALLSITE, PATCHED_SEND_CALL)
                client.write(SENDTO_CALLSITE, PATCHED_SENDTO_CALL)
                if site_state(
                    client,
                    SEND_CALLSITE,
                    ORIGINAL_SEND_CALL,
                    PATCHED_SEND_CALL,
                ) != "hooked" or site_state(
                    client,
                    SENDTO_CALLSITE,
                    ORIGINAL_SENDTO_CALL,
                    PATCHED_SENDTO_CALL,
                ) != "hooked":
                    raise RuntimeError("Callsite hook verification failed")
            except Exception:
                try:
                    client.write(SEND_CALLSITE, ORIGINAL_SEND_CALL)
                    client.write(SENDTO_CALLSITE, ORIGINAL_SENDTO_CALL)
                except Exception:
                    pass
                raise
            print(
                f"Verified: plaintext send logger active "
                f"(send + sendto, {RECORD_COUNT}-record ring)."
            )
            return 0

        if send_state == sendto_state == "original":
            print("Already restored.")
            return 0
        if send_state not in ("original", "hooked"):
            raise RuntimeError("Refusing to restore unknown send callsite")
        if sendto_state not in ("original", "hooked"):
            raise RuntimeError("Refusing to restore unknown sendto callsite")
        if send_state == "hooked":
            client.write(SEND_CALLSITE, ORIGINAL_SEND_CALL)
        if sendto_state == "hooked":
            client.write(SENDTO_CALLSITE, ORIGINAL_SENDTO_CALL)
        if site_state(
            client,
            SEND_CALLSITE,
            ORIGINAL_SEND_CALL,
            PATCHED_SEND_CALL,
        ) != "original" or site_state(
            client,
            SENDTO_CALLSITE,
            ORIGINAL_SENDTO_CALL,
            PATCHED_SENDTO_CALL,
        ) != "original":
            raise RuntimeError("Callsite restore verification failed")
        print("Verified: original send and sendto calls restored.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
