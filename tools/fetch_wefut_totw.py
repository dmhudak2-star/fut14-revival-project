#!/usr/bin/env python3
"""Pull the FIFA 14 Team of the Week squads from wefut.

The TOTW screen was being served the highest-rated rare cards in the
catalogue, which is a plausible side and not a Team of the Week. wefut
publishes the real ones at /squad/N -- squad 1 is titled "TOTW 1" -- so the
weeks can be walked by incrementing until the pages run out.

Only the asset ids are taken; everything else about each card already comes
from the catalogue, whose ids are the game's.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120 Safari/537.36"
BASE_ID = re.compile(r'data-base-id="(\d+)"')
TITLE = re.compile(r"<title>(.*?)</title>", re.S)


def fetch(week: int, timeout: float) -> tuple[str, list[int]] | None:
    request = urllib.request.Request(
        f"https://wefut.com/squad/{week}", headers={"User-Agent": AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError):
        return None
    title = TITLE.search(page)
    name = title.group(1).split("|")[0].strip() if title else f"TOTW {week}"
    # dict.fromkeys keeps first-seen order and drops the repeats the page
    # emits for its own preview markup.
    ids = [int(value) for value in dict.fromkeys(BASE_ID.findall(page))]
    return (name, ids) if ids else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--weeks", type=int, default=40)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=40)
    args = parser.parse_args()

    squads = []
    misses = 0
    for week in range(1, args.weeks + 1):
        result = fetch(week, args.timeout)
        if result is None:
            misses += 1
            if misses >= 3:
                break
            continue
        misses = 0
        name, ids = result
        squads.append({"week": week, "name": name, "assetIds": ids})
        print(f"{name}: {len(ids)} cards", flush=True)
        time.sleep(args.pause)

    args.out.write_text(json.dumps({"squads": squads}, separators=(",", ":")))
    print(f"wrote {len(squads)} squads to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
