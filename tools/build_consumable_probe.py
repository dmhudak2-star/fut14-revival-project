#!/usr/bin/env python3
"""Build a probe catalogue that makes the title name its own card families.

`cards_ng_db` says which consumables exist. It does **not** say which family
each block of `cardsubtype` belongs to -- `fcc_trainingcards` carries only
`cardsubtype`, `cardassetid`, `rating`, `weightrare` and `amount`, and no field
anywhere states "this block is play styles". The blocks were assigned by range,
which is a guess, and on 20 August the title contradicted it: a card served as
`playStyle` was drawn as a position modifier, "AVD >> AD", and refused on a CDM.

The title is the only source that knows, because it renders every card from its
own copy of the database. So this serves it a deliberate mixture -- one card
from each block whose family is unknown, plus two whose family is certain as
controls -- and the screen names them all.

Each card gets a distinct **quantity**, and that is the whole trick: the
quantity badge is printed on every card in the list, so one screenshot maps
card to block with no counting of positions and no assumption about ordering.

    tools/build_consumable_probe.py --out runtime/probe-consumables.json
    FIFA14_CONSUMABLES=runtime/probe-consumables.json <serveur>

Then open Apply Consumable on any player and read the screen.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "archive"))

import t3db  # noqa: E402

# (subtype, table, copies).
#
# One copy each, and that is a correction. The first version gave each probe a
# distinct number of copies, on the idea that the quantity badge printed on
# every card would identify it. It does not: the badge is the *card's own*
# quantity, always 1 here, and the copies simply become that many separate
# cards in the list. Twelve probes became seventy-eight cards to step through,
# and the console froze before the walk finished.
#
# So: one copy each, twelve cards, twelve presses of RIGHT. The card is
# identified by the detail panel, which names it -- "Contrats joueur",
# "Style" -- and that panel is what the measurement reads.
#
# Two subtypes are taken from each unknown block -- its first and one from the
# middle -- because a block being homogeneous is itself an assumption, and this
# costs nothing to check.
PROBES: list[tuple[int, str, int]] = [
    (51, "fcc_trainingcards", 1),    # control: player training, certain
    (201, "fcc_contractcards", 1),   # control: contract, certain
    (91, "fcc_trainingcards", 1),
    (100, "fcc_trainingcards", 1),
    (110, "fcc_trainingcards", 1),
    (121, "fcc_trainingcards", 1),
    (136, "fcc_trainingcards", 1),
    (232, "fcc_misccards", 1),
    (250, "fcc_trainingcards", 1),
    (260, "fcc_trainingcards", 1),
    (300, "fcc_trainingcards", 1),
    (320, "fcc_trainingcards", 1),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("runtime/db"))
    parser.add_argument("--out", type=Path,
                        default=Path("runtime/probe-consumables.json"))
    # One category, so every probe lands in the same list and one screenshot
    # covers them all.
    parser.add_argument("--item-type", default="playStyle")
    args = parser.parse_args(argv)

    database = t3db.load(
        args.db / "cards_ng_db.db", args.db / "cards_ng_db-meta.xml"
    )
    tables = {name: database.read(name) for name in
              {table for _, table, _ in PROBES}}

    cards = []
    for subtype, table, copies in PROBES:
        row = next((r for r in tables[table] if r["cardsubtype"] == subtype), None)
        if row is None:
            print(f"subtype {subtype} absent de {table}", file=sys.stderr)
            continue
        card = {
            "definitionId": row["carddbid"],
            "assetId": row["cardassetid"],
            "cardsubtypeid": subtype,
            "itemType": args.item_type,
            "member": "consumablesTrainingPlayerPlayStyle",
            "rating": row["rating"],
            "amount": row.get("amount", 0),
            "rare": bool(row["weightrare"]),
            "table": table,
            "copies": copies,
        }
        if "assetid" in row:
            card["assetid"] = row["assetid"]
        cards.append(card)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"consumables": cards}, indent=1))
    print(f"{len(cards)} sondes -> {args.out}")
    print(f"{'quantité':>9}  subtype  table")
    for card in cards:
        print(f"{card['copies']:>9}  {card['cardsubtypeid']:>7}  {card['table']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
