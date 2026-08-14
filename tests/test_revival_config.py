"""The one file that says where the revival server is.

It has a second reader that does not exist yet -- the Dashlaunch plugin in
`docs/RELEASE.md` -- so what is pinned here is the format, not just this
launcher's use of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import revival_config  # noqa: E402


def test_a_missing_file_changes_nothing(tmp_path) -> None:
    # Adding configuration must not be a flag day. With no file at all every
    # key answers what the launcher used to hardcode, so an existing checkout
    # behaves exactly as before.
    absent = tmp_path / "nowhere.ini"
    assert revival_config.value("console.address", path=absent) == "192.168.1.25"
    assert revival_config.value("console.title", path=absent) == r"Hdd:\Games\FIFA 14"
    assert revival_config.port("server.core_port", path=absent) == 10041
    assert revival_config.port("server.identity_port", path=absent) == 18080


def test_the_file_wins_over_the_defaults(tmp_path) -> None:
    ini = tmp_path / "fifa14revival.ini"
    ini.write_text(
        "[server]\n"
        "host = revival.example.net\n"
        "identity_port = 9090\n"
        "[console]\n"
        "address = 10.0.0.7\n",
        encoding="utf-8",
    )
    assert revival_config.server_host(ini) == "revival.example.net"
    assert revival_config.port("server.identity_port", path=ini) == 9090
    assert revival_config.value("console.address", path=ini) == "10.0.0.7"
    # Untouched keys still answer.
    assert revival_config.port("server.core_port", path=ini) == 10041


def test_auto_resolves_to_this_machine(tmp_path) -> None:
    # `auto` is the default and has to mean something: DHCP moved this address
    # once already, and a stale one is silent -- the title starts fine and then
    # fails on the first Blaze connect with nothing in the journal.
    ini = tmp_path / "auto.ini"
    ini.write_text("[server]\nhost = auto\n", encoding="utf-8")
    resolved = revival_config.server_host(ini)
    assert resolved == revival_config.lan_address()
    assert resolved.count(".") == 3
    assert resolved != "auto"

    # An empty setting means the same thing rather than an empty address,
    # which would be baked into the title and fail silently.
    blank = tmp_path / "blank.ini"
    blank.write_text("[server]\nhost =\n", encoding="utf-8")
    assert revival_config.server_host(blank) == revival_config.lan_address()


def test_a_hostname_is_taken_literally(tmp_path) -> None:
    # A hosted server is named, not numbered. Nothing here may assume IPv4.
    ini = tmp_path / "hosted.ini"
    ini.write_text("[server]\nhost = revival.example.net\n", encoding="utf-8")
    assert revival_config.server_host(ini) == "revival.example.net"


def test_a_windows_shaped_title_path_survives_parsing(tmp_path) -> None:
    # The title path is `Hdd:\Games\FIFA 14`. A parser doing `%`
    # interpolation would turn a path containing one into an error at some
    # future date for no benefit, which is why this reads raw.
    ini = tmp_path / "title.ini"
    ini.write_text(
        "[console]\ntitle = Hdd:\\Games\\FIFA 14 100%% test\n", encoding="utf-8"
    )
    assert "FIFA 14" in revival_config.value("console.title", path=ini)


def test_the_shipped_example_parses_and_says_what_the_defaults_say(tmp_path) -> None:
    # The example is what anybody copies. If it drifts from the defaults, the
    # first thing a new user does is change behaviour without meaning to.
    example = Path(revival_config.REPO) / revival_config.EXAMPLE_NAME
    assert example.exists()
    assert revival_config.value("console.address", path=example) == "192.168.1.25"
    assert revival_config.port("server.core_port", path=example) == 10041
    assert revival_config.port("server.identity_port", path=example) == 18080
    assert revival_config.value("server.host", path=example) == "auto"


def test_the_cli_answers_one_key(capsys) -> None:
    # tools/fut.sh reads it this way, so the contract is one line on stdout.
    example = Path(revival_config.REPO) / revival_config.EXAMPLE_NAME
    revival_config.main(["console.address", "--file", str(example)])
    assert capsys.readouterr().out.strip() == "192.168.1.25"

    # `server.host` is the one key with a rule of its own: the shell wants the
    # resolved answer, never the literal word `auto`.
    revival_config.main(["server.host", "--file", str(example)])
    assert capsys.readouterr().out.strip() == revival_config.lan_address()
