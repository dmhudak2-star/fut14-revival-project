"""Le guetteur doit survivre à une session.

Il vivait dans un dossier temporaire et a disparu avec lui, comme une clé SSH
avant lui. Ces tests existent surtout pour qu'il ait une raison d'être dans le
dépôt -- mais ils pinnent aussi les deux décisions qui font qu'il sert à
quelque chose : ne montrer par défaut que ce qui n'a pas de gestionnaire, et
suivre le journal quand un relancement le fait tourner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import watch_gaps  # noqa: E402


def shown(records: list[dict], watched: set[int], everything: bool, capsys) -> str:
    for record in records:
        watch_gaps.render(record, watched, everything)
    return capsys.readouterr().out


def test_a_route_with_no_handler_is_named_not_numbered(capsys) -> None:
    """"composant 4" says nothing; "GameManager" says what to go and read."""
    out = shown([{
        "event": "unknown_route", "component": 4, "command": 13,
        "time": "2026-08-21T19:16:29+0000",
    }], {4}, False, capsys)
    assert "SANS GESTIONNAIRE" in out
    assert "GameManager (4)" in out
    assert "commande 13" in out


def test_the_payload_is_shown_decoded(capsys) -> None:
    """The whole point: the journal decodes every request, so the first frame
    of anything new arrives readable rather than as hex."""
    out = shown([{
        "event": "frame", "direction": "request",
        "time": "2026-08-21T19:16:29+0000",
        "frame": {"component": 4, "command": 13, "fields": [
            {"label": "NTOP", "type": 0, "value": 130},
            {"label": "GVER", "type": 1, "value": "qa-only-day45"},
        ]},
    }], {4}, False, capsys)
    assert '"NTOP"' in out and "130" in out
    assert "qa-only-day45" in out


def test_a_component_that_already_works_is_not_shown(capsys) -> None:
    """A line for every served request would bury the one that matters -- the
    title sends tens of thousands of them."""
    noisy = {
        "event": "frame", "direction": "request",
        "time": "2026-08-21T19:16:29+0000",
        "frame": {"component": 9, "command": 2, "fields": []},
    }
    assert shown([noisy], {4}, False, capsys) == ""
    assert "Util (9)" in shown([noisy], set(), True, capsys)


def test_responses_are_not_shown(capsys) -> None:
    """What the server said is already known; what the console asked is not."""
    out = shown([{
        "event": "frame", "direction": "response",
        "time": "2026-08-21T19:16:29+0000",
        "frame": {"component": 4, "command": 13, "fields": []},
    }], {4}, False, capsys)
    assert out == ""


def test_the_newest_journal_is_the_one_followed(tmp_path, monkeypatch) -> None:
    """Every relaunch opens a new one, and a watcher on the old file is a
    watcher that has quietly stopped watching."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    old = runtime / "live-easw-20260821-100000.jsonl"
    new = runtime / "live-easw-20260821-200000.jsonl"
    old.write_text("{}\n")
    new.write_text("{}\n")
    import os
    os.utime(old, (1, 1))
    monkeypatch.setattr(watch_gaps, "REPO", tmp_path)
    assert watch_gaps.newest_journal("live-easw-*.jsonl") == str(new)


def test_every_component_this_repo_knows_has_a_name() -> None:
    """The ids came out of the client's own constructors; a watcher printing
    a bare number would throw that away."""
    assert watch_gaps.COMPONENTS[4] == "GameManager"
    assert watch_gaps.COMPONENTS[2271] == "OSDKTournaments"
    assert watch_gaps.name(9) == "Util (9)"
    # And one nobody has identified stays honest about it.
    assert watch_gaps.name(2000) == "? (2000)"
