"""Compile the plugin skeleton, so it cannot rot unnoticed.

`plugin/plugin.c` said of itself that it had never been compiled, and that was
true: the repository has no PowerPC toolchain, so nothing ever read the file
except a person. A host compiler cannot say whether the plugin *works* -- it is
the wrong architecture, and none of the kernel calls exist -- but it says
whether the file is well formed, which is exactly the class of mistake that
would otherwise be found by someone with an XDK, hours later, in a VM.

So: compile it as C, with the host compiler, and require it to be clean.

The one tolerated warning is unused-function. The three `apply_*`, the title
check and the signature scan are called from module-load hooks that need the
SDK to register, so until that exists they are unreferenced by construction.
Silencing them with a reference would hide the real thing this test is for.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "plugin.c"

TOLERATED = ("unused function", "unused-function")


def compiler() -> str | None:
    for name in ("cc", "clang", "gcc"):
        found = shutil.which(name)
        if found:
            return found
    return None


@pytest.mark.skipif(compiler() is None, reason="no host C compiler")
def test_the_plugin_skeleton_compiles_clean() -> None:
    with tempfile.TemporaryDirectory() as temp:
        finished = subprocess.run(
            [
                compiler(), "-c", "-std=c99", "-Wall", "-Wextra",
                "-o", str(Path(temp) / "plugin.o"), str(PLUGIN),
            ],
            capture_output=True,
            text=True,
        )
    output = finished.stdout + finished.stderr
    assert finished.returncode == 0, f"plugin.c does not compile:\n{output}"

    complaints = [
        line for line in output.splitlines()
        if (": warning:" in line or ": error:" in line)
        and not any(tolerated in line for tolerated in TOLERATED)
    ]
    assert not complaints, "\n".join(complaints)


@pytest.mark.skipif(compiler() is None, reason="no host C compiler")
def test_the_generated_header_is_what_it_compiles_against() -> None:
    # patches.h is generated, and a generator change that produced invalid C
    # would show up here rather than in a VM. plugin.c includes it, so the
    # test above already covers it -- this pins that the include is real, so
    # that relationship cannot be quietly broken.
    assert '#include "patches.h"' in PLUGIN.read_text()
    header = PLUGIN.parent / "patches.h"
    assert header.exists()
    text = header.read_text()
    assert "STAGE1_CAVE_COUNT" in text and "TU3_BRANCH_COUNT" in text
    # The two the address flows into, and the URL slot a boot-time resolver
    # needs to find. A header without them builds a plugin that talks to the
    # server and still draws NOT FOUND on every card.
    assert "cave_fut_resource_stub" in text
    assert "PATCH_FUT_RESOURCE_STUB_URL_ADDR" in text
    assert "cave_ticket_dummy" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
