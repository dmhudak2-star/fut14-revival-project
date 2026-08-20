"""`Xbdm.write` must split long setmem command lines by itself.

XBDM parses a command into a fixed buffer and `setmem` spends two hex
characters per byte, so a long enough write is rejected outright -- `446-`,
with nothing in the message about length. On 20 August the connect stub grew
by sixteen bytes, crossed that limit, and the whole launch patch failed in a
way that read like a bad address or an unsupported build.

The rule is: at most CHUNK bytes per setmem, addresses consecutive, and a short
write is still exactly one command so nothing about the existing behaviour
changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from fifa14_plain_send_hook import Xbdm, write_chunks  # noqa: E402


class Recorder(Xbdm):
    """An Xbdm that records commands instead of opening a socket."""

    def __init__(self) -> None:  # noqa: D107 -- deliberately skips the socket
        self.sent: list[str] = []

    def command(self, text: str) -> str:
        self.sent.append(text)
        return "200- OK"


def addresses_and_payloads(sent: list[str]) -> list[tuple[int, int]]:
    out = []
    for line in sent:
        head, _, data = line.partition(" data=")
        out.append((int(head.split("addr=")[1], 16), len(data) // 2))
    return out


def test_a_short_write_is_still_one_command() -> None:
    client = Recorder()
    client.write(0x83C8E600, bytes(range(4)))
    assert len(client.sent) == 1
    assert client.sent[0] == "setmem addr=0x83C8E600 data=00010203"


def test_a_write_at_the_limit_is_one_command() -> None:
    client = Recorder()
    client.write(0x83C8E600, bytes(Xbdm.CHUNK))
    assert len(client.sent) == 1


def test_a_longer_write_splits_into_consecutive_pieces() -> None:
    client = Recorder()
    # 252 bytes: the size the connect stub reached when the two EAS FC ports
    # were added, and the size that made XBDM answer 446.
    client.write(0x83C8E600, bytes(252))
    pieces = addresses_and_payloads(client.sent)
    assert [n for _, n in pieces] == [128, 124]
    assert [a for a, _ in pieces] == [0x83C8E600, 0x83C8E680]
    assert sum(n for _, n in pieces) == 252


def test_no_command_line_can_reach_the_parser_limit() -> None:
    # The margin, stated as a number rather than as trust: 128 bytes of payload
    # is 256 hex characters plus a 30-character preamble.
    client = Recorder()
    client.write(0x83C8E600, bytes(4096))
    assert max(len(line) for line in client.sent) < 300


def test_write_chunks_still_works_for_its_callers() -> None:
    client = Recorder()
    write_chunks(client, 0x83C8E600, bytes(200))
    assert [n for _, n in addresses_and_payloads(client.sent)] == [128, 72]
