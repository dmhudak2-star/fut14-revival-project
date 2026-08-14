#!/usr/bin/env python3
r"""Where the revival server is, said once and in one place.

The console has to be told an address, and until now it was told by whoever
typed the command: `tools/fut.sh` worked out this Mac's LAN address and handed
it to two patchers as `--local-ip`, which compile it into the title's memory at
launch. That is fine for exactly one setup -- this one. Anyone else pointing a
console somewhere else needs the launcher, a Python and the right argument,
which is not something a release can ask for.

This file is the answer instead:

    [server]
    host = auto            # or 192.168.1.40, or revival.example.net
    core_port = 10041      # Blaze
    identity_port = 18080  # HTTP

    [console]
    address = 192.168.1.25
    title = Hdd:\Games\FIFA 14

`docs/RELEASE.md` describes the Dashlaunch plugin that is meant to replace the
XBDM patching altogether. That plugin reads this same file from the console's
own disk, so the format is settled here -- where it is exercised on every
launch -- rather than invented later on paper.

The `[console]` section is for the development launcher only. A plugin running
on the console already knows which console it is on.

`host = auto` resolves to this machine's LAN address, and that is not a
convenience. DHCP moved this address once already, and a stale value is
**silent**: the title comes up normally and then fails on the first Blaze
connect, with nothing in the journal, because nothing ever reached the server.
"""

from __future__ import annotations

import argparse
import configparser
import os
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_NAME = "fifa14revival.ini"
EXAMPLE_NAME = "fifa14revival.example.ini"

# What every key falls back to when neither the file nor the caller says.
# These are the values the project has been running on, so a missing file
# changes nothing.
DEFAULTS = {
    "server.host": "auto",
    "server.core_port": "10041",
    "server.identity_port": "18080",
    "console.address": "192.168.1.25",
    "console.title": r"Hdd:\Games\FIFA 14",
}


def config_path() -> Path:
    """The file to read. `FIFA14_REVIVAL_CONFIG` overrides, as tests do.

    A missing file is not an error. The defaults above are the values this
    project has always used, so the launcher behaves identically until someone
    writes the file -- which is what makes adding it safe rather than a
    flag day.
    """
    override = os.environ.get("FIFA14_REVIVAL_CONFIG")
    if override:
        return Path(override)
    return REPO / CONFIG_NAME


def load(path: Path | None = None) -> configparser.RawConfigParser:
    """Parse the file, or return an empty parser if there is none.

    Raw rather than the default parser: a Windows-shaped title path is a
    perfectly ordinary value here, and `%` interpolation would turn one into a
    parse error at some future date for no benefit.
    """
    parser = configparser.RawConfigParser()
    target = path if path is not None else config_path()
    if target.exists():
        parser.read(target, encoding="utf-8")
    return parser


def value(key: str, default: str | None = None,
          path: Path | None = None) -> str:
    """One `section.option`, from the file or from the defaults."""
    section, _, option = key.partition(".")
    parser = load(path)
    if parser.has_option(section, option):
        found = parser.get(section, option).strip()
        if found:
            return found
    if default is not None:
        return default
    return DEFAULTS.get(key, "")


def lan_address() -> str:
    """This machine's address on the LAN the console is on.

    Asked of the routing table rather than of a named interface: the answer
    here lives on en1 and not en0, which a list of interface names gets wrong
    the moment somebody runs this on a machine wired differently. Nothing is
    sent -- a connected UDP socket only makes the kernel choose a source
    address.

    192.0.2.1 is TEST-NET-1: reserved for documentation and never routed, so
    this cannot be mistaken for reaching out to somebody's host.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        pass
    finally:
        probe.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def server_host(path: Path | None = None) -> str:
    """The address the console should be pointed at.

    `auto`, or an empty setting, means "this machine". Anything else is taken
    literally, including a hostname -- which is what a hosted server will be.
    """
    configured = value("server.host", path=path)
    if not configured or configured.lower() == "auto":
        return lan_address()
    return configured


def port(key: str, path: Path | None = None) -> int:
    try:
        return int(value(key, path=path))
    except ValueError:
        return int(DEFAULTS[key])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read one setting, for shell callers.",
    )
    parser.add_argument("key", help="section.option, e.g. server.host")
    parser.add_argument("default", nargs="?", default=None)
    parser.add_argument("--file", default=None)
    args = parser.parse_args(argv)
    path = Path(args.file) if args.file else None
    # `server.host` is the one key with a rule of its own, and the shell
    # asking for it wants the resolved answer, not the literal word `auto`.
    if args.key == "server.host":
        print(server_host(path))
    else:
        print(value(args.key, args.default, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
