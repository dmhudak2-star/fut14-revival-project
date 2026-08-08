"""Build a FUT club inventory out of cards this build already ships.

A FUT match needs eleven real player items, and the screens before it need a
club to look at: an inventory, a squad, a kit, a badge, a stadium, a ball. None
of that can be invented from nothing -- an asset id with no art behind it draws
a blank card, and the squad screen rejects records it cannot resolve.

The card database itself (`data/db/cards_ng_db.db` inside `cards0.big`) does
not decode yet: its chunks report an LZX block type of 7 at the first symbol,
which no window size fixes. But the icebreaker pack list does decode, and it is
the same build's own data -- four starter squads of twenty-three players, each
with a real asset id, rating, rare flag, club and six attributes. 158023 is
Messi, 167397 is Neymar. Those are the cards this disc can actually draw.

So the catalogue here is not synthetic. It is the icebreaker squads, unpacked
into the item shape the FUT endpoints expect.
"""

from __future__ import annotations

import json
from pathlib import Path


PACK_LIST = Path(__file__).resolve().parent / "icebreakerpacklist.json"

# resourceId carries the asset id in its low 24 bits and a version in the high
# byte; version 1 is the base card. The PC revival asserts the same invariant.
RESOURCE_VERSION = 0x0100_0000

# Item ids have to be unique and stable across a session, and must not collide
# with the presentation items below.
FIRST_PLAYER_ITEM_ID = 1_600_000_001

FORMATION = "f442"

# Positions come from the card catalogue, keyed by asset id.
#
# Two earlier attempts were wrong and both are worth remembering. Assigning a
# position per squad slot put Messi at right back, because the pack squads are
# not in formation order -- slot 10 of the first pack is Neuer. Sending no
# position at all, on the theory that the title would resolve it from the same
# record it resolves the portrait and club badge from, made every player a
# goalkeeper: it does not fall back, it defaults.
#
# So the position has to be supplied, and it has to be right. The catalogue in
# `fifa14_cards.json` carries one per card, and its asset ids are the game's --
# Messi is 158023 with club 241 there, exactly as in the icebreaker fixture.
CARD_CATALOGUE = Path(__file__).resolve().parent / "fifa14_cards.json"

# Used only when a card is missing from the catalogue. Outfield rather than GK,
# because a wrong outfield position costs chemistry while a wrong goalkeeper
# costs the match.
FALLBACK_POSITION = "CM"

# The eleven slots of f442, in the order the squad screen walks them, each with
# the positions that can fill it from best fit to worst. The pack squads are
# not in this order -- the first pack has its goalkeeper at slot 10 -- so the
# starting eleven has to be arranged rather than taken as it comes.
F442_SLOTS = [
    ("GK", ("GK",)),
    ("LB", ("LB", "LWB", "CB")),
    ("CB", ("CB", "RB", "LB")),
    ("CB", ("CB", "CDM", "RB")),
    ("RB", ("RB", "RWB", "CB")),
    ("LM", ("LM", "LW", "LWB", "CM")),
    ("CM", ("CM", "CDM", "CAM")),
    ("CM", ("CM", "CDM", "CAM")),
    ("RM", ("RM", "RW", "RWB", "CM")),
    ("ST", ("ST", "CF", "LW", "RW")),
    ("ST", ("ST", "CF", "LW", "RW")),
]


def _arrange(players: list[dict]) -> list[dict]:
    """Put the best fit in each slot, then bench whoever is left."""
    remaining = list(players)
    eleven: list[dict] = []
    for _, preferred in F442_SLOTS:
        pick = None
        for position in preferred:
            candidates = [p for p in remaining if p["preferredPosition"] == position]
            if candidates:
                pick = max(candidates, key=lambda p: p["rating"])
                break
        if pick is None:
            # Nobody plays there; take the highest rated rather than leave the
            # slot empty, which the squad screen treats as an incomplete side.
            pick = max(remaining, key=lambda p: p["rating"])
        remaining.remove(pick)
        eleven.append(pick)
    return eleven + sorted(remaining, key=lambda p: -p["rating"])


def _positions_by_asset() -> dict[int, str]:
    if not CARD_CATALOGUE.exists():
        return {}
    document = json.loads(CARD_CATALOGUE.read_text())
    positions: dict[int, str] = {}
    for card in document.get("cards", []):
        position = (card.get("position") or "").strip()
        if position:
            positions.setdefault(int(card["assetId"]), position)
    return positions

# Kit, badge, stadium and ball. Without these the club has nothing to present
# and the match cannot dress either side. Asset ids are the retail defaults the
# PC revival also uses.
PRESENTATION_ACTIVES = [
    {"id": 1700000001, "assetId": 14, "resourceId": 6300000, "rating": 0,
     "itemType": "kit", "itemState": "activeHomeKit"},
    {"id": 1700000002, "assetId": 15, "resourceId": 6400001, "rating": 0,
     "itemType": "kit", "itemState": "activeAwayKit"},
    {"id": 1700000003, "assetId": 241, "resourceId": 6000000, "rating": 0,
     "itemType": "badge", "itemState": "activeBadge"},
    {"id": 1700000004, "assetId": 6, "resourceId": 6200004, "rating": 0,
     "itemType": "stadium", "itemState": "activeStadium"},
    {"id": 1700000005, "assetId": 23, "resourceId": 8120091, "rating": 0,
     "itemType": "ball", "itemState": "activeBall"},
]


def _presentation_items() -> list[dict]:
    items = []
    for base in PRESENTATION_ACTIVES:
        item = dict(base)
        item.update(
            {
                "discardValue": 0,
                "lastSalePrice": 0,
                "timestamp": 1,
                "untradeable": True,
            }
        )
        items.append(item)
    return items


def _player_item(
    item_id: int,
    asset_id: int,
    rating: int,
    rare: int,
    play_style: int,
    team_id: int,
    attributes: list[int],
    position: str,
    item_state: str = "free",
) -> dict:
    return {
        "id": item_id,
        "assetId": asset_id,
        "resourceId": RESOURCE_VERSION | asset_id,
        "rating": rating,
        "preferredPosition": position,
        "teamid": team_id,
        "leagueId": 0,
        "nation": 0,
        "itemType": "player",
        "itemState": item_state,
        "formation": FORMATION,
        # A full contract and full fitness: a card that cannot take the field
        # is the same as no card at all for a first match.
        "contract": 99,
        "fitness": 99,
        "injuryGames": 0,
        "injuryType": "none",
        "suspension": 0,
        "training": 0,
        "playStyle": play_style,
        # A quick sell pays this. Zero everywhere meant selling a card returned
        # nothing, which is also how the balance first showed up wrong.
        "discardValue": max(10, (rating - 40) ** 2 // 20) if rating else 0,
        "lastSalePrice": 0,
        "timestamp": 1,
        "untradeable": True,
        "rareflag": rare,
        "cardsubtypeid": 1 if rare else 0,
        "assists": 0,
        "lifetimeAssists": 0,
        "attributeList": [
            {"index": index, "value": value} for index, value in enumerate(attributes)
        ],
        "statsList": [{"index": index, "value": 0} for index in range(5)],
        "lifetimeStats": [{"index": index, "value": 0} for index in range(5)],
    }


class ClubInventory:
    """Every card the club owns, plus the squad that starts."""

    def __init__(self, pack_list: Path = PACK_LIST) -> None:
        document = json.loads(pack_list.read_text())
        packs = document["packList"]
        positions = _positions_by_asset()

        self.items: list[dict] = []
        self.squad: list[dict] = []
        next_id = FIRST_PLAYER_ITEM_ID

        for pack_index, pack in enumerate(packs):
            attributes = [pack[f"Attribute{n}"] for n in range(1, 7)]
            for slot, asset_id in enumerate(pack["squad"]):
                item = _player_item(
                    item_id=next_id,
                    asset_id=asset_id,
                    rating=pack["Rating"][slot],
                    rare=pack["Rare"][slot],
                    play_style=pack["playStyle"][slot],
                    team_id=pack["teamId"][slot],
                    attributes=[column[slot] for column in attributes],
                    position=positions.get(asset_id, FALLBACK_POSITION),
                )
                self.items.append(item)
                # The first pack becomes the starting squad; the rest stay in
                # the club as spares, which is what gives the transfer and club
                # screens something to show.
                if pack_index == 0:
                    self.squad.append(item)
                next_id += 1

        self.squad = _arrange(self.squad)
        self.items.extend(_presentation_items())

    # -- responses -------------------------------------------------------

    def club_response(self) -> bytes:
        return json.dumps({"itemData": self.items}, separators=(",", ":")).encode()

    def squad_list_response(self, name: str) -> bytes:
        rating = round(sum(item["rating"] for item in self.squad[:11]) / 11)
        return json.dumps(
            {
                "squad": [
                    {
                        "id": 1,
                        "squadName": name,
                        "rating": rating,
                        "chemistry": 100,
                        "formation": FORMATION,
                    }
                ]
            },
            separators=(",", ":"),
        ).encode()

    def active_squad_response(self, name: str) -> bytes:
        players = []
        for index, item in enumerate(self.squad):
            players.append(
                {
                    "index": index,
                    "itemData": item,
                    # Shirt numbers for the eleven who start; the bench carries
                    # zero, as retail does.
                    "kitNumber": index + 1 if index < 11 else 0,
                }
            )
        return json.dumps(
            {
                "personaId": 0,
                "id": 1,
                "squadName": name,
                "formation": FORMATION,
                "players": players,
                "manager": [],
                "actives": _presentation_items(),
            },
            separators=(",", ":"),
        ).encode()

    def purchased_items_response(self) -> bytes:
        return b'{"duplicateItemIdList":[],"itemData":[]}'


# -- the transfer market ---------------------------------------------------
#
# The club holds 92 cards. The catalogue holds 14019. The market is where the
# difference becomes visible: it is the one screen whose job is to show players
# you do not own.
#
# Listings are generated from the catalogue on demand rather than held, because
# 14019 standing auctions is not a market, it is a phone book. Each search
# returns the best matches for its filters.

MARKET_ITEM_ID_BASE = 1_800_000_000
MARKET_TRADE_ID_BASE = 1_900_000_000


def _price_for(rating: int, rareflag: int) -> int:
    """A plausible asking price, so the market is not uniformly free."""
    base = max(150, (rating - 40) ** 2 * 3)
    if rareflag:
        base *= 2
    return int(round(base / 50) * 50)


class CardCatalogue:
    """Every card in the game, searchable."""

    def __init__(self, path: Path = CARD_CATALOGUE) -> None:
        self.cards: list[dict] = []
        if path.exists():
            self.cards = json.loads(path.read_text()).get("cards", [])

    def search(self, query: dict[str, str], limit: int = 40) -> list[dict]:
        def wanted(card: dict) -> bool:
            position = query.get("position", "any")
            if position not in ("any", "", None) and card.get("position") != position:
                return False
            level = query.get("level", "any")
            if level not in ("any", "", None):
                rarity = (card.get("rarity") or "").lower()
                if level.lower() not in rarity:
                    return False
            for key, field in (("nation", "nationId"), ("league", "leagueId"), ("team", "clubId")):
                value = query.get(key)
                if value not in (None, "", "-1") and str(card.get(field)) != value:
                    return False
            name = (query.get("maskedDefId") or query.get("name") or "").strip().lower()
            if name and name not in (card.get("name") or "").lower():
                return False
            minr, maxr = query.get("minb"), query.get("maxb")
            rating = card.get("rating", 0)
            if minr and rating < int(minr):
                return False
            if maxr and rating > int(maxr):
                return False
            return True

        matches = [card for card in self.cards if wanted(card)]
        matches.sort(key=lambda card: -card.get("rating", 0))
        return matches[:limit]

    def auctions(self, query: dict[str, str], limit: int = 40) -> bytes:
        listings = []
        for offset, card in enumerate(self.search(query, limit)):
            price = _price_for(card.get("rating", 0), card.get("rareflag", 0))
            item = _player_item(
                item_id=MARKET_ITEM_ID_BASE + offset,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                item_state="forSale",
            )
            item["untradeable"] = False
            item["leagueId"] = card.get("leagueId", 0)
            item["nation"] = card.get("nationId", 0)
            listings.append(
                {
                    "tradeId": MARKET_TRADE_ID_BASE + offset,
                    "itemData": item,
                    "tradeState": "active",
                    "buyNowPrice": price,
                    "startingBid": max(150, price // 2),
                    "currentBid": 0,
                    "offers": 0,
                    "watched": False,
                    "bidState": "none",
                    "tradeOwner": False,
                    "expires": 3600,
                    "sellerName": "FUT",
                    "sellerEstablished": 2013,
                    "sellerId": 1,
                    "confidenceValue": 100,
                }
            )
        return json.dumps(
            {"auctionInfo": listings, "duplicateItemIdList": [], "total": len(listings)},
            separators=(",", ":"),
        ).encode()


# -- the coin balance ------------------------------------------------------
#
# The header reads its balance from whatever response last carried it, not at
# login: it showed a clean zero until the first quick sell, then uninitialised
# memory, because our quick-sell reply was an empty object and the parser never
# wrote the field.
#
# `totalCredits` is in CardsDLL's JSON member table, next to `total` and
# `totalGames`. `credits` and `coins` go out beside it: an unrecognised sibling
# at the top level is skipped, so naming all three costs nothing and a wrapper
# would have broken the parse, as {"userInfo":{...}} did.

STARTING_COINS = 50_000


class Wallet:
    """The club's coin balance, held for the life of the server."""

    def __init__(self, coins: int = STARTING_COINS) -> None:
        self.coins = coins

    def credit(self, amount: int) -> int:
        self.coins = max(0, self.coins + int(amount))
        return self.coins

    def debit(self, amount: int) -> int:
        return self.credit(-abs(int(amount)))

    def response(self) -> bytes:
        return json.dumps(
            {
                "totalCredits": self.coins,
                "credits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()
