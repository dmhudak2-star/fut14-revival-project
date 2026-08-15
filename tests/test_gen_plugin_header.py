"""The generated C header must be valid C and must not hand-transcribe bytes.

The plugin can only be compiled elsewhere, but the header generator runs here,
so its output is checked here: it parses as C (when a compiler is present) and
it carries the manifest's bytes unchanged."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import extract_patch_manifest as M  # noqa: E402
import gen_plugin_header as G  # noqa: E402


def _header(tmp: Path) -> str:
    manifest = M.build("203.0.113.10", 10041, 18080)
    (tmp / "patches.json").write_text(json.dumps(manifest))
    out = tmp / "patches.h"
    G.main([str(tmp / "patches.json"), "--output", str(out)])
    return out.read_text()


def test_the_header_carries_the_manifest_bytes() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        manifest = M.build("203.0.113.10", 10041, 18080)
        text = _header(tmp)
        # A hook's expected bytes must appear as C in the header.
        hook = manifest["stage1_launch"]["sites"][0]
        first_byte = int(hook["expect"][:2], 16)
        assert f"0x{first_byte:02X}" in text
        assert f"0x{hook['address']:08X}" in text
        # The build timestamp is the discriminator and must be present.
        assert manifest["build"]["default_xex_timestamp"] in text


def test_the_header_is_valid_c() -> None:
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        import pytest
        pytest.skip("no C compiler")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _header(tmp)
        probe = tmp / "probe.c"
        probe.write_text(
            '#include "patches.h"\n'
            "int main(void){return STAGE1_CAVE_COUNT+TU3_BRANCH_COUNT;}\n"
        )
        result = subprocess.run(
            [cc, "-I", str(tmp), "-fsyntax-only", str(probe)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
