"""The one-command installer, checked for the ways it goes stale.

It is the only thing most people will ever run from this project, and it runs
on a phone we cannot see. The failures worth catching here are the silent ones:
a release URL that no longer matches the tarball the packager builds, or a
shell syntax error that only shows up as "still nothing" in somebody's Termux.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"
sys.path.insert(0, str(REPO / "tools"))

import package_client  # noqa: E402


def test_it_is_valid_shell() -> None:
    shell = shutil.which("sh")
    assert shell, "no sh to check with"
    done = subprocess.run([shell, "-n", str(INSTALL)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_names_it_downloads_are_the_names_the_packager_produces() -> None:
    # The URL and the directory are written into the script by hand; the
    # tarball and its top-level directory come from package_client. If those
    # ever drift apart the script downloads something real and then fails to
    # find it, which reads like a corrupt download.
    text = INSTALL.read_text()
    assert f'DIR="{package_client.TOP}"' in text
    assert f"{package_client.TOP}.tgz" in text


def test_it_refuses_a_python_older_than_the_server_needs() -> None:
    # A phone with 3.9 otherwise fails much later, in an import, inside a
    # subprocess -- which looks like a broken package rather than an old
    # interpreter.
    text = INSTALL.read_text()
    assert "(3,10)" in text


def test_curl_fails_loudly_on_an_http_error() -> None:
    # Without -f, a 404 is saved as a short HTML file with the tarball's name,
    # and the next line reports that tar cannot read it. The error then names
    # the wrong thing entirely.
    text = INSTALL.read_text()
    assert "curl -fL" in text


def test_it_checks_the_console_answers_before_declaring_success() -> None:
    # XBDM not being loaded is the single most common way this fails, and it
    # is invisible until a launch is attempted. Port 730 is the check.
    assert "730" in INSTALL.read_text()
