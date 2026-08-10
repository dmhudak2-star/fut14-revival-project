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
import os
import random
import time

# How many cards go out when the club is asked for with no count of its own.
#
# The target is 77 KB, the largest club response this console was measured
# surviving -- eighteen times across three sessions. A card costs about 860
# bytes in this build, not the 550 it cost when those measurements were taken,
# so 90 hits that size where 140 came out at 120 KB.
#
# FIFA14_CLUB_LIMIT raises or lowers it; 0 restores the unbounded behaviour,
# which is what served 244 KB immediately before a FUT teardown.
try:
    CLUB_UNFILTERED_LIMIT = int(os.environ.get("FIFA14_CLUB_LIMIT", "90"))
except ValueError:
    CLUB_UNFILTERED_LIMIT = 90
if CLUB_UNFILTERED_LIMIT <= 0:
    CLUB_UNFILTERED_LIMIT = 10**9
from pathlib import Path


PACK_LIST = Path(__file__).resolve().parent / "icebreakerpacklist.json"

# resourceId carries the asset id in its low 24 bits and a version in the high
# byte; version 1 is the base card. The PC revival asserts the same invariant.
RESOURCE_VERSION = 0x0100_0000

# Item ids have to be unique and stable across a session, and must not collide
# with the presentation items below.
FIRST_PLAYER_ITEM_ID = 1_600_000_001

FORMATION = "f442"
CLUB_NAME_DEFAULT = "Fondateur FUT"

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


# Leagues the Xbox build does not appear to carry. A card from one of these
# draws with "Club undefined" and the raw localisation key
# "*leagueName_abbr15_0" instead of a league name -- the title is failing to
# resolve the id, not mis-reading the card.
#
# Which leagues those are cannot be determined from here: the catalogue holds
# 42 and the console knows some subset. This list is the suspect end of that
# range -- lower divisions and regional competitions -- and is meant to be
# corrected from what the screen actually shows rather than trusted as it is.
UNRESOLVED_LEAGUES = {
    2002,   # Nacional B
    2025,   # Liga do Brasil B
    332,    # Ukrayina Liha
    336,    # Liga Postobón
    347,    # South African FL
    371,    # Scotland League
}


def _card_resolves(card: dict) -> bool:
    """Keep only cards the title can actually draw.

    A missing club or league leaves the card showing placeholder text where its
    badge and competition should be, which is worse than the card not being
    offered at all.
    """
    if not (card.get("name") or "").strip():
        return False
    if not card.get("clubId") or not card.get("nationId"):
        return False
    if card.get("leagueId") in UNRESOLVED_LEAGUES:
        return False
    if (card.get("club") or "").strip().lower() in ("", "undefined"):
        return False
    return True


def _cards_by_asset() -> dict[int, dict]:
    """The catalogue keyed by asset id.

    The icebreaker packs carry an asset id, a rating, a rare flag, a club and
    six attributes -- but no position, nation or league. Leaving those at zero
    is why a club search for, say, a Cameroonian centre back returned the whole
    squad: every card matched every nation, because every card had nation 0.
    """
    if not CARD_CATALOGUE.exists():
        return {}
    document = json.loads(CARD_CATALOGUE.read_text())
    by_asset: dict[int, dict] = {}
    for card in document.get("cards", []):
        if _card_resolves(card):
            by_asset.setdefault(int(card["assetId"]), card)
    return by_asset

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
    nation: int = 0,
    league: int = 0,
    rarity: str = "",
) -> dict:
    return {
        "id": item_id,
        "assetId": asset_id,
        "resourceId": RESOURCE_VERSION | asset_id,
        "rating": rating,
        "preferredPosition": position,
        "teamid": team_id,
        "leagueId": league,
        "nation": nation,
        # Kept for our own filtering; the client ignores members it does not
        # know at the top level of an item.
        "rarity": rarity,
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


class ClubIdentity:
    """The club's name, as the player chose it.

    `PUT /ut/game/fifa14/user/club` carries `{"clubName": ..., "clubAbbr": ...}`
    and used to be answered `{}` and forgotten. The club-creation screen
    therefore worked -- the name was accepted and shown -- and the next load
    had no club again, because every other route still reported an empty name.
    """

    def __init__(self, name: str = "", abbr: str = "FUT") -> None:
        self.name = name
        self.abbr = abbr

    def adopt(self, document: dict) -> bool:
        if not isinstance(document, dict):
            return False
        name = str(document.get("clubName") or "").strip()
        abbr = str(document.get("clubAbbr") or "").strip()
        if not name and not abbr:
            return False
        if name:
            self.name = name
        if abbr:
            self.abbr = abbr
        return True

    def state(self) -> dict:
        return {"name": self.name, "abbr": self.abbr}

    def restore(self, saved: dict | None) -> None:
        if isinstance(saved, dict):
            self.name = str(saved.get("name") or "")
            self.abbr = str(saved.get("abbr") or "FUT")


CLUB_IDENTITY = ClubIdentity()


def first_run() -> bool:
    """Whether to start with no club at all.

    The club is seeded from every captain's squad in the icebreaker pack list
    -- all four, 358 cards -- because `fcc_login2` treats an empty squad as
    fatal. That seed is also why the first-time journey never appears: the
    captain selection exists to give a new player his first squad, and a
    player who already has one has nothing to choose.

    The two cannot both be true, so this makes the seed optional rather than
    guessing which side wins. With FIFA14_FIRST_RUN=1 the club starts empty
    and the answer is whatever the console then does: refuse the login the way
    the documented constraint says, or walk the icebreaker.

    Off by default. An empty club is known to break the login for an existing
    player, which is the state anyone not running this experiment is in.
    """
    return os.environ.get("FIFA14_FIRST_RUN", "").strip().lower() in {"1", "true", "yes"}


class ClubInventory:
    """Every card the club owns, plus the squad that starts."""

    def __init__(self, pack_list: Path = PACK_LIST, seeded: bool | None = None) -> None:
        document = json.loads(pack_list.read_text())
        packs = document["packList"]
        catalogue = _cards_by_asset()
        if seeded is None:
            seeded = not first_run()

        self.items: list[dict] = []
        self.squad: list[dict] = []
        next_id = FIRST_PLAYER_ITEM_ID
        if not seeded:
            packs = []

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
                    position=(catalogue.get(asset_id) or {}).get("position")
                    or FALLBACK_POSITION,
                    nation=(catalogue.get(asset_id) or {}).get("nationId", 0),
                    league=(catalogue.get(asset_id) or {}).get("leagueId", 0),
                    rarity=(catalogue.get(asset_id) or {}).get("rarity", ""),
                )
                self.items.append(item)
                # The first pack becomes the starting squad; the rest stay in
                # the club as spares, which is what gives the transfer and club
                # screens something to show.
                if pack_index == 0:
                    self.squad.append(item)
                next_id += 1

        self.squad = _arrange(self.squad) if self.squad else []
        if not seeded:
            # Nothing else either: kits, badges, stadiums and consumables are
            # club contents too, and a club that has not been created does not
            # own them. Serving them would leave the same contradiction one
            # tab further along.
            return
        self.items.extend(_presentation_items())
        # Consumables, kits, badges, stadiums, balls and staff. Without them
        # the Consommables, Elements club and Personnel tabs are empty and
        # their filters have nothing to act on.
        self.items.extend(_club_extras())

    # -- responses -------------------------------------------------------

    def club_response(self, query: dict[str, str] | None = None) -> bytes:
        """The club, filtered the way the club-search screen asks for it.

        Its parameter names are not the market's: `level`, `nation`, `league`,
        `team`, `position`, `count`. Ignoring them returned the whole club for
        every search, so looking for a Cameroonian centre back listed everyone.

        Ordered by rating, best first, the way the market search already is.
        Unsorted, the club and the squad's player picker listed cards in the
        order the icebreaker packs happened to add them, which reads as random.
        Sorting here rather than after the filter matters because the screen
        pages: slicing an unsorted list puts arbitrary cards on page one.

        `sorted` and not `.sort` -- with no query `items` is `self.items`
        itself, and sorting in place would reorder the club everywhere else.

        Players come first. Consumables carry a `rating` of their own -- a
        playStyle sits at 99 -- so ranking the club on rating alone buried the
        best player behind five of them. Within each group the order is still
        rating first, best down.
        """

        def order(item: dict) -> tuple:
            return (
                0 if item.get("itemType") == "player" else 1,
                -item.get("rating", 0),
                item.get("name", ""),
            )

        items = sorted(self.items, key=order)

        if query:

            def number(key: str) -> int | None:
                try:
                    return int(query[key])
                except (KeyError, TypeError, ValueError):
                    return None

            level = (query.get("level") or "").strip().lower()
            position = (query.get("position") or "").strip()
            nation, league = number("nation"), number("league")
            team = number("team")
            kind = (query.get("type") or "").strip().lower()

            def wanted(item: dict) -> bool:
                if kind and kind not in ("any", ""):
                    # The consumables screen asks for the family, not each
                    # kind: it reads the counts, then searches the club for
                    # "consumable". Comparing that against itemType -- which is
                    # contract, fitness, healing and so on -- matched nothing,
                    # so the screen found the counts and then no items.
                    if kind in ("consumable", "consumables"):
                        if (
                            item.get("itemType") not in CONSUMABLE_TYPES
                            and item.get("consumableType") not in CONSUMABLE_TYPES
                        ):
                            return False
                    elif item.get("itemType") != kind:
                        return False
                if position and position not in ("any", ""):
                    if item.get("preferredPosition") != position:
                        return False
                if level and level not in ("any", ""):
                    if level not in (item.get("rarity") or "").lower():
                        return False
                for value, field in (
                    (nation, "nation"),
                    (league, "leagueId"),
                    (team, "teamid"),
                ):
                    if value not in (None, -1) and item.get(field) != value:
                        return False
                return True

            items = [item for item in items if wanted(item)]
            # The club search pages too, and honouring only `count` meant every
            # page returned the same first results. The parameter it uses is
            # not certain, so accept the spellings it could send.
            start = 0
            for key in ("start", "skip", "offset", "from"):
                value = number(key)
                if value:
                    start = value
                    break
            count = number("count") or number("num") or 0
            items = items[start:] if start else items
            if count:
                items = items[:count]
        if not query:
            # Asked with no parameters at all, this used to return the whole
            # club in one document: 244 KB of JSON at 453 cards, growing with
            # every pack.
            #
            # Only that case. A filtered request without a count -- the club
            # screen asking for every player, say -- is left whole, because
            # bounding it would change what a search means, and the response
            # measured before the teardown was the bare one.
            #
            # The bound is the largest response the console was measured
            # surviving -- 77 KB, eighteen times across three sessions -- not a
            # guess. The one 244 KB response ever served was followed by the
            # FUT session tearing itself down and CardsDLL being unloaded.
            #
            # That is a correlation on a single observation, so this bounds an
            # unbounded response; it is not established as the fix for that
            # teardown. And it truncates: a club larger than the limit is not
            # shown whole to a screen that asked for all of it.
            items = items[:CLUB_UNFILTERED_LIMIT]
        return json.dumps({"itemData": items}, separators=(",", ":")).encode()

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

    # Named sides, keyed by the id the client uses. Slot 1 is the one loaded
    # at boot; the rest are whatever has been built since. Without this there
    # was one squad and no way to add, rename or drop another.
    def _squads(self) -> dict[int, dict]:
        if not hasattr(self, "_squad_store"):
            self._squad_store: dict[int, dict] = {
                1: {
                    # Named only once a club exists. In first-run mode the
                    # name is empty here too, or squad/list keeps asserting a
                    # club the rest of the responses say has not been created.
                    "name": "" if first_run() else "Fondateur FUT",
                    "formation": FORMATION,
                    "players": [item["id"] for item in self.squad],
                }
            }
        return self._squad_store

    def rename_active_squad(self, name: str) -> None:
        """Give the side the club's name once the club has one.

        The starting squad is named after the club, so a club created after the
        squad existed left the squad list still announcing nothing.
        """
        squads = self._squads()
        active = self.active_squad_id()
        if active in squads and name:
            squads[active]["name"] = name

    def squad_ids(self) -> list[int]:
        return sorted(self._squads())

    def save_squad(
        self,
        squad_id: int,
        item_ids: list[int],
        name: str | None = None,
        formation: str | None = None,
    ) -> int:
        """Write a side, creating it if the id is new.

        A squad id of zero or one the club has never seen means "make a new
        one" -- that is how the create button asks, and refusing it is why no
        second squad could exist.
        """
        squads = self._squads()
        if not squad_id or squad_id not in squads:
            squad_id = squad_id or (max(squads) + 1 if squads else 1)
        by_id = {item["id"] for item in self.items}
        # 0 is an empty slot, not an unknown card: keep it so the eleven do not
        # shift up into each other's positions.
        kept = [
            item_id if item_id in by_id else 0
            for item_id in item_ids
        ]
        if not any(kept):
            kept = []
        entry = squads.get(squad_id, {})
        squads[squad_id] = {
            "name": name or entry.get("name") or f"Équipe {squad_id}",
            "formation": formation or entry.get("formation") or FORMATION,
            "players": kept or entry.get("players") or [],
        }
        if squad_id == 1 and kept:
            self.set_squad(kept)
        return squad_id

    def delete_squad(self, squad_id: int) -> bool:
        squads = self._squads()
        # Slot 1 is the side the club plays with; deleting it would leave the
        # club with nothing to field.
        if squad_id == 1 or squad_id not in squads:
            return False
        del squads[squad_id]
        return True

    def squad_summaries(self) -> bytes:
        by_id = {item["id"]: item for item in self.items}
        entries = []
        for squad_id, squad in sorted(self._squads().items()):
            fielded = [by_id[i] for i in squad["players"][:11] if i in by_id]
            rating = (
                round(sum(item["rating"] for item in fielded) / len(fielded))
                if fielded
                else 0
            )
            entries.append(
                {
                    "id": squad_id,
                    "squadName": squad["name"],
                    "formation": squad["formation"],
                    "rating": rating,
                    "starRating": rating,
                    "chemistry": 100,
                }
            )
        return json.dumps({"squad": entries}, separators=(",", ":")).encode()

    def set_active(self, squad_id: int) -> None:
        """Remember which side the club is playing with.

        Nothing in the traffic says "make this one active" -- the choice is
        made in the screen and never sent, so it was lost on leaving FUT. What
        the client does do is load the chosen side by id, and that is taken as
        the signal here. It is an inference, not a contract.
        """
        if squad_id in self._squads():
            self.active_id = squad_id
            players = self._squads()[squad_id]["players"]
            if any(players):
                self.set_squad([i for i in players if i])

    def active_squad_id(self) -> int:
        return getattr(self, "active_id", 1)

    def squad_document(self, squad_id: int, name: str = "") -> bytes:
        """One named side, by id.

        Every squad id returned the active side, so a freshly created team came
        back holding the first team's players -- it looked pre-filled when it
        was in fact empty.
        """
        squads = self._squads()
        squad = squads.get(squad_id)
        if squad is None:
            return self.active_squad_response(name or CLUB_NAME_DEFAULT)
        by_id = {item["id"]: item for item in self.items}
        players = []
        for index in range(23):
            item_id = squad["players"][index] if index < len(squad["players"]) else 0
            item = by_id.get(item_id)
            players.append(
                {
                    "index": index,
                    # An empty slot is {"id": 0}, which is how the screen draws
                    # a gap rather than a card.
                    "itemData": item if item else {"id": 0},
                    "kitNumber": index + 1 if item and index < 11 else 0,
                }
            )
        fielded = [
            by_id[i] for i in squad["players"][:11] if i in by_id
        ]
        rating = (
            round(sum(item["rating"] for item in fielded) / len(fielded))
            if fielded
            else 0
        )
        return json.dumps(
            {
                "personaId": 0,
                "id": squad_id,
                "squadName": squad["name"],
                "formation": squad["formation"],
                "chemistry": 100 if fielded else 0,
                "starRating": rating,
                "rating": rating,
                "changed": False,
                "players": players,
                "manager": [],
                "actives": _presentation_items(),
            },
            separators=(",", ":"),
        ).encode()

    def set_squad(self, item_ids: list[int]) -> None:
        """Replace the starting eleven and bench with these cards, in order.

        The squad was the list built at load time and nothing could change it,
        so a card bought or pulled from a pack reached the club and then had
        nowhere to go: the assign screen found it among nothing it could field
        and simply backed out.
        """
        by_id = {item["id"]: item for item in self.items}
        chosen = [by_id[item_id] for item_id in item_ids if item_id in by_id]
        if chosen:
            self.squad = chosen

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
        starters = self.squad[:11] or self.squad
        rating = (
            round(sum(item["rating"] for item in starters) / len(starters))
            if starters
            else 0
        )
        return json.dumps(
            {
                "personaId": 0,
                "id": 1,
                "squadName": name,
                "formation": FORMATION,
                # FutSquadLoadServerResponse keeps these at the root, and the
                # squad screen never emitted a save without them: nothing in
                # the journal, not even a rejected one. `changed` is the one
                # that matters -- a squad that cannot mark itself modified has
                # no reason to be written back.
                "chemistry": 100,
                "starRating": rating,
                "rating": rating,
                "changed": False,
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

AUCTION_DURATION = 3600
# A day, so a listing does not lapse while the screen is being read.
AUCTION_WINDOW = 86400


def _now() -> int:
    return int(time.time())


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

    _issued = 0

    def __init__(self, path: Path = CARD_CATALOGUE) -> None:
        self.cards: list[dict] = []
        if path.exists():
            self.cards = [
                card
                for card in json.loads(path.read_text()).get("cards", [])
                if _card_resolves(card)
            ]
        # Listings are generated per search, so a bid arriving later refers to
        # a trade id that no longer exists anywhere unless it is remembered.
        self.served: dict[int, dict] = {}
        # Cards already bought. Without this the market regenerates the same
        # listing on the next search and the player you just paid for is still
        # sitting there for sale.
        self.sold: set[int] = set()

    def search(self, query: dict[str, str]) -> tuple[list[dict], int]:
        """Filtered, sorted, paged. Returns the page and the full match count.

        The market's parameter names are its own -- `lev` not `level`,
        `definitionId` for one specific player, `start` and `num` for paging.
        Filtering on the names the club search uses meant none of them applied,
        and ignoring `start` meant every page returned the same forty cards.
        """

        def number(key: str) -> int | None:
            value = query.get(key)
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        definition = number("definitionId") or number("maskedDefId")
        level = (query.get("lev") or query.get("level") or "").strip().lower()
        position = (query.get("pos") or query.get("position") or "").strip()
        nation = number("nat") if query.get("nat") else number("nation")
        league = number("leag") if query.get("leag") else number("league")
        team = number("team")
        min_rating, max_rating = number("minb"), number("maxb")

        def wanted(card: dict) -> bool:
            if definition and card.get("assetId") != definition:
                return False
            if level and level not in ("any", ""):
                if level not in (card.get("rarity") or "").lower():
                    return False
            if position and position not in ("any", ""):
                if card.get("position") != position:
                    return False
            for value, field in (
                (nation, "nationId"),
                (league, "leagueId"),
                (team, "clubId"),
            ):
                if value not in (None, -1) and card.get(field) != value:
                    return False
            rating = card.get("rating", 0)
            if min_rating is not None and rating < min_rating:
                return False
            if max_rating is not None and rating > max_rating:
                return False
            return True

        # No exclusion by asset. Removing a bought player took every version
        # of him off the market -- the three Benatias share one asset id, so
        # buying the 90 hid the 86 and the 84 as well. A market carries many
        # copies of the same card; that is what a market is.
        matches = [card for card in self.cards if wanted(card)]
        matches.sort(key=lambda card: (-card.get("rating", 0), card.get("name", "")))

        start = number("start") or 0
        count = number("num") or number("count") or 20
        return matches[start : start + count], len(matches)

    def auctions(self, query: dict[str, str], coins: int | None = None) -> bytes:
        page, total = self.search(query)
        listings = []
        try:
            offset = int(query.get("start") or 0)
        except ValueError:
            offset = 0
        for index, card in enumerate(page):
            price = _price_for(card.get("rating", 0), card.get("rareflag", 0))
            # Unique per listing served, not derived from the page position.
            # Deriving it meant buying the same slot twice produced the same
            # item id, and the club refuses an id it already holds -- which is
            # why one particular player could never be bought again while
            # everyone else could.
            CardCatalogue._issued += 1
            item = _player_item(
                item_id=MARKET_ITEM_ID_BASE + CardCatalogue._issued,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                # "forSale" marks a card *you* have listed, and the screen
                # offers no bid or buy on your own listing -- pressing A did
                # nothing at all. A card on someone else's auction is "free".
                item_state="free",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
            item["untradeable"] = False
            item["owners"] = 1
            item["lastSalePrice"] = 0
            listing = {
                    # Order matters. These parsers read a stream of members and
                    # an unrecognised one can end the object early, taking
                    # everything after it with it -- which is consistent with
                    # the prices showing (they come first) while "Temps
                    # restant" stayed blank and the actions panel opened empty.
                    #
                    # So the timing goes near the front, and the stray "id"
                    # member this code used to add -- which is not part of an
                    # auction record -- is gone.
                    "tradeId": MARKET_TRADE_ID_BASE + offset + index,
                    "tradeState": "active",
                    # Buying failed with "la liste a expiré" while the panel
                    # itself worked, so the expiry check is what refuses it.
                    # The bounds were this machine's clock, and the console has
                    # been offline for years -- its clock is not this one's, so
                    # any absolute comparison is a coin toss.
                    #
                    # So: a window that cannot be outside whatever the console
                    # believes the time is, and a long relative countdown.
                    # EXPIRE_TIME is the member CardsDLL names beside
                    # FUT_AUCTION_EXPIRED, and this server had never sent it --
                    # expires, startTime and endtime are none of them. Sent
                    # alongside the others rather than instead of them, since
                    # those are what made the Actions entry appear.
                    "expires": AUCTION_WINDOW,
                    "EXPIRE_TIME": AUCTION_WINDOW,
                    "expireTime": AUCTION_WINDOW,
                    "startTime": 0,
                    "endtime": 2147483647,
                    "buyNowPrice": price,
                    "startingBid": max(150, price // 2),
                    "currentBid": 0,
                    "offers": 0,
                    "watched": False,
                    "bidState": "none",
                    "tradeOwner": False,
                    "sellerName": "FUT",
                    "sellerEstablished": 2013,
                    "sellerId": 1,
                    "confidenceValue": 100,
                    "itemData": item,
            }
            self.served[listing["tradeId"]] = listing
            listings.append(listing)
        # `total` is the size of the whole result set, not of this page: it is
        # what the screen pages against.
        document = {
            "auctionInfo": listings,
            "duplicateItemIdList": [],
            "total": total,
        }
        if coins is not None:
            document.update({"credits": coins, "totalCredits": coins, "coins": coins})
        return json.dumps(document, separators=(",", ":")).encode()

    def status_for(self, trade_ids: list[int], coins: int) -> bytes:
        """Answer /trade/status?tradeIds=... with those auctions.

        The client polls this for the specific auction it is about to bid on.
        Answering with an empty list -- whatever was asked -- told it nothing
        about that auction, and it refused with "Auction state is invalid for
        bidding". The listing has to come back, by id.
        """
        found = [
            self.served[trade_id]
            for trade_id in trade_ids
            if trade_id in self.served
        ]
        return json.dumps(
            {
                "auctionInfo": found,
                "duplicateItemIdList": [],
                "total": len(found),
                "credits": coins,
                "totalCredits": coins,
                "coins": coins,
            },
            separators=(",", ":"),
        ).encode()


    def bid(self, trade_id: int, amount: int, wallet: "Wallet") -> tuple[bytes, dict | None]:
        """Bid on, or buy outright, a listing this server served earlier.

        Returns the reply and the item won, if the bid took it. A bid at or
        above the buy-now price ends the auction immediately, which is how the
        Buy Now button behaves; anything less is recorded as the standing bid.
        """
        listing = self.served.get(trade_id)
        if listing is None:
            return (
                json.dumps(
                    {"reason": "INVALID_REQUEST", "tradeId": trade_id},
                    separators=(",", ":"),
                ).encode(),
                None,
            )
        buy_now = int(listing.get("buyNowPrice") or 0)
        if amount > wallet.coins:
            return (
                json.dumps(
                    {"reason": "INSUFFICIENT_COINS", "credits": wallet.coins},
                    separators=(",", ":"),
                ).encode(),
                None,
            )
        wallet.debit(amount)
        won = buy_now and amount >= buy_now
        listing = dict(listing)
        listing["currentBid"] = amount
        listing["bidState"] = "highest"
        listing["tradeState"] = "closed" if won else "active"
        listing["offers"] = int(listing.get("offers") or 0) + 1
        listing["credits"] = wallet.coins
        listing["totalCredits"] = wallet.coins
        listing["coins"] = wallet.coins
        self.served[trade_id] = listing
        item = listing.get("itemData") if won else None
        if won and isinstance(item, dict) and item.get("assetId"):
            self.sold.add(int(item["assetId"]))
        return json.dumps(listing, separators=(",", ":")).encode(), item



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

STARTING_COINS = 1_000_000


class Wallet:
    """The club's coin balance, held for the life of the server."""

    def __init__(self, coins: int = STARTING_COINS) -> None:
        self.coins = coins

    def credit(self, amount: int) -> int:
        self.coins = max(0, self.coins + int(amount))
        return self.coins

    def debit(self, amount: int) -> int:
        return self.credit(-abs(int(amount)))

    def auction_state(self) -> bytes:
        """An empty trade list that still reports the balance."""
        return json.dumps(
            {
                "auctionInfo": [],
                "duplicateItemIdList": [],
                "total": 0,
                "credits": self.coins,
                "totalCredits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()

    def credits_response(self) -> bytes:
        """FutUserCreditsServerResponse, as the native parser reads it.

        The currency names are compared against the literal lower-case strings
        `coins` and `points`. Sending "COINS" -- which is what the PC
        reference's fixture uses -- matches nothing, so the balance is never
        written and the header keeps whatever it had.

        `unopenedPacks` has to be an object, not an array: the parser descends
        into it for preOrderPacks/recoveredPacks, and an array leaves it walking
        the wrong token type.
        """
        return json.dumps(
            {
                "credits": self.coins,
                "currencies": [
                    {"name": "coins", "funds": self.coins, "finalFunds": self.coins},
                    {"name": "points", "funds": 0, "finalFunds": 0},
                ],
                "unopenedPacks": {"preOrderPacks": 0, "recoveredPacks": 0},
            },
            separators=(",", ":"),
        ).encode()

    def user_info(self, club_name: str, club_abbr: str) -> bytes:
        """FutGetUserInfo, flat -- there is no `userInfo` wrapper.

        Wrapping it is what made the club header print 0xCDCDCDCD: the parser
        did not recognise the shape and never wrote the fields at all.
        """
        return json.dumps(
            {
                "personaId": 0,
                "clubName": club_name,
                "clubAbbr": club_abbr,
                # A player who has not named his club must be allowed to. This
                # was flatly False, which tells a brand-new account the one
                # thing it most needs to do is forbidden.
                "clubNameChangeAllowed": not club_name,
                # A club that does not exist was not established in 2013.
                #
                # `fcc_login1` sends `createClub` instead of `iceBreaker` when
                # `SkipIceBreaker() || GetFUT1TeamName().IS_RETURN_USER`.
                # SkipIceBreaker was disassembled and returns 0 always -- `li
                # r3, 0` and a branch to the marshaller, two instructions -- so
                # the second term is the one that was true. The object it reads
                # is built at 0x890EECD0, which writes `CLUB_EST` from +0x5C of
                # the user record and `IS_RETURN_USER` from the byte at +0x8D.
                # Serving a founding year for a club with no name is the same
                # contradiction the club name itself was, one field along.
                "established": 2013 if club_name else 0,
                "divisionOffline": 10,
                "divisionOnline": 10,
                "won": 0,
                "draw": 0,
                "loss": 0,
                "seasonTicket": False,
                "fifaPointsFromLastYear": 0,
                "fifaPointsTransferredStatus": 0,
                # Aliases some of the UI binders read instead.
                "coins": self.coins,
                "credits": self.coins,
                "points": 0,
                "fifaPoints": 0,
            },
            separators=(",", ":"),
        ).encode()

    def response(self) -> bytes:
        return json.dumps(
            {
                "totalCredits": self.coins,
                "credits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()


# -- opening a pack --------------------------------------------------------

# The nine packs FIFA 14 sells. Tier decides which cards can come out, count
# how many, and rares how many of them are rare -- a gold pack that draws
# bronze cards is not a gold pack.
PACK_SPECS: dict[int, dict] = {
    103: {"name": "Bronze Pack", "tier": "bronze", "coins": 400, "points": 0,
          "count": 12, "rares": 1, "premium": False, "group": "Bronze Packs"},
    104: {"name": "Premium Bronze Pack", "tier": "bronze", "coins": 750,
          "points": 0, "count": 12, "rares": 3, "premium": True,
          "group": "Bronze Packs"},
    203: {"name": "Silver Pack", "tier": "silver", "coins": 2500, "points": 50,
          "count": 12, "rares": 1, "premium": False, "group": "Silver Packs"},
    204: {"name": "Premium Silver Pack", "tier": "silver", "coins": 3750,
          "points": 75, "count": 12, "rares": 3, "premium": True,
          "group": "Silver Packs"},
    303: {"name": "Gold Pack", "tier": "gold", "coins": 5000, "points": 100,
          "count": 12, "rares": 1, "premium": False, "group": "Gold Packs"},
    304: {"name": "Premium Gold Pack", "tier": "gold", "coins": 7500,
          "points": 150, "count": 12, "rares": 3, "premium": True,
          "group": "Gold Packs"},
    305: {"name": "Jumbo Gold Pack", "tier": "gold", "coins": 10000,
          "points": 0, "count": 24, "rares": 7, "premium": True,
          "group": "Gold Packs"},
    306: {"name": "Gold Players Pack", "tier": "gold", "coins": 15000,
          "points": 0, "count": 12, "rares": 1, "premium": False,
          "group": "Gold Packs"},
    307: {"name": "Premium Gold Players Pack", "tier": "gold", "coins": 25000,
          "points": 0, "count": 12, "rares": 3, "premium": True,
          "group": "Gold Packs"},
}

GOLD_PACK_ID = 304
GOLD_PACK_PRICE = PACK_SPECS[GOLD_PACK_ID]["coins"]
PACK_ITEM_ID_BASE = 1_950_000_000

# Which ratings belong to which tier, so a bronze pack cannot hand you Messi.
TIER_RATINGS = {
    "bronze": (0, 64),
    "silver": (65, 74),
    "gold": (75, 99),
}

# The ordinary cards: what a player actually collects. Everything else in the
# catalogue -- Team of the Week, Team of the Season, World Cup, Legend, MOTM,
# iMOTM, Team of the Year, Record Breaker -- is a special, and specials are
# not what a new account should be handed on its first day.
ORDINARY_RARITIES = {
    "non-rare bronze",
    "rare bronze",
    "non-rare silver",
    "rare silver",
    "non-rare gold",
    "rare gold",
}

# The three packs a new club opens with, poorest first.
STARTER_PACKS = (103, 203, 303)

# A starter gold pack should not be a jackpot. Ratings above this are dropped
# from the draw entirely rather than merely made unlikely, because "unlikely"
# on twelve cards across three packs still hands out a 90 often enough.
STARTER_RATING_CAP = 78


def is_ordinary(card: dict) -> bool:
    return (card.get("rarity") or "").strip().lower() in ORDINARY_RARITIES


def store_catalogue(timestamp: int = 2147483647) -> bytes:
    """Every pack, priced, grouped and buyable."""
    purchases = []
    for index, (pack_id, spec) in enumerate(sorted(PACK_SPECS.items())):
        currencies = [
            {"name": "coins", "funds": spec["coins"], "finalFunds": spec["coins"]}
        ]
        if spec["points"]:
            currencies.append(
                {
                    "name": "points",
                    "funds": spec["points"],
                    "finalFunds": spec["points"],
                }
            )
        # The asset id names the tier's artwork, not the pack: the PC
        # reference ships assetId 3 for the gold pack. Deriving it from the
        # pack id resolved to nothing and the bronze tiles drew NOT FOUND.
        tier_asset = {"bronze": 1, "silver": 2, "gold": 3}[spec["tier"]]
        purchases.append(
            {
                "id": pack_id,
                "assetId": tier_asset,
                "actionType": "CREATEPACK",
                "packType": "CARDPACK",
                "description": f"FUT_STORE_PACK_{pack_id}_DESC",
                "displayGroup": {"priority": index, "value": spec["tier"]},
                "displayGroupAssetId": tier_asset,
                "displayGroupUseDefaultImage": True,
                "useDefaultImage": True,
                "isPremium": spec["premium"],
                "dealType": "REGULAR",
                "saleType": "NONE",
                "state": "active",
                "visible": 1,
                "sortPriority": index,
                "currencies": currencies,
            }
        )
    return json.dumps(
        {"purchase": purchases, "timestamp": timestamp}, separators=(",", ":")
    ).encode()


class PackShop:
    """Sells any pack in the catalogue, and draws cards that match its tier."""

    def __init__(
        self,
        catalogue: "CardCatalogue",
        wallet: "Wallet",
        inventory: "ClubInventory | None" = None,
    ) -> None:
        self.catalogue = catalogue
        self.wallet = wallet
        # Needed to tell a duplicate from a new card: a duplicate is one the
        # club already holds, which is a question only the club can answer.
        self.inventory = inventory
        self.purchases = 0
        # Cards drawn but not yet acknowledged by the client. The purchased
        # items endpoint reports these, which is how they reach the club.
        self.pending: list[dict] = []
        self._pools: dict[str, list[dict]] = {}
        for tier, (low, high) in TIER_RATINGS.items():
            self._pools[tier] = [
                card
                for card in catalogue.cards
                if low <= card.get("rating", 0) <= high
            ]

    def grant_starter_packs(self, rng: random.Random | None = None) -> int:
        """Draw the three packs a new club opens with, into the pending pile.

        Bronze, silver, gold. Ordinary cards only -- no Team of the Week, no
        Team of the Season, no Legend -- and nothing above
        `STARTER_RATING_CAP`, so the gold pack is a start rather than a
        jackpot. They cost nothing.

        The pending pile is the route the client already reads through
        purchased/items and sends to the club; the native `unopenedPacks` /
        `starterPack` members exist in CardsDLL but the route that carries
        them is not established here, and guessing one is how screens get
        frozen.
        """
        rng = rng or random.Random()
        drawn_total = 0
        for pack_id in STARTER_PACKS:
            spec = self.spec(pack_id)
            if spec is None:
                continue
            pool = [
                card
                for card in (self._pools.get(spec["tier"]) or self.catalogue.cards)
                if is_ordinary(card)
                and card.get("rating", 0) <= STARTER_RATING_CAP
            ]
            if not pool:
                continue
            rares = [card for card in pool if card.get("rareflag")] or pool
            commons = [card for card in pool if not card.get("rareflag")] or pool
            drawn = []
            for slot in range(int(spec["count"])):
                source = rares if slot < int(spec["rares"]) else commons
                card = rng.choice(source)
                item = _player_item(
                    item_id=PACK_ITEM_ID_BASE + 900_000 + drawn_total,
                    asset_id=card["assetId"],
                    rating=card.get("rating", 0),
                    rare=card.get("rareflag", 0),
                    play_style=0,
                    team_id=card.get("clubId", 0),
                    attributes=card.get("attributes", [0] * 6),
                    position=card.get("position") or FALLBACK_POSITION,
                    item_state="new",
                    nation=card.get("nationId", 0),
                    league=card.get("leagueId", 0),
                    rarity=card.get("rarity", ""),
                )
                item["untradeable"] = False
                drawn.append(item)
                drawn_total += 1
            self._mark_duplicates(drawn)
            self.pending.extend(drawn)
        return drawn_total

    def spec(self, pack_id: int) -> dict | None:
        return PACK_SPECS.get(int(pack_id))

    def price(self, pack_id: int) -> int:
        spec = self.spec(pack_id)
        return int(spec["coins"]) if spec else GOLD_PACK_PRICE

    def can_afford(self, pack_id: int = GOLD_PACK_ID) -> bool:
        return self.wallet.coins >= self.price(pack_id)

    def open_pack(
        self, pack_id: int = GOLD_PACK_ID, rng: random.Random | None = None
    ) -> bytes:
        rng = rng or random.Random()
        spec = self.spec(pack_id) or PACK_SPECS[GOLD_PACK_ID]
        self.wallet.debit(int(spec["coins"]))
        self.purchases += 1

        pool = self._pools.get(spec["tier"]) or self.catalogue.cards
        rares = [card for card in pool if card.get("rareflag")]
        commons = [card for card in pool if not card.get("rareflag")] or pool

        drawn = []
        for slot in range(int(spec["count"])):
            # The rare slots come first, as retail does -- a pack that promises
            # three rares has to actually contain three.
            source = rares if slot < int(spec["rares"]) and rares else commons
            card = rng.choice(source)
            item = _player_item(
                item_id=PACK_ITEM_ID_BASE + self.purchases * 100 + slot,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                item_state="new",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
            item["untradeable"] = False
            drawn.append(item)
        duplicate_pairs = self._mark_duplicates(drawn)
        self.pending.extend(drawn)
        return json.dumps(
            {
                "numberItems": len(drawn),
                "purchasedPackId": int(pack_id),
                "itemList": drawn,
                # Pairs, never bare new ids -- see _mark_duplicates. This was
                # empty, and with it empty the pack screen showed a repeat as
                # an ordinary card however clearly the card itself was marked.
                "duplicateItemIdList": duplicate_pairs,
                "credits": self.wallet.coins,
                "totalCredits": self.wallet.coins,
                "coins": self.wallet.coins,
            },
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def _signature(item: dict):
        """What makes two cards the same card.

        `resourceId`, exactly -- not `assetId`. A player's special versions all
        share his asset id: a Team of the Season Ruffier and a Rare Gold
        Ruffier are both asset 167628 and are not the same card. Keying on the
        asset flags the special as a repeat of the ordinary one.

        `assetId` with the rare flag is the fallback for a card that carries no
        resource id, which is better than nothing but cannot tell two specials
        of the same player apart.
        """
        resource = item.get("resourceId")
        if resource:
            return ("resource", resource)
        return ("asset", item.get("assetId"), item.get("rareflag"))

    def _mark_duplicates(self, drawn: list[dict]) -> list[dict]:
        """Pair each new card with the owned card it repeats.

        Two shapes go out, because the singular and the plural are read in
        different places. `duplicateItemId` on the card itself names the one it
        repeats; `duplicateItemIdList` carries the same pairing as records:

            {"itemId": <new>, "duplicateItemId": <owned>}

        The list is what the FIFA 14 pack screen actually reads -- reported
        independently from a build where marking only the card did not show a
        duplicate at all. What must never go back in that list is a bare list
        of the *new* ids, which is what froze the title: it told the screen to
        compare each card against itself.
        """
        owned: dict[tuple, int] = {}
        if self.inventory is not None:
            for item in self.inventory.items:
                owned.setdefault(self._signature(item), item["id"])
        pairs: list[dict] = []
        for item in drawn:
            key = self._signature(item)
            existing = owned.get(key)
            if existing and existing != item["id"]:
                item["duplicateItemId"] = existing
                pairs.append({"itemId": item["id"], "duplicateItemId": existing})
            else:
                owned.setdefault(key, item["id"])
        return pairs

    def _duplicates(self, drawn: list[dict]) -> list[int]:
        """The ids among these the club already owns.

        A duplicate is the same asset in the same version -- a rare Messi and a
        base Messi are two different cards, and only one of them is a repeat.
        Cards drawn twice inside a single pack count too, after the first.
        """
        owned = set()
        if self.inventory is not None:
            owned = {
                (item.get("assetId"), item.get("rareflag"))
                for item in self.inventory.items
            }
        duplicates, seen = [], set()
        for item in drawn:
            signature = (item.get("assetId"), item.get("rareflag"))
            if signature in owned or signature in seen:
                duplicates.append(item["id"])
            seen.add(signature)
        return duplicates

    def purchased_items(self) -> bytes:
        return json.dumps(
            {
                # Empty on purpose, and the binary says why the shape was
                # wrong: CardsDLL carries GetCardDuplicate and HAS_DUPLICATE,
                # so the client asks for *the card already owned* that a new
                # one duplicates. Listing the new ids here points it at the
                # card it is holding, which is very likely the loop it hung in.
                #
                # Reporting duplicates here froze the title
                # outright: after buying a second Chamakh the client fetched
                # squad/active, tradePile and this, and stopped dead. The pack
                # response carries the same list without trouble, so the fault
                # is this document specifically -- most likely the ids it wants
                # are the cards already owned rather than the new ones, and a
                # freeze is not worth guessing through.
                "duplicateItemIdList": [],
                "itemData": self.pending,
                "credits": self.wallet.coins,
                "totalCredits": self.wallet.coins,
                "coins": self.wallet.coins,
            },
            separators=(",", ":"),
        ).encode()

    def refused(self) -> bytes:
        return json.dumps({"reason": "INSUFFICIENT_COINS"}, separators=(",", ":")).encode()


# -- what you can do with a card -------------------------------------------
#
# Retail pile numbers. Squad membership is not a pile: players in the eleven
# are still owned in the club.
CONSUMABLE_TYPES = {
    "contract", "fitness", "healing", "training", "position", "playStyle",
}


PILE_TRANSFER = 5
PILE_PURCHASED = 6
PILE_CLUB = 7


class CardActions:
    """Moving cards between piles, and quick-selling them."""

    def __init__(
        self,
        shop: "PackShop",
        wallet: "Wallet",
        inventory: "ClubInventory | None" = None,
    ) -> None:
        self.shop = shop
        self.wallet = wallet
        self.inventory = inventory
        # Cards taken out of a pack and kept. When an inventory is attached,
        # this *is* its item list -- otherwise a card sent to the club is held
        # here and never appears in the club, which is exactly how it looked:
        # the send succeeded, the card vanished.
        self.club: list[dict] = []
        # Pile 5: cards set aside to be listed, not yet on the market.
        self.transfer: list[dict] = []
        # Cards actually listed, keyed by trade id.
        self.listings: dict[int, dict] = {}
        # Item ids the client tried to move that this server has never held.
        # Every one of these is a card the player saw and lost.
        self.unmatched: list[int] = []
        self._next_trade_id = 2_000_000_000

    def _take_pending(self, item_id: int) -> dict | None:
        for index, item in enumerate(self.shop.pending):
            if item["id"] == item_id:
                return self.shop.pending.pop(index)
        return None

    def move(self, document: dict) -> bytes:
        """PUT /item -- send to club, or to the transfer list.

        The body is {"itemData":[{"id":N,"pile":7}, ...]} and each entry has to
        be acknowledged individually. Answering with a club search, which is
        what the old fixture did, acknowledges nothing: the card stays in the
        pack screen and the button appears dead.
        """
        entries = document.get("itemData") if isinstance(document, dict) else None
        results = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            try:
                item_id = int(entry.get("id") or entry.get("itemId") or 0)
            except (TypeError, ValueError):
                continue
            pile = entry.get("pile", PILE_CLUB)
            try:
                pile = int(pile)
            except (TypeError, ValueError):
                pile = PILE_CLUB
            item = self._take_pending(item_id)
            if item is None:
                for index, owned in enumerate(self.club):
                    if owned["id"] == item_id:
                        item = self.club.pop(index)
                        break
            if item is not None:
                if pile == PILE_TRANSFER:
                    # Set aside to be listed. It leaves the club until it is
                    # either listed or moved back -- taking it out of the
                    # inventory too, or the card shows in both places at once,
                    # which is what looked like a duplicate.
                    item["itemState"] = "forSale"
                    self._forget(item_id)
                    if not any(held["id"] == item_id for held in self.transfer):
                        self.transfer.append(item)
                else:
                    item["itemState"] = "free"
                    self._keep(item)
            if item is None:
                # An id neither pending nor already held. This used to be
                # acknowledged as a success anyway, which is how a card could
                # be drawn, shown, sent to the club, confirmed -- and then
                # exist nowhere. A TOTS Ruffier went that way.
                #
                # Reporting the failure is the honest answer and the only one
                # that can be noticed. The card is still gone, but the client
                # is no longer told otherwise.
                self.unmatched.append(item_id)
                results.append(
                    {
                        "id": item_id,
                        "success": False,
                        "reason": "item not found",
                        "errorCode": 461,
                        "pile": pile,
                    }
                )
                continue
            results.append(
                {
                    "id": item_id,
                    "success": True,
                    "reason": "",
                    "errorCode": 0,
                    "pile": pile,
                }
            )
        return json.dumps({"itemData": results}, separators=(",", ":")).encode()

    def discard_many(self, item_ids: list[int]) -> bytes:
        """Quick sell one card or a whole pack at once.

        The client sends {"itemId":[...]} -- always a list, even for one card,
        and twelve entries long when you sell a pack outright. Reading it as a
        single integer produced no id at all, so the reply named no item and the
        screen raised an error.
        """
        sold, awarded = [], 0
        for item_id in item_ids:
            item = self._take_pending(item_id)
            if item is None:
                for index, owned in enumerate(self.club):
                    if owned["id"] == item_id:
                        item = self.club.pop(index)
                        break
            if item is not None:
                self._forget(item_id)
            awarded += int(item["discardValue"]) if item else 200
            sold.append({"id": item_id})
        self.wallet.credit(awarded)
        return json.dumps(
            {"totalCredits": awarded, "items": sold},
            separators=(",", ":"),
        ).encode()

    def discard(self, item_id: int | None = None) -> bytes:
        """Quick sell.

        `totalCredits` here is what this sale paid, not the resulting balance --
        the absolute figure comes from /user/credits. Returning the whole
        balance made the sale appear to pay tens of thousands.
        """
        item = self._take_pending(item_id) if item_id else None
        if item is None:
            for index, owned in enumerate(self.club):
                if item_id and owned["id"] == item_id:
                    item = self.club.pop(index)
                    break
        awarded = int(item["discardValue"]) if item else 200
        self.wallet.credit(awarded)
        return json.dumps(
            {
                "totalCredits": awarded,
                "items": [{"id": item_id}] if item_id else [],
            },
            separators=(",", ":"),
        ).encode()


    def _forget(self, item_id: int) -> None:
        """Drop a card from the club, wherever it is held."""
        self.club = [held for held in self.club if held["id"] != item_id]
        if self.inventory is not None:
            self.inventory.items = [
                held for held in self.inventory.items if held["id"] != item_id
            ]

    def _keep(self, item: dict) -> None:
        """Put a card in the club.

        A duplicate is allowed in. Refusing it here was wrong: owning a second
        copy is legitimate, and it is the moment of keeping it that the game
        offers to compare the two and decide -- sell one, swap, or hold both.
        Silently dropping it made "send to club" do nothing at all for exactly
        the card that needed a decision.

        What is still refused is the same item id arriving twice, which is not
        a second card but the same one counted again.
        """
        if any(held["id"] == item["id"] for held in self.club):
            return

        # Duplicates are allowed in. The card carries duplicateItemId naming
        # the one it repeats, so the screen can offer the comparison itself --
        # which is the retail behaviour, and better than deciding for the
        # player as the auto-sell stopgap did.
        self.club.append(item)
        if self.inventory is not None:
            if any(held["id"] == item["id"] for held in self.inventory.items):
                return
            self.inventory.items.append(item)

    def _find(self, item_id: int) -> dict | None:
        for pool in (self.transfer, self.club, self.shop.pending):
            for item in pool:
                if item["id"] == item_id:
                    return item
        return None

    def list_for_sale(self, document: dict) -> bytes:
        """POST /auctionhouse -- put a card on the market.

        The body names the item and the prices; the reply has to hand back a
        trade id, because that is what the trade pile and every later bid or
        withdrawal refer to.
        """
        item_data = document.get("itemData") if isinstance(document, dict) else None
        if isinstance(item_data, list):
            item_data = item_data[0] if item_data else None
        item_id = None
        if isinstance(item_data, dict):
            item_id = item_data.get("id") or item_data.get("itemId")
        if item_id is None:
            item_id = document.get("itemId") or document.get("id")
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            item_id = None

        def price(key: str, fallback: int) -> int:
            try:
                return int(document.get(key) or fallback)
            except (TypeError, ValueError):
                return fallback

        item = self._find(item_id) if item_id else None
        starting = price("startingBid", 150)
        buy_now = price("buyNowPrice", max(200, starting * 2))
        duration = price("duration", 3600)

        self._next_trade_id += 1
        trade_id = self._next_trade_id
        listing = {
            "tradeId": trade_id,
            "id": trade_id,
            "itemData": item or {"id": item_id},
            "tradeState": "active",
            "startingBid": starting,
            "buyNowPrice": buy_now,
            "currentBid": 0,
            "offers": 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": True,
            "expires": duration,
            "sellerName": "Fondateur FUT",
            "sellerEstablished": 2013,
            "sellerId": 0,
            "confidenceValue": 100,
        }
        self.listings[trade_id] = listing
        for pool in (self.transfer, self.club):
            for index, owned in enumerate(pool):
                if owned["id"] == item_id:
                    pool.pop(index)
                    break
        return json.dumps(listing, separators=(",", ":")).encode()

    def trade_pile(self, coins: int) -> bytes:
        """Everything currently listed, plus the balance the header reads."""
        return json.dumps(
            {
                "auctionInfo": list(self.listings.values()),
                "duplicateItemIdList": [],
                "total": len(self.listings),
                "credits": coins,
                "totalCredits": coins,
                "coins": coins,
            },
            separators=(",", ":"),
        ).encode()

    def withdraw(self, trade_id: int) -> bytes:
        """Pull a listing back; the card returns to the transfer pile."""
        listing = self.listings.pop(trade_id, None)
        if listing and isinstance(listing.get("itemData"), dict):
            item = listing["itemData"]
            if "assetId" in item:
                self.transfer.append(item)
        return json.dumps({"id": trade_id}, separators=(",", ":")).encode()


# -- everything a club owns that is not a player ---------------------------
#
# The club screens have tabs for consumables, club items and staff, and a
# search that filters on them. Serving players only leaves those tabs empty and
# their filters inert.

CLUB_ITEM_ID_BASE = 1_750_000_000

# The consumables come out of the game's own card database -- see
# tools/build_consumables.py. They used to be invented here: three grades per
# family with asset ids counted up from 1000, which drew NOT FOUND art on every
# card, named all of them "Entraînement equipe" and applied nothing. The title
# looks a consumable up by its subtype and draws it by its asset id, so neither
# is ours to choose.
CONSUMABLE_FILE = Path(__file__).resolve().parent / "fifa14_consumables.json"

# Contracts and fitness are what a club actually runs out of, so it carries a
# stack of each; one of everything else is enough to apply it.
CONSUMABLE_COPIES = {"contract": 5, "fitness": 5, "healing": 3}


def _consumable_catalogue() -> list[dict]:
    try:
        return json.loads(CONSUMABLE_FILE.read_text())["consumables"]
    except (OSError, ValueError, KeyError):
        return []

CLUB_ITEM_KINDS = [
    ("kit", 14, 6300000, 4),
    ("badge", 241, 6000000, 4),
    ("stadium", 6, 6200000, 3),
    ("ball", 23, 8120091, 3),
    ("manager", 1, 6100000, 2),
    ("staff", 2, 6150000, 3),
]


def _club_extras() -> list[dict]:
    """Consumables, kits, badges, stadiums, balls and staff."""
    items: list[dict] = []
    next_id = CLUB_ITEM_ID_BASE

    for card in _consumable_catalogue():
        kind = card["itemType"]
        for _ in range(CONSUMABLE_COPIES.get(kind, 1)):
            items.append(
                {
                    "id": next_id,
                    "assetId": card["assetId"],
                    "resourceId": RESOURCE_VERSION | card["assetId"],
                    "definitionId": card["definitionId"],
                    "rating": card["rating"],
                    "itemType": kind,
                    "itemState": "free",
                    # What the title looks the card up by: its name, its
                    # description and what applying it does all come from here.
                    "cardsubtypeid": card["cardsubtypeid"],
                    "discardValue": 0,
                    "lastSalePrice": 0,
                    "timestamp": 1,
                    "untradeable": False,
                    "rareflag": 1 if card["rare"] else 0,
                    "consumableType": kind,
                    "consumableMember": card["member"],
                    "amount": card["amount"],
                    "count": 1,
                }
            )
            next_id += 1

    for kind, asset, resource, count in CLUB_ITEM_KINDS:
        for index in range(count):
            items.append(
                {
                    "id": next_id,
                    "assetId": asset + index,
                    "resourceId": resource + index,
                    "rating": 0,
                    "itemType": kind,
                    "itemState": "free",
                    "discardValue": 0,
                    "lastSalePrice": 0,
                    "timestamp": 1,
                    "untradeable": False,
                    "rareflag": 0,
                }
            )
            next_id += 1
    return items


# -- the game modes --------------------------------------------------------
#
# Seasons, tournaments and Team of the Week. Each of these screens refuses an
# empty list the way fcc_login2 refuses an empty squad, so "none available" is
# not a neutral answer -- it is the error the screen reports.

SEASON_DIVISIONS = [
    (10, "Division 10", 4, 2, 400),
    (9, "Division 9", 4, 2, 500),
    (8, "Division 8", 5, 2, 650),
    (7, "Division 7", 5, 3, 800),
    (6, "Division 6", 6, 3, 1000),
    (5, "Division 5", 6, 3, 1300),
    (4, "Division 4", 7, 4, 1700),
    (3, "Division 3", 7, 4, 2200),
    (2, "Division 2", 8, 4, 3000),
    (1, "Division 1", 10, 5, 5000),
]

# The cups. Every member name below is one CardsDLL carries: they were read
# out of the module's own JSON name table, which is sorted and contiguous
# between `trophiesOffline` and `kitsHome` in `.rdata`. `treeType`, `numTeams`,
# `numRounds`, `matchlength`, `rounds`, `rewardMultiplier`, `awardSet`,
# `awardType`, `halid`, `elgReq`, `eligibilityOperation`, `aigroup`,
# `unlockreq`, `triesMax`, `triesPeriod`, `triesRemaining`, `nextReset`,
# `starttime`, `timeUntilStart`, `timeUntilEnd`, `visStart`, `visEnd`,
# `trophyResourceId` and `trophyUserCount` are all present, as is `knockout`
# for treeType.
#
# `rounds` is an ARRAY of round records. The previous attempt here served it as
# a count -- `"rounds": 5` -- alongside invented `name`/`level`/`entryFee`/
# `active`/`won` members, and opening Competition Joueur Solo froze the title
# outright. That freeze is why this list was emptied and left empty. A number
# where the parser walks an array is the whole explanation, and none of the
# invented members appear in the name table.

TOURNAMENT_ROUNDS = {
    # (round id, difficulty, reward multiplier, coins)
    1: [(1, 1, 1, 150), (2, 1, 1, 200), (3, 2, 1, 300), (4, 2, 1, 500)],
    2: [(1, 2, 1, 250), (2, 2, 1, 350), (3, 3, 1, 500), (4, 3, 2, 900)],
    3: [(1, 3, 1, 400), (2, 3, 1, 600), (3, 4, 2, 900), (4, 4, 2, 1500)],
}

# The trophy ids are the game's own. `cards0.big` carries 70 of them under
# data/ui/external/ion_fut/artassets/fcctournamenttrophies/, named
# trophy_<id>_<tier> for ids 1100..1169, each in bronze, silver, gold and dark,
# beside a notfound.big. Serving `trophyResourceId` 0 is what drew that
# notfound placeholder: the cups listed correctly and had no art.
#
# The art is local. The client still fetches
# /fut/items/images/trophies/xbl2/item.big -- the string
# `items/images/trophies/xbl2/` is built into CardsDLL -- but that pack is not
# where these come from.

TROPHY_FIRST = 1100
TROPHY_LAST = 1169
TROPHY_TIER = "gold"


def empty_big_archive() -> bytes:
    """A structurally valid, empty EA BIGF container.

    Everything under /fut/items/images/ that ends in `.big` is a BIG archive,
    and the server answered the whole /fut/items/ prefix with
    `{"itemData":[]}` -- sixteen bytes of JSON where a binary container was
    asked for. The console asks for two of these on the cup screen: the pack
    itself, and a degenerate `/trophies/xbl2/.big` with no basename when the
    trophy definition carries none.

    Magic, declared size, zero directory entries, header size -- big-endian,
    the same contract this project's own BIG reader uses. Empty on purpose:
    the EA trophy CDN is gone and no art is being invented here. It makes the
    response parseable rather than wrong.
    """
    return b"BIGF" + (16).to_bytes(4, "big") + (0).to_bytes(4, "big") + (
        16
    ).to_bytes(4, "big")


def trophy_item_response(resource_id: int, tier: str = TROPHY_TIER) -> bytes:
    """The definition behind a cup's `trophyResourceId`.

    The journal settles what this endpoint is for. With `trophyResourceId` 0
    the console asked for `/fut/items/xbl2/0.json` once per cup; with 1100,
    1101 and 1102 it asked for those three. So the field drives the request and
    this document is the trophy's definition.

    Answered with the blanket `{"itemData":[]}` the whole prefix gets, the
    definition is empty -- and on entering a cup the console then asked for

        /fut/items/images/trophies/xbl2/.big

    with nothing between the prefix and the extension. It builds that path from
    a member of this document, and an empty list left it empty.

    Which member is not settled. `assetName`, `name` and `image` are the three
    candidates the module's name table carries; all three go out holding the
    same basename, and the next path the console asks for names the winner.
    An unrecognised sibling is skipped, so offering three costs nothing.
    """
    resource_id = int(resource_id)
    basename = f"trophy_{resource_id}_{tier}"
    return json.dumps(
        {
            "itemData": [
                {
                    "id": resource_id,
                    "assetId": resource_id,
                    "resourceId": resource_id,
                    "itemType": "trophy",
                    "assetName": basename,
                    "name": basename,
                    "image": basename,
                }
            ]
        },
        separators=(",", ":"),
    ).encode()

TOURNAMENTS = [
    # (id, teams, match length in minutes, final award, trophy resource)
    (1, 16, 6, 500, 1100),
    (2, 16, 6, 1200, 1101),
    (3, 16, 8, 2500, 1102),
]

# The AI opponents a cup is drawn from. Real EA club ids -- 1 Arsenal,
# 2 Aston Villa, 5 Chelsea, 7 Everton, 9 Liverpool, 10 Manchester City,
# 11 Manchester United, 13 Newcastle, 18 Tottenham, 21 West Ham -- and the
# draw is one short of numTeams because the club itself takes the last slot.
TOURNAMENT_TEAM_POOL = [
    241, 243, 21, 69, 10, 11, 9, 22, 5, 73, 1, 13, 18, 2, 7, 8,
]


def _season_matches(division: int, count: int) -> list[dict]:
    """A division's fixture list.

    The same shape as a cup's `rounds`: the schedule is an array of records,
    not a count. `roundId` is zero-based here -- the wire `round` in
    season/user is one-based and the client decrements it.
    """
    base = max(1, min(5, 1 + (10 - int(division)) // 2))
    return [
        {
            "teamId": TOURNAMENT_TEAM_POOL[index % len(TOURNAMENT_TEAM_POOL)],
            "difficulty": min(5, base + (1 if index >= 7 else 0)),
            "rewardMult": 1,
            "roundId": index,
            "coins": 250 + (10 - int(division)) * 25 + index * 10,
        }
        for index in range(max(0, int(count)))
    ]


def _season_prize(level: str, threshold: int, coins: int = 0) -> dict:
    awards = (
        []
        if int(coins) <= 0
        else [
            {
                "type": "coin",
                "value": int(coins),
                "assetId": 0,
                "count": 1,
                "halId": 0,
                "teamId": 0,
            }
        ]
    )
    return {
        "prizeLevel": level,
        "thresholdPoint": int(threshold),
        "awardMappings": [{"awards": awards}],
    }


def _season_record(index: int, division: int, matches: int, promote: int, coins: int) -> dict:
    title = 12 if int(division) == 10 else min(30, int(promote) + 3)
    holding = 300 if int(division) == 10 else max(300, int(coins) // 5)
    promotion = 1500 if int(division) == 10 else max(500, int(coins) - 400)
    return {
        "id": int(index),
        "type": "OFFLINE",
        "divisionId": int(division),
        "numMatches": int(matches),
        "matchLengthMin": 6,
        "matches": _season_matches(division, matches),
        "prizeSet": [
            _season_prize("RELEGATION", 0, 0),
            _season_prize("MAINTENANCE", 0, holding),
            _season_prize("PROMOTION", int(promote), promotion),
            _season_prize("CHAMPIONSHIP", title, int(coins)),
        ],
        "elgOperation": "AND",
        "elgReq": [],
        # -1, not 0. Zero is a real resource id as far as the client is
        # concerned: with it the cup screen went and fetched
        # /fut/items/xbl2/0.json once per entry. -1 is the "no trophy" value.
        "trophyResourceId": -1,
        "trophyUseCount": 0,
        "visStartDays": 3650,
        "visEndDays": 3650,
        "startDateTime": 0,
        "endDateTime": FOREVER,
        "untilStartSeconds": 0,
        "untilEndSeconds": 315360000,
    }


def season_wire_mode() -> str:
    """`empty` unless FIFA14_SEASON_MODE asks for the native shape.

    Three shapes have been served here and all three failed on the console:

    * invented members (`division`, `matchesPlayed`, `coinsPerWin`, ...), none
      of which is in CardsDLL's name table -- the screen read its constructor
      defaults;
    * those names corrected but the thresholds and rewards flat at the top
      level, reusing the *cup's* time names -- "Les saisons ne sont pas
      disponibles pour le moment";
    * the full native shape below, with `matches` and `prizeSet` as arrays --
      the FUT loader froze on entering the mode.

    Empty is the only answer known not to break anything, and it is what the
    PC revival carried here for the same reason. The native shape stays
    reachable for a deliberate test rather than being the default that greets
    anyone who opens the mode.
    """
    raw = os.environ.get("FIFA14_SEASON_MODE", "empty").strip().lower()
    return "native" if raw in {"native", "full", "on"} else "empty"


def seasons_response() -> bytes:
    """The divisions.

    A season carries its fixture list in `matches` and its rewards in
    `prizeSet`, both arrays of records -- the same fault as a cup's `rounds`
    served as a count, one level deeper. Every member of the native record was
    checked against the module's name table, and the screen still froze, so
    something below is still wrong and the freeze is not worth serving by
    default. See `season_wire_mode`.
    """
    if season_wire_mode() == "empty":
        return json.dumps({"seasons": []}, separators=(",", ":")).encode()
    return json.dumps(
        {
            "seasons": [
                _season_record(index, division, matches, promote, coins)
                for index, (division, _name, matches, promote, coins) in enumerate(
                    SEASON_DIVISIONS, start=1
                )
            ]
        },
        separators=(",", ":"),
    ).encode()


def season_user_response(division: int = 10, played: int = 0) -> bytes:
    """Where the club currently stands, and nothing more.

    The parser handles `seasonId`, `divisionId` and `round`. `seasonId` is
    decremented by the client, so 1 selects the first record in the list above.
    `round` is decremented too: wire 1 is the first scheduled match, and wire 0
    would become the client's 0xFFFF invalid sentinel.

    Everything else that used to go out here -- points, won, draw, the record,
    the end result -- was guessed, and guessed members are what this document
    is now deliberately without.
    """
    if season_wire_mode() == "empty":
        # What this route carried before any of the guessed shapes: an empty
        # object leaves the native response at its constructor defaults rather
        # than naming a season the list no longer offers.
        return b"{}"
    index = next(
        (
            position
            for position, row in enumerate(SEASON_DIVISIONS, start=1)
            if row[0] == division
        ),
        1,
    )
    return json.dumps(
        {
            "seasonId": index,
            "divisionId": int(division),
            "round": max(0, int(played)) + 1,
        },
        separators=(",", ":"),
    ).encode()


FOREVER = 2147483647


def tournament_entry(identifier: int) -> dict:
    """One cup, in the shape the native parser reads."""
    teams, match_length, award, trophy = next(
        (row[1:] for row in TOURNAMENTS if row[0] == identifier),
        TOURNAMENTS[0][1:],
    )
    rounds = TOURNAMENT_ROUNDS[identifier]
    return {
        "id": identifier,
        "type": "offline",
        "treeType": "knockout",
        "aigroup": 0,
        "eligibilityOperation": "AND",
        "elgReq": [],
        "numTeams": teams,
        "numRounds": len(rounds),
        "matchlength": match_length,
        "rounds": [
            {
                "id": round_id,
                "difficulty": difficulty,
                "rewardMultiplier": multiplier,
                "coins": coins,
            }
            for round_id, difficulty, multiplier, coins in rounds
        ],
        "awardSet": {"awards": [{"awardType": 1, "value": award, "halid": 0}]},
        "lock": "UNLOCKED",
        "unlockreq": 0,
        # No entry limit: triesMax 0 is what an always-playable offline cup
        # carries, and a nonzero triesRemaining against triesMax 0 is what
        # makes the screen show "0 essais restants" and refuse entry.
        "triesMax": 0,
        "triesPeriod": 0,
        "triesRemaining": 0,
        "nextReset": 0,
        "starttime": 0,
        "endtime": FOREVER,
        "timeUntilStart": 0,
        "timeUntilEnd": 315360000,
        "visStart": 3650,
        "visEnd": 3650,
        "trophyResourceId": trophy,
        "trophyUserCount": 0,
    }


def tournaments_response() -> bytes:
    return json.dumps(
        {"tournament": [tournament_entry(row[0]) for row in TOURNAMENTS]},
        separators=(",", ":"),
    ).encode()


def tournament_teams_response(count: int = 15, group: int = 0) -> bytes:
    """The draw. `teamId` is the only member this document carries.

    The query is `/teams?groupId=%d&count=%d` in the module's own template, so
    the group is part of the request even though every cup here declares
    `aigroup` 0. Rotating the pool by it keeps two groups from drawing the same
    side in the same order, without inventing a second pool.
    """
    count = max(0, min(int(count), len(TOURNAMENT_TEAM_POOL)))
    size = len(TOURNAMENT_TEAM_POOL)
    offset = (int(group) % size) if size else 0
    rotated = TOURNAMENT_TEAM_POOL[offset:] + TOURNAMENT_TEAM_POOL[:offset]
    return json.dumps({"teamId": rotated[:count]}, separators=(",", ":")).encode()


class TournamentProgress:
    """Where the club stands in each cup, kept across launches.

    The client serialises its own progress and `.rdata` carries the format
    string it builds the body from:

        {"round":%d,"dataVersion":%d,"tournamentData":"

    It sits among the cup constants -- `TOO_MANY_TOURNAMENTS`, `JOINED`,
    `LOCKED_TROPHIES` -- which is what identifies it as the tournament one.
    There is a near-identical string spelling the blob `data` instead, but it
    is followed immediately by `%d/division/%d` and belongs to seasons; reading
    that one as the cup's format was a misidentification. The shared tail is
    `","progressDataVersion":%d,"progressData":"`.

    `data` is still accepted on the way in, and the reply also spells the
    progress blob `progressdata`, which is how the name table carries it beside
    the camel-cased `progressDataVersion`. An unrecognised sibling at the top
    level is skipped, as everywhere else in this protocol.
    """

    def __init__(self) -> None:
        self.entries: dict[int, dict] = {}

    def apply(self, identifier: int, document: dict) -> dict:
        identifier = int(identifier)
        if not isinstance(document, dict):
            document = {}
        current = self.entries.get(identifier, {})

        def pick(*names, default=0):
            for name in names:
                if name in document:
                    return document[name]
            return current.get(names[0], default)

        entry = {
            "round": int(pick("round", default=1) or 1),
            "dataVersion": int(pick("dataVersion", default=1) or 1),
            "tournamentData": pick("tournamentData", "data", default="") or "",
            "progressDataVersion": int(
                pick("progressDataVersion", default=1) or 1
            ),
            "progressData": pick("progressData", "progressdata", default="") or "",
        }
        self.entries[identifier] = entry
        return entry

    def response(self, identifier: int) -> bytes:
        """A cup with no saved progress answers with its id and nothing else.

        That is what the client sends up first, and inventing a round or an
        empty data blob for a cup that was never entered would put the screen
        into a tournament that does not exist.
        """
        identifier = int(identifier)
        entry = self.entries.get(identifier)
        if entry is None:
            return json.dumps(
                {"tournamentId": identifier}, separators=(",", ":")
            ).encode()
        return json.dumps(
            {
                "tournamentId": identifier,
                "round": entry["round"],
                "dataVersion": entry["dataVersion"],
                "tournamentData": entry["tournamentData"],
                "progressDataVersion": entry["progressDataVersion"],
                "progressData": entry["progressData"],
                "progressdata": entry["progressData"],
            },
            separators=(",", ":"),
        ).encode()

    def delete(self, identifier: int) -> bool:
        return self.entries.pop(int(identifier), None) is not None

    def active_ids(self) -> list[int]:
        return sorted(self.entries)

    def state(self) -> dict:
        return {str(key): value for key, value in self.entries.items()}

    def restore(self, saved: dict | None) -> None:
        for key, value in (saved or {}).items():
            if isinstance(value, dict):
                self.apply(int(key), value)


TOURNAMENT_PROGRESS = TournamentProgress()


def active_tournaments_response() -> bytes:
    """Only the cups actually entered.

    This used to name every cup in the catalogue, which told the screen the
    club was mid-run in all of them while no progress existed for any.
    """
    return json.dumps(
        {"tournamentId": TOURNAMENT_PROGRESS.active_ids()}, separators=(",", ":")
    ).encode()


TOTW_FILE = Path(__file__).resolve().parent / "fifa14_totw.json"


def _totw_asset_ids() -> list[int]:
    """The real Team of the Week, if it was fetched.

    wefut publishes one at /squad/1, titled "TOTW 1". The squads after it are
    not the following weeks -- that path is a public gallery of user-built
    sides -- so only pages that name themselves TOTW are kept, and the rest of
    the screen falls back to the best rare cards in the catalogue.
    """
    if not TOTW_FILE.exists():
        return []
    squads = json.loads(TOTW_FILE.read_text()).get("squads", [])
    return list(squads[0]["assetIds"]) if squads else []


def totw_response(catalogue: "CardCatalogue", size: int = 23) -> bytes:
    """Team of the Week."""
    by_asset = {card["assetId"]: card for card in catalogue.cards}
    best = [
        by_asset[asset] for asset in _totw_asset_ids() if asset in by_asset
    ]
    if len(best) < size:
        # Fill the bench from the catalogue's best rares, so the squad screen
        # gets a full side rather than a partial one.
        seen = {card["assetId"] for card in best}
        best += [
            card
            for card in catalogue.cards
            if card.get("rareflag")
            and card.get("rating", 0) >= 80
            and card["assetId"] not in seen
        ][: size - len(best)]
    best = best[:size]
    items = []
    for index, card in enumerate(best):
        items.append(
            _player_item(
                item_id=1_850_000_000 + index,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 1),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                item_state="free",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
        )
    # The screen wants a challenge, not a squad: CardsDLL names
    # RequestChallengeData, GetChallengeData, GetTotalChallenges and
    # SetSelectedChallengeInfo, and its JSON table carries `squadChallenge`
    # at 0x8902FFD8 beside `squadId`. So a challenge is a squad you play
    # against, and the document lists them.
    #
    # The member names below are the ones the binary actually carries;
    # the arrangement around them is still inferred, which is why this is
    # kept small -- an invented shape froze the title twice tonight.
    saved_squads = (
        json.loads(TOTW_FILE.read_text()).get("squads", [])
        if TOTW_FILE.exists()
        else []
    )

    def challenge(index: int, squad: dict) -> dict:
        """One side to play against, rated from the cards it actually holds.

        `opponentRating` was computed as `max(... for card in [])` -- over an
        empty list, so every challenge advertised a rating of 0 and an
        opponent of team 0. A side you are invited to beat has to say how
        strong it is.
        """
        cards = [
            by_asset[asset]
            for asset in (squad.get("assetIds") or [])
            if asset in by_asset
        ] or best
        eleven = sorted(
            cards, key=lambda card: -card.get("rating", 0)
        )[:11]
        rating = (
            round(sum(card.get("rating", 0) for card in eleven) / len(eleven))
            if eleven
            else 0
        )
        # The club most of them play for, which is what an opponent team id
        # means here. Zero is "no team" and drew nothing.
        clubs = [card.get("clubId") for card in eleven if card.get("clubId")]
        team = max(set(clubs), key=clubs.count) if clubs else 0
        return {
            "squadId": index + 1,
            "squadName": squad.get("name", f"TOTW {index + 1}"),
            "formation": FORMATION,
            "opponentTeam": int(team),
            "opponentRating": rating,
        }

    challenges = [challenge(i, s) for i, s in enumerate(saved_squads)]
    return json.dumps(
        {
            "itemData": items,
            "formation": FORMATION,
            "squadName": "Équipe de la semaine",
            "squadChallenge": challenges,
        },
        separators=(",", ":"),
    ).encode()


def hub_response(inventory: "ClubInventory", listings: int) -> bytes:
    """The My Club and transfer tiles read their counts from here.

    Both were fixed numbers -- 92 players and no auctions -- so the club tile
    never moved as cards arrived and the market tile always read zero.
    """
    players = sum(
        1 for item in inventory.items if item.get("itemType") == "player"
    )
    return json.dumps(
        {"auctionCount": listings, "clubPlayers": players},
        separators=(",", ":"),
    ).encode()


# The member names CardsDLL's JSON table carries for consumables, between
# 0x89030F9C and 0x89031148. The response is an object with these members, not
# an entries array -- numbered keys meant nothing to it, which is why the apply
# screen reported none available while the club held sixteen.
#
# Each card names the one it belongs to -- tools/build_consumables.py takes it
# from the subtype block the card sits in, which is how the database and these
# member names line up: 201 against 202 for a player's contract and a
# manager's, 219 against 220 for a player's fitness and the team's.
CONSUMABLE_MEMBERS = {
    "contract": "consumablesContractPlayer",
    "fitness": "consumablesFitnessPlayer",
    "healing": "consumablesHealing",
    "training": "consumablesTrainingPlayer",
    "position": "consumablesPosition",
    "playStyle": "consumablesTrainingPlayerPlayStyle",
}

# The rest of the members. Every one is a real distinction the game makes --
# a keeper's training card is not an outfielder's -- and the club now holds
# cards for each, so the counts are counted rather than shared out.
#
# They used to go out at zero, and that is what "Pas d'élément disponible" was
# reporting when applying from the squad screen: that screen decides from these
# counts alone, and picking a goalkeeper makes it read consumablesTrainingGk.
# The manager's own cards are in the database -- subtypes 250 to 273 and 300
# to 340 -- but nothing in it says which member each block belongs to, and
# guessing would put the wrong card under the wrong name. So these members
# report their family's count, which is what they did before any of this and
# what stopped the popup: a count that is too generous costs nothing, a zero
# refuses to apply anything.
CONSUMABLE_FALLBACKS = {
    "consumablesTrainingManager": "training",
    "consumablesTrainingManagerLeagueModifier": "training",
    "consumablesFormationManager": "position",
}

# The three the game also asks for in aggregate. Each is its family's count
# rather than the sum of the members under it: the members that fall back
# would be counted twice, and a club of 242 consumables would report 300.
CONSUMABLE_TOTALS = {
    "consumablesContract": "contract",
    "consumablesFitness": "fitness",
    "consumablesTraining": "training",
}


def consumable_stats_response(inventory: "ClubInventory") -> bytes:
    """How many of each consumable the club holds, under the real member names."""
    held: dict[str, int] = {member: 0 for member in CONSUMABLE_MEMBERS.values()}
    by_kind: dict[str, int] = {}

    total = 0
    for item in inventory.items:
        kind = item.get("consumableType") or item.get("itemType")
        if kind not in CONSUMABLE_TYPES:
            continue
        count = int(item.get("count") or 1)
        total += count
        by_kind[kind] = by_kind.get(kind, 0) + count
        member = item.get("consumableMember")
        if member:
            held[member] = held.get(member, 0) + count

    document = dict(held)
    for member, kind in CONSUMABLE_FALLBACKS.items():
        document[member] = by_kind.get(kind, 0)
    for name, kind in CONSUMABLE_TOTALS.items():
        document[name] = by_kind.get(kind, 0)
    document["consumables"] = total
    return json.dumps(document, separators=(",", ":")).encode()


def club_stats_response(inventory: "ClubInventory") -> bytes:
    """The Mon Club counters: players, rares, staff, stadiums, kits, badges.

    They all read zero because the stats endpoints answered with an empty
    entries list -- a club full of cards reporting nothing owned.

    The key numbers are the screen's own slots; they are recovered from what
    the screen displays rather than from a document, so the mapping is a
    reading and not a certainty. The counts themselves are exact.
    """
    def count(kind: str) -> int:
        return sum(1 for item in inventory.items if item.get("itemType") == kind)

    players = count("player")
    rares = sum(
        1
        for item in inventory.items
        if item.get("itemType") == "player" and item.get("rareflag")
    )
    entries = [
        {"key": 0, "value": players},
        {"key": 1, "value": rares},
        {"key": 2, "value": count("staff") + count("manager")},
        {"key": 3, "value": count("stadium")},
        {"key": 4, "value": count("kit")},
        {"key": 5, "value": count("badge")},
        {"key": 6, "value": count("ball")},
        {"key": 7, "value": 0},
    ]
    return json.dumps({"entries": entries}, separators=(",", ":")).encode()





def consumables_response(inventory: "ClubInventory") -> bytes:
    """The Consommables screen asks here, by category.

    It was a 404, which is why the tab looked empty however many consumables
    the club held.
    """
    items = [
        item
        for item in inventory.items
        if item.get("itemType") in CONSUMABLE_TYPES
        or item.get("consumableType") in CONSUMABLE_TYPES
    ]
    return json.dumps({"itemData": items}, separators=(",", ":")).encode()


def totw_index() -> bytes:
    """The list of Team of the Week squads available to view.

    The screen asks for the TOTW itself and then for this list, and a 404 here
    is what it reports as "aucune Équipe de la semaine disponible" -- the squad
    had already been served successfully.
    """
    squads = []
    if TOTW_FILE.exists():
        squads = json.loads(TOTW_FILE.read_text()).get("squads", [])
    return json.dumps(
        {
            "squad": [
                {
                    "id": index + 1,
                    "squadName": squad.get("name", f"TOTW {index + 1}"),
                    "formation": FORMATION,
                    "rating": 0,
                    "chemistry": 100,
                }
                for index, squad in enumerate(squads)
            ],
            "userInfo": [],
        },
        separators=(",", ":"),
    ).encode()


# -- keeping the club between sessions -------------------------------------
#
# Everything above is rebuilt from the icebreaker packs at every server start,
# so a card sent to the club survived exactly as long as the process did: the
# club counter went back to 92 on the next relaunch and the pack you opened was
# gone. Entering FUT needs a relaunch, so that is every single session.

SAVE_FILE = Path(__file__).resolve().parent.parent / "runtime" / "club-save.json"


class ClubSave:
    """The club's own state, written to disk and reloaded."""

    def __init__(self, path: Path = SAVE_FILE) -> None:
        self.path = path

    def load(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions", tasks: "ManagerTasks | None" = None) -> bool:
        if not self.path.exists():
            return False
        try:
            saved = json.loads(self.path.read_text())
        except (ValueError, OSError):
            return False
        wallet.coins = int(saved.get("coins", wallet.coins))
        known = {item["id"] for item in inventory.items}
        for item in saved.get("acquired", []):
            if item["id"] not in known:
                inventory.items.append(item)
                actions.club.append(item)
        for item in saved.get("sold", []):
            inventory.items = [
                held for held in inventory.items if held["id"] != item
            ]
        known = {item["id"] for item in actions.shop.pending}
        for item in saved.get("pending", []):
            if item["id"] not in known:
                actions.shop.pending.append(item)
        if tasks is not None:
            for key, value in (saved.get("tasks") or {}).items():
                tasks.complete(int(key), int(value))
        for key, value in (saved.get("squads") or {}).items():
            inventory._squads()[int(key)] = value
        if saved.get("activeSquad"):
            inventory.set_active(int(saved["activeSquad"]))
        if saved.get("squad"):
            inventory.set_squad([int(x) for x in saved["squad"]])
        actions.transfer = list(saved.get("transfer", []))
        actions.listings = {
            int(key): value for key, value in saved.get("listings", {}).items()
        }
        TOURNAMENT_PROGRESS.restore(saved.get("tournaments"))
        CLUB_IDENTITY.restore(saved.get("club"))
        return True

    def save(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions", tasks: "ManagerTasks | None" = None) -> None:
        starting = ClubInventory()
        original = {item["id"] for item in starting.items}
        current = {item["id"] for item in inventory.items}
        document = {
            "coins": wallet.coins,
            # Only what differs from the starting club, so the save stays small
            # and a change to the icebreaker packs still flows through.
            "acquired": [
                item for item in inventory.items if item["id"] not in original
            ],
            "sold": sorted(original - current),
            "pending": actions.shop.pending,
            "squad": [item["id"] for item in inventory.squad],
            "tasks": {str(k): v for k, v in tasks.completed.items()} if tasks else {},
            "activeSquad": inventory.active_squad_id(),
            "squads": {
                str(key): value for key, value in inventory._squads().items()
            },
            "transfer": actions.transfer,
            "listings": {str(key): value for key, value in actions.listings.items()},
            "tournaments": TOURNAMENT_PROGRESS.state(),
            "club": CLUB_IDENTITY.state(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document, separators=(",", ":")))


def totw_index_with_squad(catalogue: "CardCatalogue") -> bytes:
    """The Team of the Week list, carrying the squad itself as well.

    The screen asks clientdata/totw and then this, and rejected the plain list
    it was given -- the squad had already been served successfully, so what it
    refuses is this document. Which member it reads is not known, so the squad
    summary, the cards and an empty user list all go out together; an
    unrecognised sibling at the top level is skipped.
    """
    index = json.loads(totw_index())
    squad = json.loads(totw_response(catalogue))
    index["itemData"] = squad["itemData"]
    index["formation"] = squad["formation"]
    return json.dumps(index, separators=(",", ":")).encode()


# -- manager tasks ---------------------------------------------------------
#
# The thirteen tasks on the club hub. They were answered with a fixed empty
# entries list, so nothing you completed was ever recorded: the progress bar
# stayed at 0/13 and every task reset on the next launch.

class ManagerTasks:
    """What the manager has done, kept and reloaded."""

    def __init__(self) -> None:
        self.completed: dict[int, int] = {}

    def complete(self, task: int, value: int = 1) -> None:
        self.completed[int(task)] = int(value)

    def apply(self, document: dict) -> int:
        """Take whatever the client reports and record it.

        The body arrives as an entries array of key/value pairs, the same shape
        it is served in, so a task marked done comes back as its own key.
        """
        entries = document.get("entries") if isinstance(document, dict) else None
        count = 0
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            try:
                self.complete(int(entry.get("key")), int(entry.get("value") or 1))
                count += 1
            except (TypeError, ValueError):
                continue
        return count

    def response(self) -> bytes:
        return json.dumps(
            {
                "entries": [
                    {"key": key, "value": value}
                    for key, value in sorted(self.completed.items())
                ]
            },
            separators=(",", ":"),
        ).encode()
