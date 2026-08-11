"""Keep the test suite away from the real club.

`tests/test_fifa14_blaze_server.py` imports the server module, and importing it
builds a live club: the saved coins, the saved squad, the saved cups. Every
route under test that writes then wrote *the player's own save file* -- with
whatever a test had just done to the module globals.

That is not hypothetical. A cup run entered at 00:33 one evening was gone from
the save by 00:38, rewritten with an empty tournament table by a test run in
between, and the next server start loaded a club with no cup in progress. The
file was simply smaller; nothing said so.

Redirecting the save has to happen before the server module is imported,
because `ClubSave.__init__` binds `SAVE_FILE` as a default argument at class
definition time -- reassigning the module global afterwards is too late.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_SCRATCH = Path(tempfile.mkdtemp(prefix="fifa14-tests-")) / "club-save.json"
os.environ["FIFA14_CLUB_SAVE"] = str(_SCRATCH)
