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

import base64
import json
import os
import random
import time

# How many cards go out when the club is asked for with no count of its own.
#
# The target is 77 KB, the largest club response this console was measured
# surviving -- eighteen times across three sessions.
#
# 90 was calibrated on a response of nothing but players, at about 860 bytes a
# card. The bare response is a mix now -- see `_bounded_club`, which stopped it
# being ninety players and no consumables -- and a consumable costs about 330
# bytes, so the same 77 KB holds 130 mixed cards where it held 90. Measured,
# not scaled: 90 came out at 48.4 KB and 130 at 70.5 KB.
#
# 126 rather than 130 since the response began carrying the club's duplicate
# pairs: 130 cards plus 46 pairs measured 76.2 KB against a ceiling of 77, and
# the pairs grow with the club exactly as the cards do. 126 puts it back at
# 73.7 KB, the headroom the 130 was chosen with. Four cards off a view that is
# already truncated, and which the screen filters rather than scrolls.
#
# FIFA14_CLUB_LIMIT raises or lowers it; 0 restores the unbounded behaviour,
# which is what served 244 KB immediately before a FUT teardown.
try:
    CLUB_UNFILTERED_LIMIT = int(os.environ.get("FIFA14_CLUB_LIMIT", "126"))
except ValueError:
    CLUB_UNFILTERED_LIMIT = 126
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


class Persona:
    """Who this club belongs to, in one place.

    FUT tells a client who it is through four channels and they have to carry
    the same persona: the /user body, /eaid/personas, and the
    EASW-Nucleus-Persona and EASW-Userid headers. The squad documents carry it
    too.

    Correcting one of them and not the others is worse than leaving them all
    wrong, and that is exactly what happened on 12 August: /user was moved off
    a flat 0 onto the console's real nucleus id while every squad document
    still said 0, and the squad screen came back with eleven blank cards. The
    data was untouched -- the server still held all 23 -- but the client will
    not show a squad that belongs to somebody else.

    So there is one value, set once when the identity is known, and every
    document reads it. Agreement by construction rather than by remembering.
    """

    def __init__(self) -> None:
        self.id: int = 0

    def adopt(self, persona_id: int | None) -> None:
        if persona_id:
            self.id = int(persona_id)


PERSONA = Persona()


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


def card_signature(item: dict):
    """What makes two cards the same card.

    The asset, the version and the rating. `resourceId` was the whole key, on
    the reasoning that a player's specials each carry their own -- and that is
    true of retail data and false of ours. This server builds it as
    `RESOURCE_VERSION | asset_id` with the version byte always 1, so every
    version of a player resolves to the same number and the key collapsed onto
    the asset. Which is the exact mistake DUPLICATES.md warns about, reached
    from the other end.

    What it looked like: a Team of the Year 98 reported as a repeat of a Team
    of the Year 92, a Team of the Week 74 as a repeat of a Rare Silver 73, a
    Team of the Season 84 as a repeat of a Rare Gold 79. Five such pairs in a
    club of 213. Ibra 96 and Lahm 92 were two of them.

    `rarity` is what names the version here -- "Rare Gold", "Team of the Week",
    "iMOTM" -- and the rating separates two cards of one player inside a single
    family. Two genuinely identical cards agree on all three.
    """
    return (
        item.get("assetId"),
        (item.get("rarity") or "").strip().lower(),
        item.get("rating"),
    )


def club_duplicate_pairs(items: list[dict]) -> list[dict]:
    """Which of these players repeat one the club already had.

    A pack marks its own repeats before you accept them, and that marking was
    the end of it: once the card was in the club nothing said so any more. The
    club screen is where a player actually goes looking for repeats to sell,
    and it showed a club full of ordinary cards.

    The card kept as the original is the one with the smallest id, which is the
    one owned longest -- acquired cards are numbered upwards as they arrive.
    Anything else would move the marker from one copy to the other as the club
    is re-sorted, and offer to sell a different card each time the screen is
    opened.

    Marks are rewritten rather than added to, so a card whose twin has since
    been sold stops claiming to repeat a card that is not there.

    Only players. A second contract card is not a repeat of the first, it is a
    second contract -- consumables are meant to accumulate.
    """
    players = sorted(
        (item for item in items if item.get("itemType") == "player"),
        key=lambda item: item.get("id") or 0,
    )
    first: dict[tuple, int] = {}
    pairs: list[dict] = []
    for item in players:
        key = card_signature(item)
        original = first.get(key)
        if original is None:
            first[key] = item["id"]
            item.pop("duplicateItemId", None)
            continue
        item["duplicateItemId"] = original
        pairs.append({"itemId": item["id"], "duplicateItemId": original})
    return pairs


def pile_duplicate_pairs(pending: list[dict], owned: list[dict]) -> list[dict]:
    """Which cards waiting in the purchased pile repeat one already owned.

    The pack screen gets its pairs in the pack response and shows the repeat
    there. The unassigned pile is a different screen with a duplicates tab of
    its own, and it was handed an empty list -- so a card that the pack itself
    had just flagged sat in that tab's absence, unremarked. Two Vargas out of
    one pack on 12 August: marked on the card, missing from the panel.

    The card kept as the original is whichever was acquired first -- a copy in
    the club always beats one still in the pile, and inside the pile the
    smaller id wins, because ids are issued upwards as cards arrive.
    """
    # A bought card is put in the pile *and* in the club -- the pile alone lost
    # it, so both hold the same card under the same id. Pairing across the two
    # lists therefore has to ignore a card meeting itself, or the screen is
    # told to compare a card against itself. That is the one shape
    # DUPLICATES.md says must never go out: it froze the title outright when a
    # pack sent a bare list of its own new ids.
    #
    # Pelé, bought on 12 August, came back paired 1800000049 -> 1800000049.
    waiting = {item.get("id") for item in pending}
    first: dict[tuple, int] = {}
    for item in sorted(owned, key=lambda row: row.get("id") or 0):
        if item.get("itemType") != "player" or item.get("id") in waiting:
            continue
        first.setdefault(card_signature(item), item["id"])
    pairs: list[dict] = []
    for item in sorted(pending, key=lambda row: row.get("id") or 0):
        if item.get("itemType") != "player":
            continue
        key = card_signature(item)
        original = first.get(key)
        if original is None or original == item.get("id"):
            first.setdefault(key, item["id"])
            item.pop("duplicateItemId", None)
            continue
        item["duplicateItemId"] = original
        pairs.append({"itemId": item["id"], "duplicateItemId": original})
    return pairs


def _bounded_club(items: list[dict]) -> list[dict]:
    """The bare club, capped, without wiping out a whole kind of card.

    Slicing the sorted list took the first `CLUB_UNFILTERED_LIMIT` cards, and
    the sort puts players first -- so the bare response was **ninety players
    and nothing else**, every consumable, kit, badge and staff card cut off.

    That is what "Pas d'élément disponible" was. Applying a consumable to a
    player reads the club the client already holds; the club it held had no
    consumable in it, so the picker had nothing to offer and never asked the
    server for more. The counts said 35 contracts and the list said none.

    Half the budget goes to players, best rated first. The rest is dealt round
    by round across every other kind the club holds, so each appears, and any
    share a kind cannot fill goes back to the others. A mixed ninety is
    smaller than ninety players, so the byte size stays under what the console
    was measured surviving.
    """
    if len(items) <= CLUB_UNFILTERED_LIMIT:
        return items

    players = [item for item in items if item.get("itemType") == "player"]
    others: dict[str, list[dict]] = {}
    for item in items:
        if item.get("itemType") == "player":
            continue
        others.setdefault(item.get("itemType") or "", []).append(item)

    share = CLUB_UNFILTERED_LIMIT // 2
    kept = players[:share]
    budget = CLUB_UNFILTERED_LIMIT - len(kept)

    queues = [list(group) for group in others.values()]
    while budget > 0 and any(queues):
        for queue in queues:
            if not queue or budget <= 0:
                continue
            kept.append(queue.pop(0))
            budget -= 1
    # Whatever the other kinds could not fill goes back to the players.
    if budget > 0:
        kept.extend(players[share:share + budget])

    # `items` was sorted before the cap and the client reads it in order.
    order = {id(item): index for index, item in enumerate(items)}
    kept.sort(key=lambda item: order[id(item)])
    return kept


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
                        if item.get("itemType") not in CONSUMABLE_TYPES:
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
            items = _bounded_club(items)
        # Worked out over the whole club, reported for what is on this page:
        # the card a repeat points at is often not in the same search result,
        # and picking the original from the page alone would name a different
        # card on every filter.
        served = {item.get("id") for item in items}
        pairs = [
            pair
            for pair in club_duplicate_pairs(self.items)
            if pair["itemId"] in served
        ]
        return json.dumps(
            {"itemData": items, "duplicateItemIdList": pairs},
            separators=(",", ":"),
        ).encode()

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
                "personaId": PERSONA.id,
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
                "personaId": PERSONA.id,
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


# What a card is worth, anchored rather than computed from its rating.
#
# The old formula was `(rating - 40) ** 2 * 3`, doubled for a rare. That makes
# a 99 worth 10,443 coins and an 89 worth 7,203 -- the whole market fitted
# inside a starting balance, so nothing in it was worth saving for. A card's
# value is not a smooth function of its rating: it doubles somewhere around 84
# and again around 87, and the top of the game is two orders of magnitude
# above the middle of it.
#
# These are FIFA 14-era figures, not modern FUT ones. The same table the PC
# revival uses, which is where an 89 Iniesta listing at 359,000 comes from.
MARKET_ANCHORS = {
    64: 400, 65: 500, 70: 900, 74: 1500, 75: 1600, 76: 2200, 77: 3000,
    78: 4500, 79: 6500, 80: 9000, 81: 13000, 82: 20000, 83: 30000,
    84: 45000, 85: 70000, 86: 110000, 87: 175000, 88: 275000, 89: 425000,
    90: 650000, 91: 850000, 92: 1000000, 93: 1250000, 94: 1500000,
    95: 1800000, 96: 2200000, 97: 2700000, 98: 3300000, 99: 4000000,
}

# A special is worth more than the ordinary card of the same rating, and how
# much more depends on which special it is.
MARKET_SPECIAL_MULTIPLIER = {
    "team of the week": 1.35,
    "team of the season": 1.55,
    "world cup": 1.30,
    "motm": 1.60,
    "imotm": 1.70,
    "team of the year": 2.25,
    "record breaker": 1.80,
    "legend": 2.00,
}

# A non-rare goes for less than the rare version of the same rating.
MARKET_NON_RARE_MULTIPLIER = 0.82


def _round_price(value: float) -> int:
    """To a step the market would actually quote, never below the floor."""
    price = max(150, int(round(value)))
    if price < 1000:
        step = 50
    elif price < 10000:
        step = 100
    elif price < 50000:
        step = 250
    elif price < 100000:
        step = 500
    else:
        step = 1000
    return max(150, int(round(price / step) * step))


# How many sellers are asking for the same card, and how far apart. Every
# listing on the market used to be the only one of its card and every one of
# them was priced identically, so the price-comparison screen compared one
# number with itself.
MARKET_SPREADS = {
    3: (-0.040, 0.000, 0.045),
    4: (-0.050, -0.015, 0.025, 0.065),
    5: (-0.060, -0.030, 0.000, 0.032, 0.070),
    6: (-0.065, -0.040, -0.015, 0.015, 0.045, 0.080),
    7: (-0.070, -0.045, -0.020, 0.000, 0.025, 0.055, 0.090),
}

# An hour to a day. A market where every listing expires at the same moment is
# a market nobody has to decide anything about.
MARKET_DURATIONS = (3600, 10800, 21600, 43200, 86400)

# Somebody has to be selling it.
MARKET_SELLERS = (
    "LegacyFC", "UltimateXI", "TradeKing", "OldSchoolUT",
    "MarketFC", "FootyClub", "RareGoldFC", "FUT",
)


def _market_key(card: dict) -> int:
    return int(card.get("resourceId") or card.get("assetId") or 0)


def _market_copies(card: dict) -> int:
    """How many sellers are asking for this card. Stable per card."""
    return 3 + (_market_key(card) % (max(MARKET_SPREADS) - 2))


def _market_listing_price(card: dict, index: int, count: int) -> int:
    """One seller's asking price, spread around the card's value."""
    value = _price_for(card.get("rating", 0), card.get("rareflag", 0), card)
    spread = MARKET_SPREADS.get(count)
    if not spread:
        return value
    return _round_price(value * (1.0 + spread[min(index, count - 1)]))


def _market_duration(card: dict, index: int) -> int:
    return MARKET_DURATIONS[(_market_key(card) + index * 3) % len(MARKET_DURATIONS)]


def _market_seller(card: dict, index: int) -> str:
    return MARKET_SELLERS[(_market_key(card) + index) % len(MARKET_SELLERS)]


def _price_for(rating: int, rareflag: int, card: dict | None = None) -> int:
    """What one card is worth on the market.

    The jitter is derived from the card's own resource id, not from a random
    number: the same card has to be worth the same thing on the next search,
    or a price that moved between two pages reads as a bug.
    """
    rating = max(1, min(99, int(rating)))
    if rating <= 40:
        base = 150.0
    elif rating < 64:
        base = 150.0 + (rating - 40) * 10
    else:
        base = float(MARKET_ANCHORS[max(k for k in MARKET_ANCHORS if k <= rating)])

    card = card or {}
    rarity = (card.get("rarity") or "").strip().lower()
    if rarity in MARKET_SPECIAL_MULTIPLIER:
        base *= MARKET_SPECIAL_MULTIPLIER[rarity]
    elif not rareflag:
        base *= MARKET_NON_RARE_MULTIPLIER

    resource = int(
        card.get("resourceId") or card.get("assetId") or (rating * 7919)
    )
    jitter = 0.94 + ((resource * 1103515245 + 12345) & 0xFFFF) / 65535.0 * 0.12
    return _round_price(base * jitter)


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
        # A search for one particular player is the price-comparison screen,
        # and it expects to see what several sellers are asking. A broad
        # search is a different question and gets one listing a card.
        if len(page) == 1 and (query.get("definitionId") or query.get("maskedDefId")):
            page = [page[0]] * _market_copies(page[0])
            total = len(page)
        for index, card in enumerate(page):
            price = _market_listing_price(card, index, len(page))
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
            duration = _market_duration(card, index)
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
                    "expires": duration,
                    "EXPIRE_TIME": duration,
                    "expireTime": duration,
                    "startTime": 0,
                    "endtime": 2147483647,
                    "buyNowPrice": price,
                    # Retail opens the bidding a little under the buy-now, not
                    # at half of it: a start of half is an invitation nobody
                    # would refuse and it made every auction the same auction.
                    "startingBid": _round_price(price * 0.82),
                    "currentBid": 0,
                    "offers": 0,
                    "watched": False,
                    "bidState": "none",
                    "tradeOwner": False,
                    "sellerName": _market_seller(card, index),
                    "sellerEstablished": 2013,
                    "sellerId": 1 + (_market_key(card) + index) % 999999,
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

    def user_info(self, club_name: str, club_abbr: str,
                  persona_id: int = 0) -> bytes:
        """FutGetUserInfo, flat -- there is no `userInfo` wrapper.

        Wrapping it is what made the club header print 0xCDCDCDCD: the parser
        did not recognise the shape and never wrote the fields at all.
        """
        return json.dumps(
            {
                # The same persona the EASW-Nucleus-Persona and EASW-Userid
                # headers carry. This was a flat 0 while both headers carried
                # the console's real nucleus id, so the four channels FUT
                # identifies a client through did not agree.
                #
                # Notes from a deployment with working online play put a name
                # to what that costs: an opponent that loads as a stub with a
                # blank eleven, and sessions that die about nineteen seconds
                # after login. There is no opponent here, but the EAS FC module
                # opens a second session against the same identity, and it has
                # been reporting itself disconnected throughout.
                "personaId": int(persona_id or PERSONA.id),
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
#
# `players` is how many of those slots hold a player. Retail sells twelve
# items and only three of them are players; the other nine are consumables and
# club items. Every pack here drew twelve players instead, which is why no
# contract, kit or manager has ever come out of one, and why the club's
# consumables tab has only ever shown what the club was seeded with. A players
# pack is the exception the name promises: all twelve.
PACK_SPECS: dict[int, dict] = {
    103: {"name": "Bronze Pack", "tier": "bronze", "coins": 400, "points": 0,
          "count": 12, "rares": 1, "players": 3, "premium": False,
          "group": "Packs Bronze"},
    104: {"name": "Premium Bronze Pack", "tier": "bronze", "coins": 750,
          "points": 0, "count": 12, "rares": 3, "players": 3, "premium": True,
          "group": "Packs Bronze"},
    203: {"name": "Silver Pack", "tier": "silver", "coins": 2500, "points": 50,
          "count": 12, "rares": 1, "players": 3, "premium": False,
          "group": "Packs Argent"},
    204: {"name": "Premium Silver Pack", "tier": "silver", "coins": 3750,
          "points": 75, "count": 12, "rares": 3, "players": 3, "premium": True,
          "group": "Packs Argent"},
    303: {"name": "Gold Pack", "tier": "gold", "coins": 5000, "points": 100,
          "count": 12, "rares": 1, "players": 3, "premium": False,
          "group": "Packs Or"},
    304: {"name": "Premium Gold Pack", "tier": "gold", "coins": 7500,
          "points": 150, "count": 12, "rares": 3, "players": 3, "premium": True,
          "group": "Packs Or"},
    305: {"name": "Jumbo Gold Pack", "tier": "gold", "coins": 10000,
          "points": 0, "count": 24, "rares": 7, "players": 8, "premium": True,
          "group": "Packs Or"},
    306: {"name": "Gold Players Pack", "tier": "gold", "coins": 15000,
          "points": 0, "count": 12, "rares": 1, "players": 12,
          "premium": False, "group": "Packs Or"},
    307: {"name": "Premium Gold Players Pack", "tier": "gold", "coins": 25000,
          "points": 0, "count": 12, "rares": 3, "players": 12, "premium": True,
          "group": "Packs Or"},

    # Packs this server adds. Retail FIFA 14 had no consumables-only pack and
    # nothing above 25 000, so these are not reconstructions of anything --
    # they are what an offline club with no store behind it needs to keep
    # being worth playing.
    #
    # `players` 0 makes a pack all consumables and club items; `guaranteed`
    # promises that many specials rather than rolling for them; `families`
    # replaces the house spread, so a Team of the Week pack cannot hand you a
    # Team of the Year instead.
    108: {"name": "Consumables Pack", "tier": "silver", "coins": 2000,
          "points": 0, "count": 12, "rares": 2, "players": 0, "premium": False,
          "group": "Consommables"},
    109: {"name": "Premium Consumables Pack", "tier": "gold", "coins": 6000,
          "points": 0, "count": 24, "rares": 8, "players": 0, "premium": True,
          "group": "Consommables"},
    308: {"name": "Rare Gold Pack", "tier": "gold", "coins": 100000,
          "points": 0, "count": 12, "rares": 12, "players": 12, "premium": True,
          "group": "Packs Or"},
    309: {"name": "Team of the Week Pack", "tier": "gold", "coins": 50000,
          "points": 0, "count": 12, "rares": 12, "players": 12, "premium": True,
          "guaranteed": 1, "families": {"team of the week": 1.0},
          "group": "Packs Speciaux"},
    310: {"name": "Team of the Season Pack", "tier": "gold", "coins": 250000,
          "points": 0, "count": 12, "rares": 12, "players": 12, "premium": True,
          "guaranteed": 2,
          "families": {"team of the season": 70.0, "team of the year": 12.0,
                       "record breaker": 6.0, "team of the week": 12.0},
          "group": "Packs Speciaux"},
}

# The order the store lists its groups in, cheapest first.
GROUP_ORDER = {
    "Packs Bronze": 0,
    "Packs Argent": 1,
    "Packs Or": 2,
    "Consommables": 3,
    "Packs Speciaux": 4,
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


# -- what comes out of a pack, and how often --------------------------------
#
# The draw used to be: split the tier's cards into `rareflag` set and unset,
# take the first `rares` slots from the first list and the rest from the
# second, uniformly. That is not a set of odds, it is an accident of what the
# catalogue happens to hold, and the accident was expensive.
#
# `rareflag` is set on a Rare Gold *and* on every special. In the gold tier
# that pool is 453 rare golds against 1120 specials, so a rare slot was a
# special seven times out of ten. Measured over 400 Gold Packs: 15% of them
# contained a special, and the family that came up most often was World Cup --
# because there are 517 World Cup cards in the gold tier and only 347 Team of
# the Week. Nothing decided any of that.
#
# So: rare and special are separated, the special is rolled once per pack
# against a stated chance, and its family is chosen by weight rather than by
# how many of each the database holds.

# Rating bands inside a tier, as weights. A uniform draw over the gold
# ordinaries is close to this by coincidence -- 862/254/66/18/4 -- but close by
# coincidence moves the moment the catalogue is edited.
RATING_BANDS: dict[str, tuple[tuple[tuple[int, int], float], ...]] = {
    "gold": (((75, 79), 72.0), ((80, 83), 20.0), ((84, 86), 6.0),
             ((87, 89), 1.7), ((90, 99), 0.3)),
    "silver": (((65, 69), 72.0), ((70, 72), 22.0), ((73, 74), 6.0)),
    "bronze": (((0, 59), 72.0), ((60, 62), 22.0), ((63, 64), 6.0)),
}

# A rare slot leans higher, and the lean is bounded rather than open.
RARE_BAND_MULTIPLIER = {(84, 86): 1.2, (87, 89): 1.45, (90, 99): 1.8}

# The chance a pack holds a special at all, and a second one having held the
# first. A second is deliberately much rarer than the first.
SPECIAL_CHANCE = {
    103: 0.006, 104: 0.012,
    203: 0.015, 204: 0.03,
    303: 0.08, 304: 0.16, 305: 0.25, 306: 0.20, 307: 0.35,
    # The added packs. 108 and 109 hold no players at all, so their chance
    # is nought by construction; 308 is all rare golds and pays for it.
    308: 0.45, 309: 1.0, 310: 1.0,
}
SECOND_SPECIAL_CHANCE = {303: 0.01, 304: 0.02, 305: 0.03, 306: 0.03,
                         307: 0.05, 308: 0.10, 309: 0.25, 310: 0.35}
MAX_SPECIALS_PER_PACK = 2
# A pack that promises more than the house limit gets what it promises;
# see `guaranteed` in PACK_SPECS.

# No pack hands out more than two cards rated 90 or better, however the bands
# fall. Odds are per card; this is the sentence about the pack.
ELITE_RATING = 90
MAX_ELITE_PER_PACK = 2

# How hard a pack tries to avoid handing out the same player twice.
DISTINCT_DRAW_ATTEMPTS = 12


def _pack_identity(card: dict) -> tuple:
    """The player and his version, as a catalogue row spells them.

    Not `card_signature`: that keys on `resourceId`, which the built
    item carries and the catalogue row it was built from does not, so
    comparing the two answered on different keys and let repeats
    through. Inside one pack, the asset and the rare flag are exactly
    "the same player, the same card".
    """
    return card_signature(card)

# Which special, when there is one. By weight, not by how many of each the
# catalogue holds.
#
# Legend is zero. FUT Legends were an Xbox exclusive so they belong in a 360
# pack, but nothing here has ever drawn one and whether the card renders is
# unknown -- an unknown card on the pack screen is how screens freeze. Raise
# it deliberately, with the console in front of you.
SPECIAL_FAMILY_WEIGHTS = {
    "team of the week": 58.0,
    "team of the season": 14.0,
    "world cup": 10.0,
    "motm": 8.0,
    "imotm": 5.0,
    "team of the year": 3.0,
    "record breaker": 1.0,
    "legend": 0.0,
}


def store_catalogue(timestamp: int = 2147483647) -> bytes:
    """Every pack, priced, grouped and buyable."""
    purchases = []
    # Listed by group, cheapest first inside each, so a group's packs are
    # contiguous and the headings come out in the order GROUP_ORDER gives.
    # Sorting by pack id put the two consumables packs, 108 and 109, between
    # the bronze and the silver ones.
    ordered = sorted(
        PACK_SPECS.items(),
        key=lambda row: (
            GROUP_ORDER.get(row[1]["group"], 99),
            row[1]["coins"],
            row[0],
        ),
    )
    for index, (pack_id, spec) in enumerate(ordered):
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
        # `value` is drawn as the group's heading, verbatim -- the store showed
        # "bronze", "silver" and "gold" in lower case because that is what it
        # was handed. It is not a localisation key: the packs' own
        # `FUT_STORE_PACK_<id>_DESC` is one, and that resolves against the
        # client's locale, which is why retail pack names read correctly and
        # the group headings did not.
        #
        # So the heading is written out, and the packs added here get headings
        # of their own instead of being filed under a tier they only nominally
        # belong to. `displayGroupAssetId` still names the tier's artwork.
        purchases.append(
            {
                "id": pack_id,
                "assetId": tier_asset,
                "actionType": "CREATEPACK",
                "packType": "CARDPACK",
                "description": f"FUT_STORE_PACK_{pack_id}_DESC",
                "displayGroup": {
                    # Unique per pack, which is what the document that
                    # rendered had. The client groups by `value`, so the
                    # priority only orders; sharing it between packs was a
                    # change nothing asked for.
                    "priority": index,
                    "value": spec["group"],
                },
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
        self._item_id_floor: int | None = None
        # Cards drawn but not yet acknowledged by the client. The purchased
        # items endpoint reports these, which is how they reach the club.
        self.pending: list[dict] = []
        self._pools: dict[str, list[dict]] = {}
        # The tier's ordinary cards, grouped by rating band and split on rare,
        # and its specials grouped by family. Built once: the catalogue is
        # 14 000 cards and the draw runs twelve times a pack.
        self._bands: dict[tuple[str, bool], list[tuple[tuple[int, int], float, list[dict]]]] = {}
        self._specials: dict[str, dict[str, list[dict]]] = {}
        for tier, (low, high) in TIER_RATINGS.items():
            pool = [
                card
                for card in catalogue.cards
                if low <= card.get("rating", 0) <= high
            ]
            self._pools[tier] = pool
            # Two pools, not rare against non-rare. A pack advertising "1 Rare"
            # promises at least one, not exactly one and nothing else: an
            # ordinary slot draws from every ordinary card of the tier. Making
            # it draw from non-rares only shut the top bands out of the pack
            # entirely, because a gold rated 84 or better is nearly always a
            # Rare Gold -- 84-86 came out at 0.65% against a stated 6%.
            for rare in (False, True):
                grouped = []
                for span, weight in RATING_BANDS.get(tier, ()):
                    band_low, band_high = span
                    cards = [
                        card
                        for card in pool
                        if is_ordinary(card)
                        and (card.get("rareflag") if rare else True)
                        and band_low <= card.get("rating", 0) <= band_high
                    ]
                    if cards:
                        grouped.append((span, weight, cards))
                self._bands[(tier, rare)] = grouped
            families: dict[str, list[dict]] = {}
            for card in pool:
                if is_ordinary(card):
                    continue
                families.setdefault(
                    (card.get("rarity") or "").strip().lower(), []
                ).append(card)
            self._specials[tier] = families

    # -- choosing a card ----------------------------------------------------

    def _draw_special(
        self, tier: str, rng: random.Random, weights: dict | None = None
    ) -> dict | None:
        """One special, its family chosen by weight rather than by stock.

        `weights` lets a pack promise a kind of special rather than the
        house spread -- a Team of the Week pack that could hand you a
        Team of the Year instead is not a Team of the Week pack.
        """
        table = weights or SPECIAL_FAMILY_WEIGHTS
        families = self._specials.get(tier) or {}
        choices = [
            (name, table.get(name, 0.0))
            for name, cards in families.items()
            if cards and table.get(name, 0.0) > 0
        ]
        if not choices:
            return None
        family = rng.choices(
            [name for name, _ in choices], weights=[w for _, w in choices]
        )[0]
        return rng.choice(families[family])

    def _draw_ordinary(
        self, tier: str, rare: bool, rng: random.Random, elite_left: int
    ) -> dict | None:
        """One ordinary card, its rating band chosen by weight.

        `elite_left` is how many more cards rated `ELITE_RATING` or better this
        pack may still hold. At zero the top bands are dropped from the draw
        rather than redrawn, so the cap costs nothing and cannot loop.
        """
        grouped = self._bands.get((tier, rare)) or self._bands.get((tier, False))
        if not grouped:
            return None
        choices = [
            (cards, weight * (RARE_BAND_MULTIPLIER.get(span, 1.0) if rare else 1.0))
            for span, weight, cards in grouped
            if elite_left > 0 or span[1] < ELITE_RATING
        ]
        if not choices:
            choices = [(cards, weight) for _, weight, cards in grouped]
        return rng.choice(
            rng.choices(
                [cards for cards, _ in choices],
                weights=[weight for _, weight in choices],
            )[0]
        )

    def _special_slots(
        self, pack_id: int, player_slots: list[int], rng: random.Random
    ) -> set[int]:
        """Which player slots of this pack, if any, hold a special.

        Rolled once for the pack, not once per card. Rolling per card made the
        pack's real chance a function of how many players it happened to hold,
        which is not what a stated chance means.
        """
        if not player_slots:
            return set()
        spec = PACK_SPECS.get(int(pack_id)) or {}
        # A pack that promises a special owes one every time, not with a
        # probability. `guaranteed` is that promise.
        guaranteed = max(0, int(spec.get("guaranteed", 0)))
        chosen: set[int] = set()
        for _ in range(min(guaranteed, len(player_slots))):
            remaining = [slot for slot in player_slots if slot not in chosen]
            if not remaining:
                break
            chosen.add(rng.choice(remaining))
        chance = SPECIAL_CHANCE.get(int(pack_id), 0.0)
        if not chosen and (chance <= 0 or rng.random() >= chance):
            return set()
        if not chosen:
            chosen = {rng.choice(player_slots)}
        second = SECOND_SPECIAL_CHANCE.get(int(pack_id), 0.0)
        if (
            len(chosen) < max(MAX_SPECIALS_PER_PACK, guaranteed)
            and second > 0
            and rng.random() < second
        ):
            remaining = [slot for slot in player_slots if slot not in chosen]
            if remaining:
                chosen.add(rng.choice(remaining))
        return chosen

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
            for slot, kind in enumerate(self._slot_kinds(spec, rng)):
                rare_slot = slot < int(spec["rares"])
                item_id = PACK_ITEM_ID_BASE + 900_000 + drawn_total
                # A new club needs contracts more than it needs a fourth
                # striker, so the starter packs carry the same nine non-player
                # slots the shop packs do.
                if kind == "extra":
                    item = _draw_extra(spec["tier"], rare_slot, item_id, rng)
                    if item is not None:
                        drawn.append(item)
                        drawn_total += 1
                        continue
                card = rng.choice(rares if rare_slot else commons)
                item = _player_item(
                    item_id=item_id,
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

    @staticmethod
    def _slot_kinds(spec: dict, rng: random.Random) -> list[str]:
        """Which slots of a pack hold a player and which hold everything else.

        Shuffled, so the players are not always the first three cards on the
        screen. A spec with no `players` key is all players, which is what
        every pack used to be.
        """
        count = int(spec["count"])
        players = max(0, min(count, int(spec.get("players", count))))
        kinds = ["player"] * players + ["extra"] * (count - players)
        rng.shuffle(kinds)
        return kinds

    def _next_item_id(self, slot: int) -> int:
        """An id no card already owned is using.

        This was `PACK_ITEM_ID_BASE + purchases * 100 + slot`, and `purchases`
        counts from zero every time the server starts. So the first pack after
        a restart reissued 1950000100 and up -- ids the saved club was already
        holding from an earlier session.

        `_keep` refuses an id it already holds, on the sound reasoning that the
        same item arriving twice is one card counted twice rather than two
        cards. Between them, a freshly packed card could be dropped on the way
        to the club and never appear anywhere. That is what happened to a
        Record Breaker Klose on 12 August: the club's 1950000205 was a
        Non-Rare Silver from a previous session, and the 90 went nowhere.

        So the counter is seeded from what is actually owned, once, the first
        time a pack is opened after a start.
        """
        if self._item_id_floor is None:
            owned = [item.get("id") or 0 for item in self.pending]
            if self.inventory is not None:
                owned += [item.get("id") or 0 for item in self.inventory.items]
            highest = max((i for i in owned if i < PACK_ITEM_ID_BASE + 900_000),
                          default=PACK_ITEM_ID_BASE)
            self._item_id_floor = max(PACK_ITEM_ID_BASE, highest) + 1
        return self._item_id_floor + (self.purchases - 1) * 100 + slot

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

        tier = spec["tier"]
        pool = self._pools.get(tier) or self.catalogue.cards
        kinds = self._slot_kinds(spec, rng)
        specials = self._special_slots(
            pack_id,
            [slot for slot, kind in enumerate(kinds) if kind == "player"],
            rng,
        )
        elite_left = MAX_ELITE_PER_PACK
        # The signatures this pack has already handed out.
        already: set = set()

        drawn = []
        for slot, kind in enumerate(kinds):
            item_id = self._next_item_id(slot)
            # A rare slot promises a rare card whatever kind of card it holds.
            rare_slot = slot < int(spec["rares"])
            if kind == "extra":
                item = _draw_extra(tier, rare_slot, item_id, rng)
                if item is not None:
                    drawn.append(item)
                    continue
                # No consumable catalogue: fall through and draw a player, so
                # the pack is still the size it advertises.
            # A pack never hands out the same player twice. Retail does not,
            # and the screen has no way to show it that is not confusing: two
            # Vargas out of one pack read as a bug whatever the data says. So
            # the draw is retried rather than the repeat explained.
            #
            # Bounded, and it keeps the last card if the pool cannot do better
            # -- a bronze tier with a narrow rare band can run out of distinct
            # cards before twelve slots are filled, and a pack that is short a
            # card is worse than a pack with a repeat in it.
            card = None
            for attempt in range(DISTINCT_DRAW_ATTEMPTS):
                # A special family can be small enough that every card in it
                # is already in this pack -- a Team of the Week pack drawing
                # two in forms out of a short list. After half the attempts the
                # slot stops insisting on a special and takes a rare instead,
                # which is a better card than the same one twice.
                if slot in specials and attempt < DISTINCT_DRAW_ATTEMPTS // 2:
                    card = self._draw_special(tier, rng, spec.get("families"))
                if card is None:
                    # A special is a rare card, so a slot that was going to
                    # hold one and found no family to draw from still owes a
                    # rare.
                    card = self._draw_ordinary(
                        tier, rare_slot or slot in specials, rng, elite_left
                    )
                if card is None:
                    card = rng.choice(pool)
                if _pack_identity(card) not in already:
                    break
                card = None
            if card is None or _pack_identity(card) in already:
                # Last resort: anything in the tier this pack has not already
                # handed out. Only the twelve-player packs ever reach here, and
                # only when the bands and the elite cap between them keep
                # offering the same card back.
                fresh = [
                    other for other in pool
                    if _pack_identity(other) not in already
                ]
                card = rng.choice(fresh) if fresh else (card or rng.choice(pool))
            already.add(_pack_identity(card))
            if card.get("rating", 0) >= ELITE_RATING:
                elite_left -= 1
            item = _player_item(
                item_id=item_id,
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

    # The club screen needs the same answer, so it lives at module level now.
    _signature = staticmethod(card_signature)

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
        # Everything already owned, wherever it is sitting. The club was the
        # only place looked at, and a card drawn from a pack does not go to the
        # club -- it goes to the purchased pile and waits there until it is
        # sent on. So packing the same player twice in a row said nothing the
        # second time: the first copy was in the pile, and the pile was not
        # being read. Klose 90, twice, on 12 August.
        owned: dict[tuple, int] = {}
        pools: list[list[dict]] = [self.pending]
        if self.inventory is not None:
            pools.append(self.inventory.items)
        for pool in pools:
            for item in pool:
                if item.get("itemType") != "player":
                    continue
                owned.setdefault(self._signature(item), item["id"])
        pairs: list[dict] = []
        for item in drawn:
            # Only players duplicate. A second contract card is not a repeat
            # of the first, it is a second contract -- consumables stack, and
            # marking one as a duplicate offers to quick-sell a card the club
            # is meant to accumulate.
            if item.get("itemType") != "player":
                continue
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
                # Reporting duplicates here froze the title once: after
                # buying a second Chamakh the client fetched squad/active,
                # tradePile and this, and stopped dead. What went out then was
                # a bare list of the *new* ids -- which points the client at
                # the card it is holding, and CardsDLL carrying
                # GetCardDuplicate and HAS_DUPLICATE says it wants the card
                # already owned.
                #
                # What goes out now is the pairing, {itemId: new,
                # duplicateItemId: owned}, which is the shape the pack response
                # and the club search both carry without trouble. The screen
                # has a duplicates tab of its own and it was being handed
                # nothing to put in it -- two Vargas out of one pack, marked on
                # the card and missing from the panel.
                "duplicateItemIdList": pile_duplicate_pairs(
                    self.pending,
                    self.inventory.items if self.inventory else [],
                ),
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
# FUT has exactly two consumable item types, and the family is not one of
# them: `cardsubtypeid` carries the family, `itemType` carries only whether
# the card develops a player or trains one.
#
# This served "contract", "fitness", "healing", "playStyle" and "position" --
# names invented here to match the club screens' own filters. The club tab
# worked because it filtered on the same invented names; the native Apply
# Consumable picker did not, and reported "Pas d'élément disponible" over a
# club holding 35 contracts.
CONSUMABLE_TYPES = {"development", "training"}

# Catalogue family -> the type that goes on the wire.
CONSUMABLE_WIRE_TYPE = {
    "contract": "development",
    "fitness": "development",
    "healing": "development",
    "position": "development",
    "training": "training",
    "playStyle": "training",
}

# The families themselves, as `tools/build_consumables.py` names them. Used to
# pick and weight cards, never sent.
CONSUMABLE_FAMILIES = set(CONSUMABLE_WIRE_TYPE)


def consumable_family(item: dict) -> str:
    """Which family an owned card belongs to, from the catalogue.

    The item cannot say: its `itemType` is `development` or `training` and
    nothing finer. `resourceId` is the card's own database id, so the
    catalogue answers it.
    """
    row = _consumable_definitions().get(int(item.get("resourceId") or 0))
    return row.get("itemType", "") if row else ""


PILE_TRANSFER = 5
PILE_PURCHASED = 6
PILE_CLUB = 7


# -- applying a consumable --------------------------------------------------
#
# `POST /ut/game/fifa14/item/resource/<resourceId>` with `{"apply":[{"id":N}]}`.
#
# `apply` is in CardsDLL's own member-name table, immediately beside `applyTo`,
# so the request member is the module's and not a guess; the same call is what
# the PC revival serves. The reply is empty: retail answers this one by status.
#
# Until now the club could hold a contract and show it and there was no route
# that did anything with it. Every consumable in FUT was decoration.

# What each card does, keyed by the subtype the game's own card database
# carries. The blocks come from `tools/build_consumables.py`, which reads them
# out of `cards_ng_db.db`.
CONTRACT_PLAYER, CONTRACT_MANAGER = 201, 202
# Chemistry styles, split the way CardsDLL counts them: outfield players under
# `consumablesTrainingPlayerPlayStyle`, goalkeepers under
# `consumablesTrainingGkPlayStyle`.
PLAY_STYLE_OUTFIELD = (91, 110)
PLAY_STYLE_GK = (121, 136)
HEALING_FIRST, HEALING_ANY = 211, 218
FITNESS_PLAYER, FITNESS_SQUAD = 219, 220

# 211 through 217 in order. The binary carries exactly this list and in this
# order -- FUT_HEAD_HEALING, FUT_UPPERBODY_HEALING, FUT_ARM_HEALING,
# FUT_BACK_HEALING, FUT_KNEE_HEALING, FUT_LEG_HEALING, FUT_FOOT_HEALING -- and
# 214 (back) is simply not in the card database. 218 heals anything.
HEALING_KINDS = ("head", "upperbody", "arm", "back", "knee", "leg", "foot")

# The two training blocks, six attributes each and a seventh card that raises
# all six. The offset inside the block *is* the attribute index: a player's
# `attributeList` holds six entries indexed 0 to 5, and for a keeper those six
# slots hold the keeper's attributes. So the index is right either way.
#
# Which block is the keeper's is NOT established. `build_consumables.py` calls
# 51-57 the outfield block and 61-67 the keeper's; the PC revival's catalogue
# says the opposite, and nothing in `fcc_trainingcards` names either -- it
# carries no name column, only a card art id (3 against 1). Because the
# attribute index is the same either way, the boost lands correctly whichever
# is which, so this applies the card without checking the target is the right
# kind of player rather than enforcing a rule it cannot prove.
TRAINING_BLOCKS = ((51, 57), (61, 67))

PLAYER_ATTRIBUTES = 6
ATTRIBUTE_CEILING = 99


class ConsumableRefused(Exception):
    """The card cannot be applied, and the reason is worth reporting."""


def _card_quality(rating: int) -> str:
    for tier, (low, high) in TIER_RATINGS.items():
        if low <= int(rating) <= high:
            return tier
    return "gold"


def _consumable_definitions() -> dict[int, dict]:
    """Catalogue rows by `definitionId`, which is the card's own database id."""
    return {int(card["definitionId"]): card for card in _consumable_catalogue()}


def _training_index(subtype: int) -> int | None:
    """Which attribute a training card raises, or None for the all-six card."""
    for first, last in TRAINING_BLOCKS:
        if first <= subtype < last:
            return subtype - first
        if subtype == last:
            return None
    raise ConsumableRefused(f"unsupported training subtype {subtype}")


def _bump_attributes(player: dict, indexes, amount: int) -> None:
    entries = player.get("attributeList") or []
    for entry in entries:
        if entry.get("index") in indexes:
            entry["value"] = min(ATTRIBUTE_CEILING, int(entry.get("value", 0)) + amount)


class ConsumableRack:
    """Applies an owned consumable to an owned card, and spends it."""

    def __init__(self, inventory: "ClubInventory") -> None:
        self.inventory = inventory
        # Every request the client made that could not be honoured, kept so
        # the contested families -- the play style and position blocks below
        # -- can be identified from one real application rather than guessed.
        self.refused: list[dict] = []

    # -- finding the card ---------------------------------------------------

    def _owned(self, resource_id: int) -> tuple[dict, dict]:
        """The club's copy of the card the request names, and its catalogue row.

        The request addresses the *definition*, not one particular card, so any
        owned copy will do. `resourceId` is the card's own database id -- 5001001
        and up -- and identifies exactly one definition, which is why the item
        carries it rather than a value derived from the card art: derived, every
        training card in the club answered to one id and this call could not
        tell a +5 card from a +15 one.
        """
        definitions = _consumable_definitions()
        matches = [
            item
            for item in self.inventory.items
            if item.get("itemType") in CONSUMABLE_TYPES
            and item.get("resourceId") == resource_id
        ]
        if not matches:
            raise ConsumableRefused(f"consumable {resource_id} is not owned")
        row = definitions.get(int(resource_id))
        if row is None:
            raise ConsumableRefused(f"consumable {resource_id} is not in the catalogue")
        return matches[0], row

    def resource_of(self, item_id: int) -> int:
        """The definition behind one owned consumable, by its item id.

        The client addresses a consumable two ways. `item/resource/<id>` names
        the definition and any owned copy will do; `item/<id>` names one
        particular card in the club. Only the first was ever handled, so a
        real application on 11 August at 03:00 --

            POST /ut/game/fifa14/item/1950000106
            {"apply":[{"id":1700000004}]}

        -- was answered 404 and went in the unhandled journal, where nobody
        looked. From the player's side the card simply did nothing.
        """
        for item in self.inventory.items:
            if item.get("id") != item_id:
                continue
            if item.get("itemType") not in CONSUMABLE_TYPES:
                raise ConsumableRefused(f"item {item_id} is not a consumable")
            resource = item.get("resourceId")
            if not resource:
                raise ConsumableRefused(f"item {item_id} carries no resourceId")
            return int(resource)
        raise ConsumableRefused(f"item {item_id} is not in the club")

    def _target(self, item_id: int) -> dict:
        for item in self.inventory.items:
            if item.get("id") == item_id:
                return item
        raise ConsumableRefused(f"target {item_id} is not in the club")

    # -- the effects --------------------------------------------------------

    def apply(self, resource_id: int, target_ids: list[int]) -> dict:
        """Apply one card. Raises ConsumableRefused rather than half-applying.

        Nothing is spent and nothing is written until the effect has been
        decided, so a refusal costs the player neither the card nor the state.
        """
        card, row = self._owned(int(resource_id))
        subtype = int(row.get("cardsubtypeid") or 0)
        amount = int(row.get("amount") or 0)

        if subtype == FITNESS_SQUAD:
            changed = self._squad_fitness(amount)
            effect = f"squad fitness +{amount}"
        else:
            if not target_ids:
                raise ConsumableRefused("this card needs a target")
            target = self._target(int(target_ids[0]))
            changed, effect = self._apply_to(target, row, subtype, amount)

        self.inventory.items.remove(card)
        return {
            "consumedItemId": card["id"],
            "resourceId": int(resource_id),
            "effect": effect,
            "itemData": changed,
        }

    def _apply_to(
        self, target: dict, row: dict, subtype: int, amount: int
    ) -> tuple[list[dict], str]:
        kind = target.get("itemType")

        if subtype in (CONTRACT_PLAYER, CONTRACT_MANAGER):
            if subtype == CONTRACT_PLAYER and kind != "player":
                raise ConsumableRefused("a player contract needs a player")
            if subtype == CONTRACT_MANAGER and kind not in ("manager", "staff"):
                raise ConsumableRefused("a manager contract needs a manager")
            # A contract grants a different number of matches to a gold, a
            # silver and a bronze card, and the card database carries all
            # three. The card is named for the gold figure.
            quality = _card_quality(target.get("rating", 0))
            gain = int(row.get(quality, row.get("amount", 0)) or 0)
            target["contract"] = min(
                ATTRIBUTE_CEILING, int(target.get("contract", 0)) + gain
            )
            return [target], f"contract +{gain}"

        if kind != "player":
            raise ConsumableRefused("this card can only be applied to a player")

        if subtype == FITNESS_PLAYER:
            target["fitness"] = min(
                ATTRIBUTE_CEILING, int(target.get("fitness", 0)) + amount
            )
            return [target], f"fitness +{amount}"

        if HEALING_FIRST <= subtype <= HEALING_ANY:
            return self._heal(target, subtype, amount)

        if any(first <= subtype <= last for first, last in TRAINING_BLOCKS):
            index = _training_index(subtype)
            indexes = range(PLAYER_ATTRIBUTES) if index is None else (index,)
            _bump_attributes(target, set(indexes), amount)
            where = "all six" if index is None else f"attribute {index}"
            return [target], f"training {where} +{amount}"

        if PLAY_STYLE_OUTFIELD[0] <= subtype <= PLAY_STYLE_GK[1]:
            return self._play_style(target, subtype)

        # The position block (232). What each card in it does is contested:
        # this server's catalogue called it a position change, the PC
        # revival's agrees, and the binary carries a FUT_CONSUMABLE_POSITIONMOD
        # -- but the card the console actually rendered for 232 reads
        # "DÉBLOQUER / Capacité +8 moral", which is a stadium unlock and not a
        # position at all. Both catalogues are wrong about it and the client
        # refuses to keep the card.
        #
        # Writing `preferredPosition` on the strength of that would silently
        # change the wrong field on a real card, and a card is spent either
        # way. Refused, and recorded: one application from the console names
        # the family, because the screen shows the player what the card was.
        self.refused.append(
            {
                "resourceId": row.get("definitionId"),
                "cardsubtypeid": subtype,
                "itemType": row.get("itemType"),
                "targetId": target.get("id"),
                "targetPosition": target.get("preferredPosition"),
                "targetPlayStyle": target.get("playStyle"),
            }
        )
        raise ConsumableRefused(
            f"subtype {subtype} has no established effect on this platform"
        )

    def _play_style(self, target: dict, subtype: int) -> tuple[list[dict], str]:
        """A chemistry style, written onto the card's own `playStyle`.

        This was refused for weeks, on the grounds that 91-136 might be
        position modifiers rather than play styles. What settles it is the
        member CardsDLL counts these cards under, which is in the binary's own
        name table and is not a label anybody here chose:

            91-110   consumablesTrainingPlayerPlayStyle
            121-136  consumablesTrainingGkPlayStyle

        Two ranges, one for outfield players and one for goalkeepers, which is
        exactly how chemistry styles are split in FUT and is not how a position
        modifier would be. `playStyle` is a member every player card already
        carries, and it has sat at 0 on every card in the club since the club
        existed.

        What is *not* established is the numbering: the value written is the
        card's own `cardsubtypeid`, on the reading that the style is the
        subtype. If that enumeration turns out to be offset, the visible
        consequence is one card showing the wrong style name -- another style
        card puts it right, which is not true of a wrongly written position.

        The goalkeeper split is enforced. A GK style on an outfield player is
        the one mistake the ranges make obvious, and spending the card on it
        would be the player's loss.
        """
        keeper = (target.get("preferredPosition") or "").upper() == "GK"
        for_keeper = PLAY_STYLE_GK[0] <= subtype <= PLAY_STYLE_GK[1]
        if for_keeper and not keeper:
            raise ConsumableRefused("a goalkeeper style needs a goalkeeper")
        if not for_keeper and keeper:
            raise ConsumableRefused("an outfield style cannot go on a goalkeeper")
        target["playStyle"] = subtype
        return [target], f"play style {subtype}"

    def _heal(self, target: dict, subtype: int, amount: int) -> tuple[list[dict], str]:
        games = int(target.get("injuryGames", 0) or 0)
        if games <= 0:
            raise ConsumableRefused("that player is not injured")
        if subtype != HEALING_ANY:
            wanted = HEALING_KINDS[subtype - HEALING_FIRST]
            injury = str(target.get("injuryType", "")).strip().lower()
            if injury.replace(" ", "") != wanted:
                raise ConsumableRefused(
                    f"a {wanted} healing card does not treat a {injury or 'none'} injury"
                )
        remaining = max(0, games - amount)
        target["injuryGames"] = remaining
        if remaining == 0:
            target["injuryType"] = "none"
        return [target], f"healing -{amount} match(es)"

    def _squad_fitness(self, amount: int) -> list[dict]:
        squad = [item for item in self.inventory.squad if isinstance(item, dict)]
        if not squad:
            raise ConsumableRefused("there is no active squad to restore")
        for player in squad:
            player["fitness"] = min(
                ATTRIBUTE_CEILING, int(player.get("fitness", 0)) + amount
            )
        return squad


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

    def _unlisted_entry(self, item: dict) -> dict:
        """A card sent to the transfer list but not put up for sale yet.

        Sending a card to the transfer list takes it out of the club -- it has
        to, or it shows in both places at once -- and until now the trade pile
        answered with the listings alone, so the card was in neither. It went
        the way the lost pack cards and the withdrawn contracts went: quietly,
        with the screen showing nothing where it should have been. The same
        symptom is reported against the PC revival, where unlisted pile-5 cards
        were disappearing from the Transfer List.

        Every member here already goes out on a real listing; only the values
        differ. `tradeId` 0 and `expires` -1 are what say "not up for sale",
        and the screen needs the entry to exist at all before it can offer to
        list it.
        """
        return {
            "tradeId": 0,
            "id": 0,
            "itemData": item,
            "tradeState": "",
            "startingBid": 0,
            "buyNowPrice": 0,
            "currentBid": 0,
            "offers": 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": True,
            "expires": -1,
            "sellerName": "",
            "sellerEstablished": 0,
            "sellerId": 0,
            "confidenceValue": 0,
        }

    def trade_pile(self, coins: int) -> bytes:
        """The transfer list: what is up for sale, and what is merely on it."""
        listed = {
            listing["itemData"].get("id")
            for listing in self.listings.values()
            if isinstance(listing.get("itemData"), dict)
        }
        entries = list(self.listings.values()) + [
            self._unlisted_entry(item)
            for item in self.transfer
            if item.get("id") not in listed
        ]
        return json.dumps(
            {
                "auctionInfo": entries,
                "duplicateItemIdList": [],
                "total": len(entries),
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
            # `id`, not `assetId`. The guard is "is this a card at all", and
            # `assetId` stopped answering that the moment a consumable started
            # carrying `cardassetid` instead -- withdrawing a listed contract
            # dropped it on the floor, silently, exactly the way the lost pack
            # cards used to go.
            if item.get("id"):
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


def _consumable_item(card: dict, item_id: int, item_state: str = "free") -> dict:
    """One consumable card, in the shape the club and a pack both use.

    A card drawn from a pack and a card the club was seeded with have to be
    the same object -- the pack screen, the club list and the consumables tab
    all read the same members, and a card that is shaped one way in the pack
    and another in the club is a card that disappears on the way there.

    Three of the members this used to carry -- `definitionId`, `consumableType`
    and `consumableMember` -- appear **nowhere in CardsDLL**, not in the JSON
    member-name table and not anywhere else in the module. A parser has no
    name for them, so they were never read; `consumableMember` was invented
    here outright. The PC revival hit a freeze inside the purchased-items
    parser from exactly this, an extra descriptive field on a pack consumable,
    and it froze on the *second* pack rather than the first.

    Two more that the reference sends are absent from this build too --
    `resourceGameYear` and the `rareFlag` capitalisation -- so they are not
    sent either. What remains is what the binary has a name for.

    `cardassetid`, not `assetId`: the art id for a consumable has its own
    member, and sending only `assetId` is why five of nine cards in a Premium
    Gold Pack drew NOT FOUND, all labelled "Entraînement", all showing +0.

    `resourceId` is the card's own database id -- 5001001 and up -- not a value
    derived from the art. Derived, every training card in the club shared one
    resource id, which is also the id the apply route addresses.
    """
    rare = 1 if card["rare"] else 0
    item = {
        "id": item_id,
        "itemId": item_id,
        "resourceId": card["definitionId"],
        "cardassetid": card["assetId"],
        "cardsubtypeid": card["cardsubtypeid"],
        "rating": card["rating"],
        "rareflag": rare,
        # Both spellings. The PC revival sends both because different parsers
        # in the client read different ones, and the Apply Consumable picker
        # is one of the screens that had nothing to show.
        "rareFlag": rare,
        "amount": card["amount"],
        "itemType": CONSUMABLE_WIRE_TYPE.get(card["itemType"], "development"),
        "itemState": item_state,
        "discardValue": 0,
        "lastSalePrice": 0,
        "timestamp": 1,
        "owners": 1,
        "untradeable": False,
        "tradeable": True,
        # Which pile the card sits in. Absent, the parser left it at whatever
        # its constructor held, and a picker asking "what do I own" has no way
        # to answer. 7 is the club, 6 is the purchased pile a card sits in
        # until it is sent to the club -- the PC revival overwrites its stored
        # 6 with a 7 on everything it preloads for the picker.
        "pile": PILE_PURCHASED if item_state == "new" else PILE_CLUB,
        "resourceGameYear": 2014,
        "count": 1,
    }
    # A contract is worth a different number of matches to a gold, a silver
    # and a bronze card, and the card carries all three figures.
    for tier in ("bronze", "silver", "gold"):
        if tier in card:
            item[tier] = card[tier]
    # The misc cards carry a second art id: `cardassetid` is the family's
    # picture and `assetid` picks the variant inside it. Both are member names
    # the module carries. Without it the club-modifier cards drew NOT FOUND
    # while their names resolved -- the client knew the card and had no
    # picture for it.
    if "assetid" in card:
        item["assetid"] = card["assetid"]
    return item


def _club_item(
    kind: str, asset_id: int, resource_id: int, item_id: int,
    item_state: str = "free",
) -> dict:
    """One kit, badge, stadium, ball, manager or staff card."""
    return {
        "id": item_id,
        "assetId": asset_id,
        "resourceId": resource_id,
        "rating": 0,
        "itemType": kind,
        "itemState": item_state,
        "discardValue": 0,
        "lastSalePrice": 0,
        "timestamp": 1,
        "untradeable": False,
        "rareflag": 0,
    }


def _club_extras() -> list[dict]:
    """Consumables, kits, badges, stadiums, balls and staff."""
    items: list[dict] = []
    next_id = CLUB_ITEM_ID_BASE

    for card in _consumable_catalogue():
        for _ in range(CONSUMABLE_COPIES.get(card["itemType"], 1)):
            items.append(_consumable_item(card, next_id))
            next_id += 1

    for kind, asset, resource, count in CLUB_ITEM_KINDS:
        for index in range(count):
            items.append(_club_item(kind, asset + index, resource + index, next_id))
            next_id += 1
    return items


# -- what a pack hands out that is not a player -----------------------------
#
# The same two sources the club is seeded from, drawn instead of granted. Each
# template carries the tier it belongs to and whether it counts as a rare, so
# a bronze pack fills its nine non-player slots with bronze consumables and a
# rare slot can actually land on one.

# How often each family comes up, per family and not per card. Drawing evenly
# across the 124 templates would be wrong: there are 42 training cards and 13
# contracts, so an even draw hands out three times more training than contract
# and a club still runs out of contracts. Retail is the other way round.
CONSUMABLE_DRAW_WEIGHT = {
    "contract": 40, "fitness": 22, "healing": 12, "playStyle": 10,
    "training": 8,
}

# Subtype 232 is not drawn, and it is not a position modifier either.
#
# The console named it: "DEBLOQUER / Capacite +8 moral" and "Grosse affluence
# morale 6". They are stadium unlockables, and the client refuses to keep one:
#
#   "Certains des elements ne peuvent etre conserves dans votre club car ils
#    sont deverrouillables. Utilisez-les en faisant s'afficher le menu
#    d'actions et en selectionnant 'Utiliser element'."
#
# That popup is retail behaviour, not a fault -- these do come out of retail
# packs. But nothing here serves the route behind "Utiliser element", so a
# drawn one is dead weight that raises a dialog on every pack. Drawn again
# once that route is known.
#
# This server's catalogue calls the family `position` and the PC revival's
# calls it "Internal Position". Both are wrong.
UNDRAWN_CONSUMABLE_TYPES = {"position"}

# Kits, badges, stadiums and balls have no rating and so no tier of their own.
# They are drawn in any pack. Managers and staff are the valuable end of the
# non-player draw and are kept scarce.
CLUB_ITEM_DRAW_WEIGHT = {
    "kit": 30, "badge": 30, "stadium": 12, "ball": 18, "manager": 4,
    "staff": 6,
}

# How the non-player slots divide.
#
# One, for now: consumables only. Kits, badges, balls and stadiums carry
# resource ids invented in this file -- 6000000 and up -- because no table in
# `cards_ng_db` or `fifa_ng_db` names them. Consumables have real ids and the
# real ids resolve; the invented ones drew blank card backs on the pack screen,
# two of them in a single Premium Gold Pack.
#
# The club is still seeded with kits and badges, which is where they came from
# and where the club screen expects them. Lower this once their identities
# come from the game's own data rather than from a counter.
PACK_CONSUMABLE_SHARE = 1.0

_PACK_EXTRAS: dict[tuple[str, str], list[dict]] | None = None


def _extra_tier(rating: int) -> str:
    for tier, (low, high) in TIER_RATINGS.items():
        if low <= int(rating) <= high:
            return tier
    return "gold"


def pack_extras() -> dict[tuple[str, str], list[dict]]:
    """Every non-player card a pack can draw, grouped by family, built once.

    Templates, not items: they carry no id, because the id belongs to the
    draw. `_draw_extra` copies one and stamps it. Grouping by family is what
    lets the draw weight contracts against training rather than against the
    number of training variants that happen to exist.
    """
    global _PACK_EXTRAS
    if _PACK_EXTRAS is not None:
        return _PACK_EXTRAS

    families: dict[tuple[str, str], list[dict]] = {}
    for card in _consumable_catalogue():
        if card["itemType"] in UNDRAWN_CONSUMABLE_TYPES:
            continue
        template = _consumable_item(card, 0, item_state="new")
        # No identifier survives into a template: `id` and `itemId` both
        # belong to the draw, and a stale `itemId` of 0 shipped on every pack
        # consumable until this popped it too.
        template.pop("id")
        template.pop("itemId", None)
        template["_tier"] = _extra_tier(card["rating"])
        template["_rare"] = bool(card["rare"])
        families.setdefault(("consumable", card["itemType"]), []).append(template)

    for kind, asset, resource, count in CLUB_ITEM_KINDS:
        for index in range(count):
            template = _club_item(kind, asset + index, resource + index, 0,
                                  item_state="new")
            template.pop("id")
            template.pop("itemId", None)
            # No tier: a kit is a kit in any pack.
            template["_tier"] = ""
            template["_rare"] = False
            families.setdefault(("club", kind), []).append(template)

    _PACK_EXTRAS = families
    return families


def _draw_extra(
    tier: str, rare: bool, item_id: int, rng: random.Random
) -> dict | None:
    """One non-player card for a pack slot.

    The family comes first and by weight, and only families that hold a card
    of this tier are eligible -- chemistry styles exist in gold only, and
    choosing that family in a silver pack and then relaxing the tier is how a
    99-rated style landed in a Silver Pack. Inside the family only the rare
    flag relaxes.

    An empty consumable catalogue -- the file is optional -- returns None, and
    the caller draws a player instead, which is what packs used to be.
    """
    pool = pack_extras()
    if not pool:
        return None

    kind = "consumable" if rng.random() < PACK_CONSUMABLE_SHARE else "club"
    weights = (
        CONSUMABLE_DRAW_WEIGHT if kind == "consumable" else CLUB_ITEM_DRAW_WEIGHT
    )

    def in_tier(templates: list[dict]) -> list[dict]:
        return [t for t in templates if t["_tier"] in ("", tier)]

    choices = [
        (key, weights.get(key[1], 1))
        for key, templates in pool.items()
        if key[0] == kind and in_tier(templates)
    ]
    if not choices:
        # Nothing of this kind reaches this tier: the other kind carries the
        # slot rather than the slot handing out the wrong tier.
        choices = [
            (key, 1) for key, templates in pool.items() if in_tier(templates)
        ]
    if not choices:
        return None

    family = rng.choices(
        [key for key, _ in choices], weights=[w for _, w in choices]
    )[0]
    candidates = in_tier(pool[family])
    exact = [t for t in candidates if t["_rare"] == rare]
    item = {k: v for k, v in rng.choice(exact or candidates).items()
            if not k.startswith("_")}
    item["id"] = item_id
    if "cardassetid" in item:
        item["itemId"] = item_id
    return item


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

    Magic, declared size, zero directory entries, header size. Empty on
    purpose: the EA trophy CDN is gone and no art is being invented here. It
    makes the response parseable rather than wrong.

    **The size is little-endian and the other two are big-endian.** That is not
    a choice; it is what a real BIGF from this game carries. Read out of the
    Title Update's own helperFunctions package:

        BIGF   54032 (little-endian)   3 entries (big)   header 56 (big)

    All four fields went out big-endian here, so a sixteen-byte archive
    declared its own size as 0x10000000 -- 268 megabytes. What a client does
    with that is its own business, and both screens that freeze after being
    served everything -- resuming a cup, and entering seasons -- ask for this
    archive on the way in. That is a correlation and not a demonstration; the
    field is wrong either way.
    """
    return (
        b"BIGF"
        + (16).to_bytes(4, "little")
        + (0).to_bytes(4, "big")
        + (16).to_bytes(4, "big")
    )


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

    Attempt four, on 13 August, changed the asset chain underneath and not the
    document: `/fut/items/xbl2/-1.json` answers with a real definition now
    instead of an empty one, so the console built
    `trophies/xbl2/item.big` with a basename instead of `.big` with none, and
    that archive no longer declares itself 268 MB long. Both were real defects
    on this exact path. **The screen still froze**, in the same place, after
    both documents were served -- so the asset chain was not it, and what is
    wrong is in the record.

    Which is what SEASONS.md prescribes reducing, one variable at a time. The
    ladder is reachable by name:

        empty     no seasons at all -- the only answer known to break nothing
        minimal   one division, no `matches`, no `prizeSet`
        prizes    minimal plus `prizeSet`
        matches   minimal plus `matches`
        native    every division, both arrays -- the shape that freezes

    Each rung costs a server restart and one entry into the mode. Serving the
    whole record at once produces a freeze and no information.
    """
    raw = os.environ.get("FIFA14_SEASON_MODE", "empty").strip().lower()
    if raw in {"native", "full", "on"}:
        return "native"
    if raw in {"minimal", "min", "bare"}:
        return "minimal"
    if raw in {"prizes", "prizeset"}:
        return "prizes"
    if raw == "matches":
        return "matches"
    if raw in {"nouser", "listonly"}:
        return "nouser"
    if raw in {"user-id", "userid"}:
        return "user-id"
    if raw in {"user-division", "userdivision"}:
        return "user-division"
    if raw in {"user-round", "userround"}:
        return "user-round"
    return "empty"


def seasons_response() -> bytes:
    """The divisions.

    A season carries its fixture list in `matches` and its rewards in
    `prizeSet`, both arrays of records -- the same fault as a cup's `rounds`
    served as a count, one level deeper. Every member of the native record was
    checked against the module's name table, and the screen still froze, so
    something below is still wrong and the freeze is not worth serving by
    default. See `season_wire_mode`.
    """
    mode = season_wire_mode()
    if mode == "empty":
        return json.dumps({"seasons": []}, separators=(",", ":")).encode()
    if mode.startswith("user-") or mode == "nouser":
        # Every rung above `nouser` serves the same minimal list; what varies
        # is how much of `season/user` goes out beside it.
        mode = "minimal"

    records = [
        _season_record(index, division, matches, promote, coins)
        for index, (division, _name, matches, promote, coins) in enumerate(
            SEASON_DIVISIONS, start=1
        )
    ]
    if mode != "native":
        # One rung of the ladder: a single division, and only the array the
        # rung is named for. Reducing is the only way through a freeze, which
        # gives no error to read.
        records = records[:1]
        keep_matches = mode == "matches"
        keep_prizes = mode == "prizes"
        for record in records:
            if not keep_matches:
                record["matches"] = []
                record["numMatches"] = 0
            if not keep_prizes:
                record["prizeSet"] = []
    return json.dumps({"seasons": records}, separators=(",", ":")).encode()


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
    mode = season_wire_mode()
    if mode.startswith("user-"):
        # The halving `nouser` earned: the same minimal list opened the mode
        # when this document was `{}` and froze it when it carried all three
        # members. So the fault is here, in `seasonId`, `divisionId` or
        # `round`, and they go back one at a time.
        document = {"seasonId": 1}
        if mode in {"user-division", "user-round"}:
            # `divisionId` is the member that freezes: `{"seasonId": 1}` alone
            # holds the screen, adding this one hangs it. And the value it was
            # sent, 10, is exactly the `divisionId` the single served record
            # carries -- so "it must name a division in the list" is not the
            # rule, since it did.
            #
            # What 10 also is, on a list of ten, is one past the last index;
            # on a list of one it is far past. FIFA14_SEASON_DIVISION overrides
            # it so the reading can be tested rather than argued.
            try:
                document["divisionId"] = int(
                    os.environ.get("FIFA14_SEASON_DIVISION", division)
                )
            except ValueError:
                document["divisionId"] = int(division)
        if mode == "user-round":
            document["round"] = 1
        return json.dumps(document, separators=(",", ":")).encode()
    if mode in {"empty", "nouser"}:
        # What this route carried before any of the guessed shapes: an empty
        # object leaves the native response at its constructor defaults rather
        # than naming a season the list no longer offers.
        #
        # `nouser` serves the minimal list beside this empty answer, which is
        # the halving `minimal` earned: that rung froze with both arrays empty
        # and a single division, so neither array is the fault and the question
        # becomes which of the two documents carries it.
        return b"{}"
    # Where the club actually stands, if it stands anywhere. The client saves
    # its own progress to `season/<id>/division/<div>/user` after every match
    # -- it went up at round 2 the moment the first one was walked out of --
    # and until that was handled this document reported round 1 for ever, so
    # re-entering the mode offered ten matches remaining out of ten however
    # many had been played.
    saved = SEASON_PROGRESS.current()
    if saved is not None:
        _season, saved_division = saved
        entry = SEASON_PROGRESS.entries.get(saved) or {}
        division = saved_division
        played = max(0, int(entry.get("round") or 1) - 1)
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
            # The division's **position** in the list served above, counted
            # from zero -- not its number. Sending the number is what froze the
            # mode for as long as it has existed.
            #
            # Bisected down `season/user`'s three members on 13 August:
            # `{}` opened the screen and held, `{"seasonId": 1}` held,
            # `{"seasonId": 1, "divisionId": 10}` opened and then hung, and all
            # three together hung. So it is this member, and it is not failing
            # to name a division the list holds -- the record served beside it
            # carried `divisionId` 10 itself. What 10 also is, on a list of
            # ten, is one past the last index. Sent as 0 the screen holds and
            # offers to start the season.
            #
            # The two run in opposite directions, which is what made the
            # confusion easy to keep: record ids ascend 1..10 while the
            # division numbers descend 10..1.
            "divisionId": index - 1,
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
        """A saved run is handed back exactly as the client wrote it.

        A cup with no saved progress answers with its id and nothing else.
        That is what the client sends up first, and inventing a round or an
        empty data blob for a cup that was never entered would put the screen
        into a tournament that does not exist.

        For a cup that *was* entered the reply is the five members the client
        itself serialised -- `round`, `dataVersion`, `tournamentData`,
        `progressDataVersion`, `progressData` -- and nothing besides. This
        reply had never been exercised: every earlier journal line for
        `tournament/user/<id>` is a PUT, and the first GET ever made of it,
        resuming a cup already under way, froze the title on the spot.

        Two things were added here that the client never sends. A leading
        `tournamentId`, which the id is already in the path for; and a second
        copy of the progress blob spelled `progressdata`. That spelling is in
        the name table, so it is *not* an unrecognised sibling the parser
        skips -- it is the same known field arriving twice, decoded twice into
        one slot. Removing both made the reply byte-identical to the client's
        own PUT -- and the title still froze on it. So the shape was never
        what killed it, and an entered-but-unplayed run is answered as no run
        at all; see `unplayed` below.
        """
        identifier = int(identifier)
        entry = self.entries.get(identifier)
        if entry is None or self.unplayed(entry):
            return json.dumps(
                {"tournamentId": identifier}, separators=(",", ":")
            ).encode()
        return json.dumps(
            {
                "round": entry["round"],
                "dataVersion": entry["dataVersion"],
                "tournamentData": entry["tournamentData"],
                "progressDataVersion": entry["progressDataVersion"],
                "progressData": entry["progressData"],
            },
            separators=(",", ":"),
        ).encode()

    def advance(self, identifier: int, result: str) -> dict:
        """Move a cup on by its result, and say what it paid.

        A win takes the next round; a draw replays the same one; a loss or a
        walk-out ends the run and the cup starts again from round one. The
        final's prize is paid once, and the cup stays playable afterwards --
        this is offline, and a cup you can only ever win once is a cup that
        stops existing the moment you are good enough to win it.
        """
        identifier = int(identifier)
        rounds = TOURNAMENT_ROUNDS.get(identifier) or []
        entry = self.entries.get(identifier) or {}
        played = max(1, int(entry.get("round") or 1))
        final = len(rounds) or 1

        coins = 0
        if rounds:
            # The round table is (id, difficulty, multiplier, coins).
            coins = int(rounds[min(played, final) - 1][3])

        prize = 0
        if result == "WIN":
            if played >= final:
                prize = next(
                    (award for cup, _teams, _length, award, _trophy in TOURNAMENTS
                     if cup == identifier),
                    0,
                )
                nxt = 1                       # won it; the cup is playable again
            else:
                nxt = played + 1
        elif result == "DRAW":
            nxt = played                      # the round has not been settled
        elif result in ("LOSS", "QUIT", "DNF"):
            nxt = 1
        else:
            return {"tournamentId": identifier, "round": played, "roundCoins": 0,
                    "prize": 0, "settled": False}

        if entry:
            entry = dict(entry)
            entry["round"] = nxt
            self.entries[identifier] = entry
        else:
            self.apply(identifier, {"round": nxt})
        return {
            "tournamentId": identifier,
            "previousRound": played,
            "round": nxt,
            "roundCoins": coins if result in ("WIN", "DRAW") else 0,
            "prize": prize,
            "settled": True,
        }

    def delete(self, identifier: int) -> bool:
        return self.entries.pop(int(identifier), None) is not None

    @staticmethod
    def unplayed(entry: dict) -> bool:
        """A cup that was opened and walked out of before a ball was kicked.

        The client saves its draw the moment the bracket is built, before the
        first match. That save carries the full sixteen-team blob and a
        `progressData` of `AAAAAA==` -- four zero bytes, the length header of
        an empty payload -- at round one.

        Handing that back is what freezes the title. It froze twice on it: once
        on a reply carrying two extra members, and again on a reply byte for
        byte identical to what the client itself had PUT, so the document was
        never the problem. What the client cannot do is resume a run that has
        no first match to resume from.

        Nothing is lost by calling it no run: no match has been played, and
        the draw is redrawn on the way in. A run with a round past the first,
        or with a progress blob that actually holds something, is a real run
        and is kept.
        """
        try:
            blob = base64.b64decode(entry.get("progressData") or "", validate=False)
        except Exception:
            blob = b""
        started = len(blob) > 4 or any(blob[:4])
        return int(entry.get("round") or 1) <= 1 and not started

    def active_ids(self) -> list[int]:
        """Only runs the client can actually resume -- see `unplayed`."""
        return sorted(
            identifier
            for identifier, entry in self.entries.items()
            if not self.unplayed(entry)
        )

    def state(self) -> dict:
        return {str(key): value for key, value in self.entries.items()}

    def restore(self, saved: dict | None) -> None:
        for key, value in (saved or {}).items():
            if isinstance(value, dict):
                self.apply(int(key), value)


TOURNAMENT_PROGRESS = TournamentProgress()


class SeasonProgress:
    """A season under way, kept across launches.

    The route is one level deeper than the URL template table suggests. The
    table carries `ut/%s/season/%%s/user`, and `%%s` is not the season id: the
    format string beside the season serialiser is `%d/division/%d`, so what
    goes on the wire is

        PUT /ut/game/fifa14/season/1/division/10/user

    and that is exactly what the console sent on 13 August at 13:37:48, on
    starting a Saison Joueur Solo. It answered 404, which is a hang with
    nothing to read -- the same 404 the cups' `tournament/user/<id>` was
    getting before it was handled.

    The division in the path is the division's **number**, not the position
    `season/user` reports. The client reads `divisionId` out of the record it
    picked and puts that in the URL, which is how position 0 becomes
    `division/10`.

    The body is the cup's body with one word changed: `.rdata` carries
    `{"round":%d,"dataVersion":%d,"data":"` for seasons against
    `{"round":%d,"dataVersion":%d,"tournamentData":"` for cups, and they share
    the `","progressDataVersion":%d,"progressData":"` tail. So this is
    `TournamentProgress` keyed by a pair and spelling the blob `data`.
    """

    def __init__(self) -> None:
        self.entries: dict[tuple[int, int], dict] = {}

    @staticmethod
    def _key(season: int, division: int) -> tuple[int, int]:
        return (int(season), int(division))

    def apply(self, season: int, division: int, document: dict) -> dict:
        key = self._key(season, division)
        if not isinstance(document, dict):
            document = {}
        current = self.entries.get(key, {})

        def pick(*names, default=0):
            for name in names:
                if name in document:
                    return document[name]
            return current.get(names[0], default)

        entry = {
            "round": int(pick("round", default=1) or 1),
            "dataVersion": int(pick("dataVersion", default=1) or 1),
            "data": pick("data", "seasonData", default="") or "",
            "progressDataVersion": int(pick("progressDataVersion", default=1) or 1),
            "progressData": pick("progressData", "progressdata", default="") or "",
        }
        self.entries[key] = entry
        return entry

    def response(self, season: int, division: int) -> bytes:
        """Handed back exactly as the client wrote it, or as no season at all.

        `TournamentProgress` learned this the hard way: a run saved before the
        first match is one the client cannot resume, and answering it with the
        saved blob freezes the title. The season serialiser is the same
        serialiser, so the same rule applies here before it costs another
        evening -- an unplayed season is answered with nothing rather than
        with a bracket that has no first match behind it.
        """
        entry = self.entries.get(self._key(season, division))
        if entry is None or TournamentProgress.unplayed(
            {"round": entry["round"], "progressData": entry["progressData"]}
        ):
            return b"{}"
        return json.dumps(
            {
                "round": entry["round"],
                "dataVersion": entry["dataVersion"],
                "data": entry["data"],
                "progressDataVersion": entry["progressDataVersion"],
                "progressData": entry["progressData"],
            },
            separators=(",", ":"),
        ).encode()

    def reset(self, season: int, division: int) -> bool:
        return self.entries.pop(self._key(season, division), None) is not None

    def current(self) -> tuple[int, int] | None:
        """The season and division most recently written, if any."""
        if not self.entries:
            return None
        return next(reversed(list(self.entries)))

    def state(self) -> dict:
        return {f"{season}:{division}": value
                for (season, division), value in self.entries.items()}

    def restore(self, saved: dict | None) -> None:
        for key, value in (saved or {}).items():
            if not isinstance(value, dict):
                continue
            season, _, division = str(key).partition(":")
            try:
                self.apply(int(season), int(division or 0), value)
            except ValueError:
                continue


SEASON_PROGRESS = SeasonProgress()


def season_history_response(kind: str = "offline") -> bytes:
    """Seasons already finished, of which there are none.

    Asked for straight after a season is started, once per type -- `.rdata`
    carries `/season/user/history?type=offline`, `?type=online` and the two
    World Cup spellings -- and answered 404 until now.

    An empty object is what goes out, and deliberately: no season has ever
    been completed here, so any list this could carry would be invented. The
    same reading is what `season/user` was reduced to before the divisions
    were understood, and it is the answer the parser reads as "nothing yet"
    rather than as a document it cannot make sense of.
    """
    return b"{}"


# -- settling a match -------------------------------------------------------
#
# `/ut/game/fifa14/match/end` answered with three empty members and threw the
# result away: no coins for the match, no progress in the cup, nothing to show
# on the award screen. A club could play a Gold Cup final and finish it exactly
# as poor as it started.
#
# What the client posts is its own match stats. The names below are the ones
# FIFA 14 puts on the wire -- `goals`, `shotsOnTarget`, `passingPercentage`,
# `possessionPercentage` -- inside `myMatchStats` and `opponentMatchStats`.
# Everything is read defensively: a member that is not there is a zero, never
# an exception, because a settlement that raises loses the match.

# What a finished match pays before anything else. Retail scales it by how
# much of the match was actually played.
MATCH_COMPLETION_AWARD = 325

# Each statistic pays, and each is capped: a 9-0 win is worth more than a 1-0,
# and not nine times more.
MATCH_BONUS_CAPS = {
    "goals": (40, 200),
    "shotsOnTarget": (5, 75),
    "successfulTackles": (1, 20),
    "corners": (5, 50),
    "passAccuracy": (1, 80),
    "possession": (1, 80),
}
MATCH_PENALTY_CAPS = {
    "goalsAgainst": (20, 80),
    "fouls": (1, 20),
    "cards": (10, 80),
    "offsides": (1, 15),
}
CLEAN_SHEET_AWARD = 75
MOTM_AWARD = 15


def _stat(document: dict, *names: str, default: int = 0) -> int:
    """The first of these members the document carries, as an integer."""
    for name in names:
        if name in document:
            try:
                return int(document[name] or 0)
            except (TypeError, ValueError):
                continue
    return default


def match_reward(mine: dict, theirs: dict, minutes: int = 90,
                 multiplier: int = 1, completed: bool = True) -> dict:
    """What a match paid, itemised.

    Itemised because the award screen shows the parts: a completion award and
    a skill award, not one number. Returning the total alone would leave that
    screen with two thirds of it blank.
    """
    mine = mine if isinstance(mine, dict) else {}
    theirs = theirs if isinstance(theirs, dict) else {}
    minutes = max(0, min(90, int(minutes or 0)))

    goals = max(0, _stat(mine, "goals", "goalsFor", "goalsScored"))
    against = max(0, _stat(theirs, "goals", "goalsAgainst", "goalsConceded"))
    values = {
        "goals": goals,
        "shotsOnTarget": max(0, _stat(mine, "shotsOnTarget", "shotsontarget")),
        "successfulTackles": max(0, _stat(mine, "successfulTackles", "tacklesWon")),
        "corners": max(0, _stat(mine, "corners", "cornerKicks")),
        "passAccuracy": max(0, min(100, _stat(mine, "passingPercentage", "passAccuracy"))),
        "possession": max(0, min(100, _stat(mine, "possessionPercentage", "possession"))),
    }
    penalties_seen = {
        "goalsAgainst": against,
        "fouls": max(0, _stat(mine, "fouls")),
        "cards": max(0, _stat(mine, "yellowCards", "cards"))
        + max(0, _stat(mine, "redCards")),
        "offsides": max(0, _stat(mine, "offsides", "offside")),
    }

    bonuses = {
        name: min(values[name] * rate, cap)
        for name, (rate, cap) in MATCH_BONUS_CAPS.items()
    }
    # A clean sheet is only a clean sheet if there was a match to keep it in.
    if against == 0 and minutes >= 45:
        bonuses["cleanSheet"] = CLEAN_SHEET_AWARD
    if _stat(mine, "manOfTheMatch", "motm"):
        bonuses["manOfTheMatch"] = MOTM_AWARD
    penalties = {
        name: -min(penalties_seen[name] * rate, cap)
        for name, (rate, cap) in MATCH_PENALTY_CAPS.items()
    }

    completion = (
        int(round(MATCH_COMPLETION_AWARD * minutes / 90.0)) if completed else 0
    )
    skill_raw = sum(bonuses.values()) + sum(penalties.values())
    # An abandoned match pays nothing for skill. It still cost the contracts.
    skill = max(0, int(skill_raw * max(1, int(multiplier or 1)))) if completed else 0
    return {
        "minutesPlayed": minutes,
        "completed": bool(completed),
        "completionAward": completion,
        "skillAward": skill,
        "bonuses": bonuses,
        "penalties": penalties,
        "goalsFor": goals,
        "goalsAgainst": against,
        "totalCoins": max(0, completion + skill),
    }


# What the client calls the end of a match, and what each one means for a cup.
MATCH_RESULTS = {"WIN", "DRAW", "LOSS", "DNF", "QUIT", "NO_CONTEST", "FORFEIT"}


def match_result(document: dict) -> str:
    """WIN, DRAW, LOSS, DNF or QUIT, however the client spelled it.

    `endReason` is what the observed payload carries. A result nobody
    recognises settles as NO_CONTEST, which pays nothing and moves no cup --
    the safe reading of a message this server does not understand.
    """
    reason = str(document.get("endReason") or "").strip().upper()
    if reason == "FORFEIT":
        return "QUIT"
    if reason in MATCH_RESULTS:
        return reason
    goals = document.get("myMatchStats")
    others = document.get("opponentMatchStats")
    if isinstance(goals, dict) and isinstance(others, dict):
        mine, theirs = _stat(goals, "goals"), _stat(others, "goals")
        if mine > theirs:
            return "WIN"
        if mine < theirs:
            return "LOSS"
        return "DRAW"
    return "NO_CONTEST"


def apply_match_items(inventory: "ClubInventory", items: list) -> dict:
    """Write back what the match did to the eleven who played.

    The real `/match/end` body -- captured from this console on 11 August --
    carries an `items` array beside the two stat blocks:

        {"id": 1800000019, "fitness": 99}
        {"id": 1800000011, "fitness": 95, "assists": 1}
        {"id": 1800000018, "fitness": 96, "goals": 1}

    All of it was thrown away. Nobody ever lost fitness, which is why a fitness
    card had nothing to restore and the whole consumable pile was decoration:
    every player in the club sat at 99 for ever.

    `fitness` is written, not accumulated -- the client sends the value *after*
    the match, not the wear. Goals and assists are added up, because each
    payload carries only what happened in that one match.

    `goals`, `assists` and `lifetimeAssists` are members the cards already
    carry and the name table has. `lifetimeGoals` is in neither, so nothing is
    invented for it. `statsList` and `lifetimeStats` are index/value arrays
    whose indices are not established here, and they are left alone rather
    than written to on a guess about which index means what.
    """
    by_id = {item["id"]: item for item in inventory.items if item.get("id")}
    touched = {"fitness": 0, "goals": 0, "assists": 0, "unknown": []}
    for entry in items or []:
        if not isinstance(entry, dict):
            continue
        card = by_id.get(entry.get("id"))
        if card is None:
            touched["unknown"].append(entry.get("id"))
            continue
        if "fitness" in entry:
            try:
                card["fitness"] = max(0, min(99, int(entry["fitness"])))
                touched["fitness"] += 1
            except (TypeError, ValueError):
                pass
        for member, lifetime in (("goals", None), ("assists", "lifetimeAssists")):
            try:
                scored = int(entry.get(member) or 0)
            except (TypeError, ValueError):
                continue
            if scored <= 0:
                continue
            card[member] = int(card.get(member) or 0) + scored
            if lifetime:
                card[lifetime] = int(card.get(lifetime) or 0) + scored
            touched[member] += scored
    return touched


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
        # Fill the bench so the squad screen gets a full side rather than a
        # partial one -- but from the band the real Team of the Week is
        # actually in, not from the catalogue's best rares.
        #
        # Taking the best put a 98, a 98, a 98, a 97 and a 97 on the bench of a
        # side whose real members top out at 85. That is not a Team of the
        # Week, and it is not a fair opponent either: the challenge computes
        # `opponentRating` from the first eleven, so the padding decided how
        # strong the team you play against is.
        #
        # 78 to 86 when there is nothing real to measure against, which is
        # where an in-form side of this era sits.
        ratings = [card.get("rating", 0) for card in best] or [78, 86]
        floor, ceiling = max(60, min(ratings) - 2), max(ratings)
        seen = {card["assetId"] for card in best}
        average = sum(ratings) / len(ratings)
        candidates = [
            card
            for card in catalogue.cards
            if card.get("rareflag")
            and floor <= card.get("rating", 0) <= ceiling
            and card["assetId"] not in seen
        ]
        # Closest to the side's own average first, so the bench looks like the
        # team rather than like a shortlist.
        candidates.sort(key=lambda card: abs(card.get("rating", 0) - average))
        best += candidates[: size - len(best)]
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
# The club stats contexts: 2 is the year, 5 the new cards, 6 the consumables.
CONSUMABLE_STAT_CONTEXT = 6

CONSUMABLE_TOTALS = {
    "consumablesContract": "contract",
    "consumablesFitness": "fitness",
    "consumablesTraining": "training",
}


def consumable_stats_response(inventory: "ClubInventory") -> bytes:
    """How many of each consumable the club holds, under the real member names."""
    held: dict[str, int] = {member: 0 for member in CONSUMABLE_MEMBERS.values()}
    by_kind: dict[str, int] = {}
    # The member a card counts under comes from the catalogue, keyed by the
    # card's own database id. It used to travel on the item as
    # `consumableMember` -- a name CardsDLL does not carry, so the client
    # never read it and it had no business on the wire.
    definitions = _consumable_definitions()

    total = 0
    for item in inventory.items:
        if item.get("itemType") not in CONSUMABLE_TYPES:
            continue
        row = definitions.get(int(item.get("resourceId") or 0))
        if row is None:
            continue
        kind = row["itemType"]
        count = int(item.get("count") or 1)
        total += count
        by_kind[kind] = by_kind.get(kind, 0) + count
        member = row.get("member")
        if member:
            held[member] = held.get(member, 0) + count

    document = dict(held)
    for member, kind in CONSUMABLE_FALLBACKS.items():
        document[member] = by_kind.get(kind, 0)
    for name, kind in CONSUMABLE_TOTALS.items():
        document[name] = by_kind.get(kind, 0)
    document["consumables"] = total

    # The named scalars are not what the Apply Consumable popup reads.
    #
    # That screen is backed by a sticker-book stats response, and it binds its
    # consumable-type buttons from `stat`/`entries` rows in context 6 --
    # `{contextId, contextValue, type, typeValue}` -- not from members sitting
    # at the top level. Serving the scalars alone is why the popup reported
    # "Pas d'élément disponible" over a club holding 65 contracts, with every
    # scalar it could have read non-zero and every card present.
    #
    # All six member names are in CardsDLL, `StickerBook` with them. The
    # scalars stay: other screens do read those, and the two are the same
    # counts twice rather than a choice between them.
    entries = [
        {
            "contextId": CONSUMABLE_STAT_CONTEXT,
            "contextValue": 0,
            "type": member,
            "typeValue": int(count),
        }
        for member, count in sorted(document.items())
    ]
    document["stat"] = entries
    document["entries"] = entries
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





# The category the picker names in the path, mapped to what this server calls
# the family. `/ut/game/fifa14/club/consumables/contracts` -- plural -- and
# `/club/consumables/fitness`, and `/club/consumables/development`, which is
# the wire item type rather than a family and means every card of that type.
#
# Every name below is one the console has actually asked for, read off the
# journal rather than guessed: development, contracts, fitness, playStyle,
# healing, position, training, managerLeagueModifier.
CONSUMABLE_CATEGORIES = {
    "contract": "contract",
    "contracts": "contract",
    "fitness": "fitness",
    "healing": "healing",
    "training": "training",
    "playertraining": "training",
    "gktraining": "training",
    "playstyle": "playStyle",
    "chemistry": "playStyle",
    "chemistrystyle": "playStyle",
    "position": "position",
    "positioning": "position",
    # Asked for, and this club holds none: manager cards are left out of the
    # catalogue by tools/build_consumables.py. An empty tab is the truth.
    "managerleaguemodifier": "",
    "managerleague": "",
    "managercontract": "",
    "formationmanager": "",
}


def consumables_response(
    inventory: "ClubInventory", category: str = ""
) -> bytes:
    """The Apply Consumable picker asks here, one category at a time.

    `/ut/game/fifa14/club/consumables/contracts`, then `/fitness`, then
    `/development`. This route was matched with `startswith` and answered with
    every consumable the club owns whatever was asked for -- the picker asked
    for contracts and got 242 cards of every family mixed together, which is
    not a list of contracts however many contracts are in it.

    An unknown category returns everything, which is what the bare path means.
    """
    wanted = (category or "").strip().lower()
    family = CONSUMABLE_CATEGORIES.get(wanted)
    items = []
    for item in inventory.items:
        kind = item.get("itemType")
        if kind not in CONSUMABLE_TYPES:
            continue
        if not wanted:
            pass                                    # the bare path: everything
        elif family is not None:
            # A category this club has no cards for -- manager league, say --
            # maps to the empty string and matches nothing. Falling through to
            # "everything" instead is how a tab headed one thing lists another.
            if not family or consumable_family(item) != family:
                continue
        elif wanted in CONSUMABLE_TYPES:
            # `development` and `training` are the wire types themselves.
            if kind != wanted:
                continue
        else:
            # Named something this server does not know. Nothing, not
            # everything: a wrong list reads as a working screen.
            continue
        items.append(item)
    return json.dumps(
        {
            "itemData": items,
            "total": len(items),
            "count": len(items),
            "start": 0,
        },
        separators=(",", ":"),
    ).encode()


# What `/clubUser` carries besides the persona.
#
# Every consumable the club owns, individually. Not one row per definition
# with a count -- the PC revival appends "every owned consumable" and its
# picker reports 5 contracts for 5 cards, so the client counts rows -- and not
# a per-family sample either.
#
# The sample was the bug. Twelve cards a family sounds fair until you notice
# the families here span two subtype blocks each: `training` covers 51-57 and
# 61-67, `playStyle` covers 91-110 and 121-136, and the first twelve of either
# are all from the low block. Applying to a goalkeeper reads the keeper's
# block, and the keeper's block was not in the twelve. The screen said "Pas
# d'élément disponible" over a club holding 21 of them.
#
# All 242 come to about 68 KB, which fits under what the console was measured
# surviving. The players share what is left, because the route is the client's
# face-card cache bootstrap and the PC revival appends consumables *to* a
# player page rather than instead of one.
CLUB_USER_BUDGET = 74 * 1024

# How many copies of one subtype are worth sending. The picker offers a card
# to apply, not an inventory: three of a kind is as useful as sixty-five, and
# the room saved is what lets the players travel with them. The real count is
# what `club/stats/consumables` reports, and that stays truthful.
CLUB_USER_COPIES = 3


def club_user_response(inventory: "ClubInventory", name: str) -> bytes:
    """`/clubUser` -- the persona, and the cards the client binds against.

    `FutGetClubUsersServerResponse` reads a `user` array, singular, and that is
    all this used to answer: 122 bytes. The Apply Consumable picker binds here
    and never asks the server for more -- it reads the counts from
    `club/stats/consumables` and the cards from whatever it already holds.
    """
    players: list[dict] = []
    consumables: list[dict] = []
    for item in inventory.items:
        kind = item.get("itemType")
        if kind == "player":
            players.append(item)
        elif kind in CONSUMABLE_TYPES:
            consumables.append(item)

    def document(cards: list[dict]) -> bytes:
        return json.dumps(
            {
                "user": [{"persona": name, "personaId": PERSONA.id, "public": False}],
                "itemData": cards,
                "total": len(cards),
                "count": len(cards),
                "start": 0,
            },
            separators=(",", ":"),
        ).encode()

    # Dealt by subtype, one round at a time. Coverage first: every subtype the
    # club owns gets a card before any subtype gets a second, so the keeper's
    # training block cannot be missing while the outfield block is there four
    # times over. All 242 come to 79.7 KB, which is past what the console was
    # measured surviving, so something has to give -- and what gives is the
    # fourth copy of a card, never the only copy of a kind.
    #
    # Players take what is left. Grown by measurement rather than by an
    # assumed cost a card: a consumable runs about 330 bytes and a player
    # about 860, and both move whenever a member is added to either.
    queues: dict[int, list[dict]] = {}
    for item in consumables:
        queues.setdefault(item.get("cardsubtypeid"), []).append(item)

    ordered: list[dict] = []
    for _ in range(CLUB_USER_COPIES):
        for queue in queues.values():
            if queue:
                ordered.append(queue.pop(0))

    cards: list[dict] = []
    payload = document(cards)
    for card in ordered + players:
        candidate = document(cards + [card])
        if len(candidate) > CLUB_USER_BUDGET:
            if card in players:
                break
            continue
        cards.append(card)
        payload = candidate
    return payload


def totw_index(catalogue: "CardCatalogue | None" = None) -> bytes:
    """The list of Team of the Week squads available to view.

    The screen asks for the TOTW itself and then for this list, and a 404 here
    is what it reports as "aucune Équipe de la semaine disponible" -- the squad
    had already been served successfully.

    Every entry used to advertise `rating` 0. A squad with no rating is not a
    squad the screen can offer, and "aucune disponible" is what it says about a
    list it will not take. The rating is the real one now, worked out from the
    eleven the squad names.
    """
    squads = []
    if TOTW_FILE.exists():
        squads = json.loads(TOTW_FILE.read_text()).get("squads", [])
    by_asset = (
        {card["assetId"]: card for card in catalogue.cards} if catalogue else {}
    )

    def rating(squad: dict) -> int:
        eleven = [
            by_asset[asset]
            for asset in list(squad.get("assetIds") or [])[:11]
            if asset in by_asset
        ]
        if not eleven:
            return 0
        return round(sum(card.get("rating", 0) for card in eleven) / len(eleven))

    return json.dumps(
        {
            "squad": [
                {
                    "id": index + 1,
                    "squadName": squad.get("name", f"TOTW {index + 1}"),
                    "formation": FORMATION,
                    "rating": rating(squad),
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

# FIFA14_CLUB_SAVE points this somewhere else. The test suite sets it, and it
# has to: importing the server module builds a live club from the real save,
# and any route under test that writes wrote *that file*. A cup entered one
# evening was gone by the next launch because a test run in between had saved
# the club with the tournament table a test had just cleared. Nothing warned;
# the file simply came back smaller.
SAVE_FILE = Path(
    os.environ.get("FIFA14_CLUB_SAVE")
    or Path(__file__).resolve().parent.parent / "runtime" / "club-save.json"
)


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
        # Cards the club started with that a consumable has since changed.
        # Written in place, because the squad holds the same objects and
        # replacing them would leave the squad pointing at the old ones.
        for item in saved.get("changed", []):
            if not isinstance(item, dict):
                continue
            for held in inventory.items:
                if held["id"] == item.get("id"):
                    held.clear()
                    held.update(item)
                    break
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
        SEASON_PROGRESS.restore(saved.get("seasons"))
        CLUB_IDENTITY.restore(saved.get("club"))
        return True

    def save(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions", tasks: "ManagerTasks | None" = None) -> None:
        starting = ClubInventory()
        original = {item["id"] for item in starting.items}
        current = {item["id"] for item in inventory.items}
        # A card the club started with, changed since. `acquired` cannot carry
        # it -- it was never acquired -- and `sold` cannot either, because it
        # is still owned. Without this a contract applied to a seeded player
        # was spent (the consumable is in `sold`) and the contract it bought
        # was forgotten on the next launch.
        seeded = {item["id"]: item for item in starting.items}
        changed = [
            item
            for item in inventory.items
            if item["id"] in seeded and item != seeded[item["id"]]
        ]
        document = {
            "coins": wallet.coins,
            "changed": changed,
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
            "seasons": SEASON_PROGRESS.state(),
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
    index = json.loads(totw_index(catalogue))
    squad = json.loads(totw_response(catalogue))
    index["itemData"] = squad["itemData"]
    index["formation"] = squad["formation"]
    index["squadName"] = squad["squadName"]
    index["squadChallenge"] = squad["squadChallenge"]
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
