from __future__ import annotations

import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_early_local_server as early


def test_launch_command_uses_the_retail_default_xex_path() -> None:
    command = early.launch_title_command("Hdd:\\Games\\FIFA 14")
    assert command == (
        'magicboot title="Hdd:\\Games\\FIFA 14\\default.xex" '
        'directory="Hdd:\\Games\\FIFA 14"'
    )


def test_launch_command_normalizes_separators_and_trailing_slash() -> None:
    assert early.launch_title_command("Hdd:/Games/FIFA 14/") == (
        'magicboot title="Hdd:\\Games\\FIFA 14\\default.xex" '
        'directory="Hdd:\\Games\\FIFA 14"'
    )


@pytest.mark.parametrize("directory", ["", "\\", 'Hdd:\\Games\\"quoted"'])
def test_launch_command_rejects_unusable_directories(directory: str) -> None:
    with pytest.raises(ValueError):
        early.launch_title_command(directory)
