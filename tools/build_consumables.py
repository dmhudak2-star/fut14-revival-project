#!/usr/bin/env python3
"""Build the consumables catalogue from the game's own card database.

Every consumable the club serves used to carry an invented asset id, and the
screens showed it: NOT FOUND art on each card, one name -- "Entraînement
equipe" -- on all of them, and no effect on anyone. The names and effects are
not ours to choose. The title reads them out of `cards_ng_db.db` by
`cardsubtype`, and draws the art by `cardassetid`, so one wrong subtype makes
every card in the club the same card.

    python3 tools/extract_fifa_databases.py --out runtime/db
    python3 tools/build_consumables.py --db runtime/db --out server/fifa14_consumables.json

The families are the game's, too. `CardsDLL` counts consumables under
`consumablesContractPlayer`, `consumablesContractManager`,
`consumablesFitnessPlayer`, `consumablesFitnessTeam`, `consumablesTrainingGk`
and the rest, and each of those names lands on exactly one block of subtypes in
the database -- 201 against 202 for player and manager contracts, 219 against
220 for player and team fitness, 51-57 against 61-67 for outfield and keeper
training. That correspondence is the whole reason these groupings are the
game's rather than a guess at them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "archive"))

import t3db  # noqa: E402


# Subtype -> (itemType, the member CardsDLL counts it under). The item types
# are the six the FUT screens filter on; the members are what the consumables
# tab reads its counts from.
FAMILIES: list[tuple[range, str, str]] = [
    (range(201, 202), "contract", "consumablesContractPlayer"),
    (range(202, 203), "contract", "consumablesContractManager"),
    (range(211, 219), "healing", "consumablesHealing"),
    (range(219, 220), "fitness", "consumablesFitnessPlayer"),
    (range(220, 221), "fitness", "consumablesFitnessTeam"),
    (range(51, 58), "training", "consumablesTrainingPlayer"),
    (range(61, 68), "training", "consumablesTrainingGk"),
    (range(91, 111), "playStyle", "consumablesTrainingPlayerPlayStyle"),
    (range(121, 137), "playStyle", "consumablesTrainingGkPlayStyle"),
    (range(232, 233), "position", "consumablesPosition"),
]

TABLES = (
    "fcc_contractcards",
    "fcc_healingcards",
    "fcc_trainingcards",
    "fcc_misccards",
)


def family_for(subtype: int) -> tuple[str, str] | None:
    for span, item_type, member in FAMILIES:
        if subtype in span:
            return item_type, member
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("runtime/db"))
    parser.add_argument("--out", type=Path, default=Path("server/fifa14_consumables.json"))
    args = parser.parse_args()

    database = t3db.load(
        args.db / "cards_ng_db.db", args.db / "cards_ng_db-meta.xml"
    )

    cards = []
    skipped: dict[int, int] = {}
    for table in TABLES:
        for row in database.read(table):
            subtype = row["cardsubtype"]
            family = family_for(subtype)
            if family is None:
                # Manager modifiers and coin boosts. They are real cards and
                # they are in here, but no FUT screen this server serves knows
                # what to do with one, and putting them in the club would only
                # reproduce the problem this file exists to fix.
                skipped[subtype] = skipped.get(subtype, 0) + 1
                continue
            item_type, member = family
            card = {
                "definitionId": row["carddbid"],
                "assetId": row["cardassetid"],
                "cardsubtypeid": subtype,
                "itemType": item_type,
                "member": member,
                "rating": row["rating"],
                # A contract has no single amount: it grants a different
                # number of matches to a gold, a silver and a bronze player,
                # so the table carries all three and none of them is "the"
                # amount. The gold figure is the one the card is named for.
                "amount": row.get("amount", row.get("gold", 0)),
                "rare": bool(row["weightrare"]),
                "table": table,
            }
            for tier in ("gold", "silver", "bronze"):
                if tier in row:
                    card[tier] = row[tier]
            cards.append(card)

    cards.sort(key=lambda card: card["definitionId"])
    args.out.write_text(json.dumps({"consumables": cards}, indent=1))

    by_type: dict[str, int] = {}
    for card in cards:
        by_type[card["itemType"]] = by_type.get(card["itemType"], 0) + 1
    print(f"{len(cards)} consumables -> {args.out}")
    for item_type, count in sorted(by_type.items()):
        print(f"   {item_type:<10} {count}")
    print(f"   {sum(skipped.values())} manager and boost cards left out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
