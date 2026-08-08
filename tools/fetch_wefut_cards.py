#!/usr/bin/env python3
"""Pull the FIFA 14 card list from wefut's player-database endpoint.

The club inventory was built from the four icebreaker packs this build ships:
92 cards, which is enough to field a side and nothing like enough for a
transfer market, a pack, or a club worth browsing. The game's own
`cards_ng_db.db` would be the right source and still does not decode -- its
chunks report an LZX block type of 7 at the first symbol, and every variant
tried so far produces structurally valid noise.

wefut's ids turn out to be the game's ids, which is what makes this usable at
all. Messi comes back as `base-id 158023` with `club-id 241`, exactly the asset
id and team id the icebreaker fixture carries for him, and columns 13-18 hold
`92 89 84 96 44 69` -- the same six attributes, in the same order. An asset id
that did not match would draw a blank card, so this correspondence is the whole
reason to trust the data.

The page drives a DataTables grid at `/ajax/getPlayers/14`, paged with
`iDisplayStart` and `iDisplayLength`. One request per hundred cards, spaced, so
a full pass is a few minutes rather than a hammering.

    python3 tools/fetch_wefut_cards.py --out server/fifa14_cards.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


ENDPOINT = "https://wefut.com/ajax/getPlayers/14"
REFERER = "https://wefut.com/player-database/14"
AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Verified against the icebreaker fixture: Messi reads 92 89 84 96 44 69 in
# both, in this order.
ATTRIBUTE_COLUMNS = [str(index) for index in range(13, 19)]

FIELDS = {
    "first_name": "1",
    "last_name": "2",
    "rating": "4",
    "position": "8",
    "foot": "9",
    "club": "10",
    "league": "11",
    "nation": "12",
    "rarity": "72",
}

CARD_IDS = re.compile(r'data-([a-z-]+)="([^"]*)"')


def fetch_page(start: int, length: int, timeout: float) -> dict:
    query = f"?sEcho=1&iDisplayStart={start}&iDisplayLength={length}"
    request = urllib.request.Request(
        ENDPOINT + query,
        headers={
            "User-Agent": AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": REFERER,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def parse_row(row: dict) -> dict | None:
    ids = dict(CARD_IDS.findall(row.get("player_card") or ""))
    asset_id = ids.get("base-id")
    if not asset_id or not asset_id.isdigit():
        # No asset id means no card art, which makes the record useless here
        # however complete the rest of it looks.
        return None

    def number(value: str | None) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    first = (row.get(FIELDS["first_name"]) or "").strip()
    last = (row.get(FIELDS["last_name"]) or "").strip()
    return {
        "assetId": int(asset_id),
        "name": " ".join(part for part in (first, last) if part),
        "rating": number(row.get(FIELDS["rating"])),
        "position": (row.get(FIELDS["position"]) or "").strip(),
        "foot": (row.get(FIELDS["foot"]) or "").strip(),
        "club": (row.get(FIELDS["club"]) or "").strip(),
        "league": (row.get(FIELDS["league"]) or "").strip(),
        "nation": (row.get(FIELDS["nation"]) or "").strip(),
        "clubId": number(ids.get("club-id")),
        "leagueId": number(ids.get("league-id")),
        "nationId": number(ids.get("nation-id")),
        "rareflag": number(ids.get("rareflag")),
        "rarity": (row.get(FIELDS["rarity"]) or "").strip(),
        "attributes": [number(row.get(column)) for column in ATTRIBUTE_COLUMNS],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-pages", type=int, default=400)
    args = parser.parse_args()

    cards: dict[int, dict] = {}
    start = 0
    for page in range(args.max_pages):
        try:
            payload = fetch_page(start, args.page_size, args.timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            # One bad page should not throw away everything collected so far.
            print(f"page at {start} failed: {error}", flush=True)
            time.sleep(args.pause * 4)
            continue

        rows = payload.get("aaData") or []
        if not rows:
            break

        for row in rows:
            card = parse_row(row)
            if card is None:
                continue
            # A player appears once per card version -- base, rare, TOTY. Keep
            # them apart by rarity so the catalogue holds every card rather
            # than every footballer.
            cards[(card["assetId"], card["rareflag"])] = card

        print(f"{start + len(rows):>6} rows, {len(cards):>6} cards", flush=True)
        if len(rows) < args.page_size:
            break
        start += args.page_size
        time.sleep(args.pause)

    ordered = sorted(cards.values(), key=lambda card: (-card["rating"], card["name"]))
    args.out.write_text(json.dumps({"cards": ordered}, separators=(",", ":")))
    print(f"wrote {len(ordered)} cards to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
