"""The client package must run somewhere that is not this repository.

`package_server.py` has a test that pins a hand-written file list. This one
cannot: the client's file list is *computed* by walking imports, so pinning the
list would only assert that the walk returned what the walk returned.

What is worth asserting is the thing a player actually experiences -- extract
the tarball somewhere else, run the client, and see whether it comes up. Every
missing module, every accidental import of something outside `tools/`, and
every dependency that is not in the standard library fails right here instead
of on a stranger's console.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import package_client  # noqa: E402


def extracted(temp: str) -> Path:
    archive = Path(temp) / "client.tgz"
    package_client.build(archive)
    with tarfile.open(archive) as opened:
        opened.extractall(temp)
    return Path(temp) / package_client.TOP


def test_the_client_runs_from_the_package_alone() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = extracted(temp)
        finished = subprocess.run(
            [sys.executable, "tools/revival_client.py", "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            # A bare environment: nothing of this repository on the path, so an
            # import that only resolves here cannot resolve there either.
            env={"PATH": "/usr/bin:/bin", "HOME": temp},
        )
        assert finished.returncode == 0, finished.stdout + finished.stderr
        assert "--console" in finished.stdout


def test_every_patcher_the_client_runs_imports_from_the_package_alone() -> None:
    # --help only reaches revival_client's own imports. The three patchers are
    # subprocesses, so nothing checks them until a launch does -- which is on a
    # console, with a title running, and far too late.
    with tempfile.TemporaryDirectory() as temp:
        root = extracted(temp)
        for script in (
            "fifa14_early_local_server.py",
            "fifa14_easfc_endpoint_patch.py",
            "fifa14_tu3_helperfunctions_runtime_patch.py",
            "xbox360_virtual_input.py",
        ):
            finished = subprocess.run(
                [sys.executable, "-c",
                 f"import sys; sys.path.insert(0, 'tools'); "
                 f"__import__('{Path(script).stem}')"],
                cwd=root,
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin", "HOME": temp},
            )
            assert finished.returncode == 0, script + ":\n" + finished.stderr


def test_the_package_carries_nothing_that_is_not_ours() -> None:
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "client.tgz"
        package_client.build(archive)
        with tarfile.open(archive) as opened:
            names = opened.getnames()
    # No game files, no console files, no club data, and nothing belonging to
    # Dashlaunch or the XDK. The player brings those; NOTICE.md is why.
    forbidden = (".xex", ".big", ".bh", ".dll", "runtime/", "capture/", ".ini.bak")
    for name in names:
        assert not any(bad in name for bad in forbidden), name
    assert f"{package_client.TOP}/README.md" in names
    assert any(name.endswith("NOTICE.md") for name in names)


def test_the_walk_reaches_the_modules_the_launcher_only_imports_for_a_flag() -> None:
    # These are armed only under --trace-* flags nobody passes, but the
    # launcher imports them at module scope. A hand-written list is exactly
    # where they go missing, and the symptom is a launch that dies on import
    # with the console already booting.
    reached = package_client.closure(package_client.ROOTS)
    for module in (
        "fifa14_postauth_dispatch_trace",
        "fifa14_login_callback_trace",
        "fifa14_useradded_trace",
        "fifa14_dlc_loader_trace",
    ):
        assert module in reached, module
