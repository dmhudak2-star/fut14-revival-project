"""The dashboard reads a running server without disturbing it.

Two things are worth pinning here and they pull in opposite directions. One is
that the page shows what actually happened -- who played, what they opened,
what the title asked for and did not get. The other is that finding all that
out changes nothing: the dashboard runs as a second process against the same
`runtime/` directory as a live server, so if it ever wrote there it would be
racing the game for the club files.

So the last test in this file serves every route and then checks that the
directory is byte-for-byte what it was.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

import dashboard  # noqa: E402


def write_journal(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


def stamp(minute: int) -> str:
    return f"2026-08-21T09:{minute:02d}:00+0000"


@pytest.fixture()
def install(tmp_path: Path) -> Path:
    """An install root shaped like a deployed one, with one club in it."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "fifa14_cards.json").write_text(json.dumps({
        "cards": [
            {"assetId": 158023, "name": "Lionel Messi", "rating": 94,
             "position": "CF", "club": "FC Barcelona", "rarity": "Rare Gold"},
        ]
    }))
    clubs = tmp_path / "runtime" / "clubs"
    clubs.mkdir(parents=True)
    (clubs / "700100.json").write_text(json.dumps({
        "coins": 12_345_678,
        "acquired": [
            {"id": 1900000001, "assetId": 158023, "rating": 94,
             "preferredPosition": "CF", "rarity": "Rare Gold", "itemType": "player"},
            {"id": 1900000002, "assetId": 158023, "rating": 90,
             "preferredPosition": "ST", "rarity": "Gold", "itemType": "player"},
        ],
        "squad": [1900000001, 1900000002],
        "club": {"name": "Racing Revival", "abbr": "RCR"},
        "seasons": {"1:10": {"round": 2, "won": 1, "draw": 0, "lost": 0}},
        "tournaments": {},
        "listings": {},
    }))
    write_journal(tmp_path / "runtime" / "blaze-server.jsonl", [
        {"event": "listening", "port": 10041, "transport": "plaintext", "time": stamp(1)},
        {"event": "identity_http_listening", "port": 18080, "time": stamp(1)},
        {"event": "ready", "advertise": "87.106.7.87", "core_port": 10041,
         "components": [1, 4], "time": stamp(1)},
        {"event": "fut_auth_identity_adopted", "peer": "10.0.0.9",
         "persona_id": 700100, "persona_name": "Racim", "time": stamp(2)},
        {"event": "fut_pack_opened", "peer": "10.0.0.9", "coins": 12_345_678,
         "drawn": [
             {"assetId": 158023, "id": 1, "rarity": "Rare Gold", "rating": 94},
             {"assetId": None, "id": 2, "rarity": None, "rating": 80},
         ], "time": stamp(3)},
        {"event": "fut_trophy_item", "peer": "10.0.0.9", "trophy": 1102, "time": stamp(4)},
        {"event": "fut_trophy_item", "peer": "10.0.0.9", "trophy": 1102, "time": stamp(4)},
        {"event": "fut_trophy_item", "peer": "10.0.0.9", "trophy": 1102, "time": stamp(4)},
        {"event": "unknown_route", "component": 4, "command": 60,
         "connection": 3, "time": stamp(5)},
        {"event": "identity_http_unhandled", "method": "GET", "path": "/goform/x",
         "peer": "45.9.1.1", "time": stamp(6)},
        {"event": "identity_http_unhandled", "method": "GET", "path": "/SDK/webLanguage",
         "peer": "10.0.0.9", "time": stamp(7)},
        {"event": "fut_match_created", "peer": "10.0.0.9", "season": [10, 3],
         "tournament": None, "time": stamp(8)},
    ])
    return tmp_path


def test_a_club_on_disk_becomes_a_player_row(install: Path) -> None:
    rows = dashboard.Runtime(install).players()
    assert len(rows) == 1
    row = rows[0]
    assert row["persona_id"] == 700100
    assert row["club"] == "Racing Revival"
    assert row["coins"] == 12_345_678
    # The name comes from the journal, not from the save, which has no name in
    # it -- the club is named, its manager is not.
    assert row["name"] == "Racim"
    assert row["squad_rating"] == 92
    assert row["packs"] == 1


def test_an_event_with_only_an_address_is_credited_to_its_player(install: Path) -> None:
    """The pack has no persona in it, only `peer`. Somebody still opened it."""
    feed = dashboard.Runtime(install).feed(limit=50)
    opened = next(row for row in feed if row["event"] == "fut_pack_opened")
    assert opened["persona_id"] == 700100
    assert opened["player"] == "Racim"


def test_repeated_events_fold_into_one_line_with_a_count(install: Path) -> None:
    """Three identical trophy lines are one thing happening, not three."""
    feed = dashboard.Runtime(install).feed(limit=50)
    trophies = [row for row in feed if row["event"] == "fut_trophy_item"]
    assert len(trophies) == 1
    assert trophies[0]["count"] == 3


def test_scans_and_unhandled_blaze_commands_stay_out_of_the_feed(install: Path) -> None:
    """A scan is not activity; a route the game really asked for is.

    Both arrive as `identity_http_unhandled`, so only the path tells them
    apart -- the scanner's `/goform/...` is dropped and the title's own
    `/SDK/webLanguage` stays, marked as a warning, because somebody should
    see that the game asked for something it did not get. `unknown_route`
    leaves the feed for a different reason: it fires several times a screen
    and buried everything else, and the gaps table is where it belongs.
    """
    runtime = dashboard.Runtime(install)
    feed = runtime.feed(limit=50)
    kinds = {row["event"] for row in feed}
    assert "unknown_route" not in kinds
    assert not [row for row in feed if row["category"] == "scan"]
    missing = next(row for row in feed if row["event"] == "identity_http_unhandled")
    assert missing["level"] == "warn"
    assert missing["detail"] == "GET /SDK/webLanguage"

    gaps = runtime.gaps()
    assert {"component": 4, "name": "GameManager", "command": 60, "count": 1} in gaps["blaze"]
    # The scanner's path is dropped and the game's own 404 is kept.
    assert [entry["path"] for entry in gaps["http"]] == ["/SDK/webLanguage"]


def test_a_scan_is_told_apart_from_a_route_the_game_asked_for() -> None:
    assert dashboard.is_scan("/goform/set_LimitClient_cfg")
    assert dashboard.is_scan("/")
    assert dashboard.is_scan("/.env")
    assert not dashboard.is_scan("/ut/game/fifa14/user/accountinfo")
    assert not dashboard.is_scan("/SDK/webLanguage")


def test_the_ports_shown_are_the_ones_of_the_run_in_progress(install: Path) -> None:
    """`listening` is written before `ready`, and collecting after it found none."""
    ports = {entry["port"] for entry in dashboard.Runtime(install).server_info()["ports"]}
    assert ports == {10041, 18080}


def test_a_season_reads_as_a_division_and_a_round(install: Path) -> None:
    """`season` is a [division, round] pair; printing the list said "[10, 3]"."""
    feed = dashboard.Runtime(install).feed(limit=50)
    match = next(row for row in feed if row["event"] == "fut_match_created")
    assert match["detail"] == "division 10, tour 3"


def test_pack_objects_are_counted_apart_from_players(install: Path) -> None:
    """Kits and consumables come out of packs with no assetId at all."""
    economy = dashboard.Runtime(install).economy()
    assert economy["players_pulled"] == 1
    assert economy["objects_pulled"] == 1
    assert [entry["rarity"] for entry in economy["rarity"]] == ["Rare Gold"]


def test_an_event_the_table_has_never_heard_of_still_reads(install: Path) -> None:
    row = dashboard.describe_event({"event": "fut_brand_new_thing", "time": stamp(9)})
    assert row["title"] == "Fut brand new thing"
    assert row["category"] == "system"


def serve(install: Path, token: str):
    runtime = dashboard.Runtime(install)
    server = dashboard.Dashboard(("127.0.0.1", 0), dashboard.Handler, runtime, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def fetch(url: str, token: str = "") -> tuple[int, dict]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_api_is_shut_without_the_code_and_open_with_it(install: Path) -> None:
    server, base = serve(install, "secret")
    try:
        status, _ = fetch(f"{base}/api/overview")
        assert status == 401
        # The page has to be able to ask whether it needs a code.
        status, hello = fetch(f"{base}/api/hello")
        assert status == 200 and hello["guarded"] is True
        status, payload = fetch(f"{base}/api/overview", "secret")
        assert status == 200 and payload["overview"]["players"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_serving_every_route_leaves_the_runtime_untouched(install: Path) -> None:
    """The guarantee that lets this run beside a live server.

    A second process writing into `runtime/clubs` would race the game for the
    save, and lose silently -- the server's next save would simply overwrite
    it. So nothing here writes, and this is what says so.
    """
    runtime_dir = install / "runtime"
    before = {
        path: (path.read_bytes(), path.stat().st_mtime)
        for path in sorted(runtime_dir.rglob("*")) if path.is_file()
    }
    server, base = serve(install, "")
    try:
        for route in ("/api/hello", "/api/overview", "/api/players",
                      "/api/players/700100", "/api/feed?verbose=1",
                      "/api/economy", "/api/server"):
            status, _ = fetch(f"{base}{route}")
            assert status == 200, route
    finally:
        server.shutdown()
        server.server_close()

    after = {
        path: (path.read_bytes(), path.stat().st_mtime)
        for path in sorted(runtime_dir.rglob("*")) if path.is_file()
    }
    assert after == before


def test_the_access_code_survives_a_restart(tmp_path: Path) -> None:
    """Regenerating it on every start would lock the owner out of their own
    dashboard every time systemd restarted it."""
    first = dashboard.resolve_token(tmp_path, None)
    assert first and dashboard.resolve_token(tmp_path, None) == first
    # An explicit empty string is how the guard is turned off on a home network.
    assert dashboard.resolve_token(tmp_path, "") == ""
