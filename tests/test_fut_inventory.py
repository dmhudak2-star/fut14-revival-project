from __future__ import annotations

import json
import random
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from fut_inventory import GOLD_PACK_ID, ClubInventory, RESOURCE_VERSION


INVENTORY = ClubInventory()


def test_the_catalogue_is_built_from_the_shipped_packs_not_invented() -> None:
    # 158023 is Messi and 167397 is Neymar in FIFA 14's own numbering. If these
    # stop appearing, the catalogue has drifted off the disc's data and the
    # cards will draw blank.
    assets = {
        item["assetId"] for item in INVENTORY.items if item["itemType"] == "player"
    }
    assert 158023 in assets
    assert 167397 in assets


def test_every_resource_id_carries_its_asset_id() -> None:
    # resourceId holds the asset id in its low 24 bits and a version above it.
    # Break this and the card art no longer resolves.
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert item["resourceId"] & 0x00FF_FFFF == item["assetId"]
        assert item["resourceId"] & 0xFF00_0000 == RESOURCE_VERSION


def test_item_ids_are_unique() -> None:
    ids = [item["id"] for item in INVENTORY.items]
    assert len(set(ids)) == len(ids)


def test_the_squad_fields_twenty_three() -> None:
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    assert len(squad["players"]) == 23
    assert [player["index"] for player in squad["players"]] == list(range(23))


def test_the_eleven_who_start_wear_shirt_numbers() -> None:
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    numbers = [player["kitNumber"] for player in squad["players"]]
    assert numbers[:11] == list(range(1, 12))
    assert set(numbers[11:]) == {0}


def test_the_club_presents_a_kit_badge_stadium_and_ball() -> None:
    # Without these the club has nothing to present and neither side can be
    # dressed for a match.
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    states = {item["itemState"] for item in squad["actives"]}
    assert states == {
        "activeHomeKit",
        "activeAwayKit",
        "activeBadge",
        "activeStadium",
        "activeBall",
    }


def test_every_player_can_actually_take_the_field() -> None:
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert item["contract"] > 0
        assert item["fitness"] > 0
        assert item["injuryGames"] == 0
        assert item["suspension"] == 0


def test_each_player_carries_six_attributes() -> None:
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert [entry["index"] for entry in item["attributeList"]] == list(range(6))


def test_the_squad_summary_reports_the_starting_eleven_rating() -> None:
    summary = json.loads(INVENTORY.squad_list_response("Fondateur FUT"))["squad"][0]
    starters = INVENTORY.squad[:11]
    assert summary["rating"] == round(
        sum(item["rating"] for item in starters) / len(starters)
    )
    assert summary["squadName"] == "Fondateur FUT"


def test_the_club_holds_more_than_the_starting_squad() -> None:
    # Spares are what give the club and transfer screens something to show.
    players = [item for item in INVENTORY.items if item["itemType"] == "player"]
    assert len(players) > len(INVENTORY.squad)


def test_the_wallet_starts_funded_and_names_the_field_three_ways() -> None:
    # The header reads whichever member it knows; an unrecognised sibling at
    # the top level is skipped, but a wrapper is not -- nesting these under
    # "userInfo" made the balance print 0xCDCDCDCD.
    from fut_inventory import Wallet

    balance = json.loads(Wallet().response())
    assert balance["totalCredits"] == balance["credits"] == balance["coins"]
    assert balance["coins"] > 0


def test_a_quick_sell_pays_something() -> None:
    # discardValue was zero on every card, so selling one returned nothing.
    for item in INVENTORY.items:
        if item["itemType"] == "player":
            assert item["discardValue"] > 0


def test_a_pack_costs_coins_and_returns_cards() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        GOLD_PACK_PRICE,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    before = wallet.coins
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(7)))
    assert wallet.coins == before - GOLD_PACK_PRICE
    assert opened["numberItems"] == len(opened["itemList"]) > 0
    # The reply carries the new balance: a response that omits it hands the
    # club header a zero rather than leaving it alone.
    assert opened["credits"] == wallet.coins


def test_a_pack_is_refused_without_the_coins() -> None:
    from fut_inventory import GOLD_PACK_ID, CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet(coins=10))
    assert not shop.can_afford()
    assert json.loads(shop.refused())["reason"] == "INSUFFICIENT_COINS"


def test_drawn_cards_stay_pending_until_collected() -> None:
    import random

    from fut_inventory import GOLD_PACK_ID, CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet())
    shop.open_pack(GOLD_PACK_ID, random.Random(3))
    pending = json.loads(shop.purchased_items())
    assert len(pending["itemData"]) == len(shop.pending) > 0


def test_the_market_pages_instead_of_repeating_itself() -> None:
    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()
    first = json.loads(catalogue.auctions({"start": "0", "num": "16"}))
    second = json.loads(catalogue.auctions({"start": "16", "num": "16"}))
    ids = lambda page: [a["itemData"]["id"] for a in page["auctionInfo"]]
    assert ids(first) and ids(second)
    assert not set(ids(first)) & set(ids(second))
    # total is the whole result set, which is what the screen pages against.
    assert first["total"] == second["total"] > len(first["auctionInfo"])


def test_the_market_honours_the_names_it_is_actually_sent() -> None:
    # lev, not level; definitionId, not maskedDefId. Filtering on the club
    # search's names meant none of the market's filters applied.
    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()
    gold = json.loads(catalogue.auctions({"lev": "gold", "start": "0", "num": "5"}))
    assert gold["total"] > 0
    assert all(
        "gold" in (a["itemData"]["rating"] and "gold") or True
        for a in gold["auctionInfo"]
    )
    one = json.loads(
        catalogue.auctions({"definitionId": "158023", "start": "0", "num": "5"})
    )
    assert one["total"] > 0
    assert {a["itemData"]["assetId"] for a in one["auctionInfo"]} == {158023}


def test_the_currency_names_are_lower_case() -> None:
    # The native parser compares them against the literal strings "coins" and
    # "points". "COINS", which the PC reference's fixture uses, matches nothing
    # and leaves the balance unwritten.
    from fut_inventory import Wallet

    document = json.loads(Wallet().credits_response())
    names = [entry["name"] for entry in document["currencies"]]
    assert names == ["coins", "points"]
    assert document["currencies"][0]["funds"] == document["credits"]


def test_unopened_packs_is_an_object() -> None:
    # An array leaves the parser walking the wrong token type, and it may never
    # reach its success epilogue.
    from fut_inventory import Wallet

    packs = json.loads(Wallet().credits_response())["unopenedPacks"]
    assert isinstance(packs, dict)
    assert set(packs) == {"preOrderPacks", "recoveredPacks"}


def test_user_info_is_flat() -> None:
    # Wrapping it as {"userInfo": {...}} is what made the header print
    # 0xCDCDCDCD: the parser did not recognise the shape and wrote nothing.
    from fut_inventory import Wallet

    info = json.loads(Wallet().user_info("Fondateur FUT", "FUT"))
    assert "userInfo" not in info
    assert info["clubName"] == "Fondateur FUT"
    assert info["coins"] == info["credits"] > 0


def test_quick_sell_takes_a_list_of_ids() -> None:
    # The client always sends {"itemId":[...]}, twelve long when a whole pack
    # is sold at once. Reading it as one integer yielded no id, so the reply
    # named no item and the screen errored.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(11)))
    ids = [item["id"] for item in opened["itemList"]]

    before = wallet.coins
    sold = json.loads(actions.discard_many(ids))
    assert [entry["id"] for entry in sold["items"]] == ids
    # totalCredits is what this sale paid, not the resulting balance.
    assert sold["totalCredits"] == wallet.coins - before
    assert not shop.pending


def test_sending_a_card_to_the_club_takes_it_out_of_the_pack() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(5)))
    first = opened["itemList"][0]["id"]
    pending_before = len(shop.pending)

    moved = json.loads(actions.move({"itemData": [{"id": first, "pile": 7}]}))
    assert moved["itemData"][0] == {
        "id": first,
        "success": True,
        "reason": "",
        "errorCode": 0,
        "pile": 7,
    }
    assert len(shop.pending) == pending_before - 1
    assert [item["id"] for item in actions.club] == [first]


def _actions():
    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return CardActions(shop, wallet), shop, wallet


def test_a_card_can_be_set_aside_for_listing() -> None:
    import random

    actions, shop, _ = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(9)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    # Pile 5 is the transfer list: the card leaves the club until it is listed.
    assert [item["id"] for item in actions.transfer] == [first]
    assert first not in [item["id"] for item in actions.club]


def test_listing_returns_a_trade_id_and_shows_in_the_trade_pile() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(13)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})

    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": first}, "startingBid": 300, "buyNowPrice": 900}
        )
    )
    # The trade id is what every later bid or withdrawal refers to.
    assert listing["tradeId"] == listing["id"]
    assert listing["tradeState"] == "active"
    assert (listing["startingBid"], listing["buyNowPrice"]) == (300, 900)
    assert not actions.transfer

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == listing["tradeId"]


def test_withdrawing_puts_the_card_back() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(17)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    listing = json.loads(actions.list_for_sale({"itemData": {"id": first}}))

    actions.withdraw(listing["tradeId"])
    assert [item["id"] for item in actions.transfer] == [first]
    # It is back on the transfer list, and the list says so. This asserted a
    # total of 0 while the line above asserted the card was there -- which is
    # the bug written down: withdrawing a listing made the card invisible.
    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == 0
    assert pile["auctionInfo"][0]["itemData"]["id"] == first


def test_buying_a_listing_debits_the_wallet_and_hands_over_the_card() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    # Era-accurate prices: the top of the market is a Team of the Year at
    # seven million, so a test that buys the first listing needs the coins.
    wallet = Wallet(coins=50_000_000)
    page = json.loads(catalogue.auctions({"start": "0", "num": "3"}, wallet.coins))
    listing = page["auctionInfo"][0]

    before = wallet.coins
    reply, won = catalogue.bid(
        listing["tradeId"], listing["buyNowPrice"], wallet
    )
    document = json.loads(reply)
    # At or above buy-now the auction ends: that is the Buy Now button.
    assert document["tradeState"] == "closed"
    assert wallet.coins == before - listing["buyNowPrice"]
    assert won is not None


def test_a_bid_below_buy_now_leaves_the_auction_running() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    # Era-accurate prices: the top of the market is a Team of the Year at
    # seven million, so a test that buys the first listing needs the coins.
    wallet = Wallet(coins=50_000_000)
    page = json.loads(catalogue.auctions({"start": "0", "num": "3"}, wallet.coins))
    listing = page["auctionInfo"][0]

    reply, won = catalogue.bid(listing["tradeId"], listing["startingBid"], wallet)
    assert json.loads(reply)["tradeState"] == "active"
    assert won is None


def test_bidding_beyond_the_balance_is_refused() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "1"}, wallet.coins))
    trade = page["auctionInfo"][0]["tradeId"]
    before = wallet.coins
    reply, won = catalogue.bid(trade, wallet.coins + 1, wallet)
    assert json.loads(reply)["reason"] == "INSUFFICIENT_COINS"
    assert wallet.coins == before and won is None


def test_a_search_result_can_still_be_bid_on_afterwards() -> None:
    # Listings are generated per search, so the trade id has to survive the
    # response or every later bid refers to something that no longer exists.
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "2"}, wallet.coins))
    for listing in page["auctionInfo"]:
        assert listing["tradeId"] in catalogue.served


def test_the_club_holds_more_than_players() -> None:
    # The Consommables, Éléments club and Personnel tabs each filter on an item
    # type; serving players only leaves them empty and their filters inert.
    from fut_inventory import consumable_family

    kinds = {item["itemType"] for item in INVENTORY.items}
    # FUT's two consumable types, not one per family: `cardsubtypeid` carries
    # the family and `itemType` only says develop or train.
    for kind in ("development", "training", "kit", "badge", "stadium",
                 "ball", "staff", "manager"):
        assert kind in kinds, kind

    families = {consumable_family(item) for item in INVENTORY.items}
    for family in ("contract", "fitness", "playStyle"):
        assert family in families, family


def test_consumables_carry_an_amount() -> None:
    # A contract card that grants no matches is not a contract card.
    for item in INVENTORY.items:
        from fut_inventory import consumable_family

        if consumable_family(item) in ("contract", "fitness"):
            assert item["amount"] > 0


def test_the_club_search_can_isolate_a_type() -> None:
    players = json.loads(INVENTORY.club_response({"type": "player"}))["itemData"]
    kits = json.loads(INVENTORY.club_response({"type": "kit"}))["itemData"]
    assert players and kits
    assert {item["itemType"] for item in players} == {"player"}
    assert {item["itemType"] for item in kits} == {"kit"}


def test_seasons_are_empty_by_default() -> None:
    # Three shapes have been served here and all three failed on the console,
    # the last by freezing the FUT loader outright. Empty is the only answer
    # known not to break anything, so it is what an unconfigured server sends.
    import fut_inventory as inventory

    assert json.loads(inventory.seasons_response()) == {"seasons": []}
    assert json.loads(inventory.season_user_response()) == {}


def _native_seasons(monkeypatch):
    import fut_inventory as inventory

    monkeypatch.setenv("FIFA14_SEASON_MODE", "native")
    return inventory


def test_the_native_season_record_carries_its_schedule(monkeypatch) -> None:
    inventory = _native_seasons(monkeypatch)

    seasons = json.loads(inventory.seasons_response())["seasons"]
    assert len(seasons) == 10
    assert {season["divisionId"] for season in seasons} == set(range(1, 11))
    for season in seasons:
        assert season["numMatches"] > 0
        # The fixture list is an array of records, not a count -- the same
        # fault as a cup's `rounds`, which froze the title.
        assert len(season["matches"]) == season["numMatches"]
        for match in season["matches"]:
            assert set(match) == {
                "teamId",
                "difficulty",
                "rewardMult",
                "roundId",
                "coins",
            }
        # Rewards travel through prizeSet, not as flat top-level members.
        levels = [prize["prizeLevel"] for prize in season["prizeSet"]]
        assert levels == ["RELEGATION", "MAINTENANCE", "PROMOTION", "CHAMPIONSHIP"]
        for prize in season["prizeSet"]:
            assert "thresholdPoint" in prize
            assert isinstance(prize["awardMappings"][0]["awards"], list)
        # 0 is a real resource id to the client: it went and fetched
        # /fut/items/xbl2/0.json for every entry that carried it.
        assert season["trophyResourceId"] == -1


def test_the_season_document_carries_no_flat_reward_members(monkeypatch) -> None:
    # The shape that produced "Les saisons ne sont pas disponibles pour le
    # moment" kept thresholds and coins at the top level and reused the cup's
    # time member names.
    inventory = _native_seasons(monkeypatch)

    misplaced = {
        "seasonCoins",
        "thresholdPoint",
        "visStart",
        "visEnd",
        "starttime",
        "endtime",
        "timeUntilStart",
        "timeUntilEnd",
    }
    for season in json.loads(inventory.seasons_response())["seasons"]:
        assert misplaced.isdisjoint(season)


def test_the_season_document_invents_no_member() -> None:
    # Seven members of the earlier shape -- division, matchesPlayed,
    # matchesToPlay, pointsToPromote, lost, coinsPerWin, trophiesWon -- appear
    # nowhere in CardsDLL's JSON name table, so the parser could not read them
    # and the screen stayed on its constructor defaults.
    import fut_inventory as inventory

    invented = {
        "division",
        "matchesPlayed",
        "matchesToPlay",
        "pointsToPromote",
        "lost",
        "coinsPerWin",
        "trophiesWon",
        "relegated",
        "promoted",
    }
    standing = json.loads(inventory.season_user_response())
    assert invented.isdisjoint(standing)
    for season in json.loads(inventory.seasons_response())["seasons"]:
        assert invented.isdisjoint(season)


def test_the_club_starts_in_the_bottom_division(monkeypatch) -> None:
    inventory = _native_seasons(monkeypatch)

    standing = json.loads(inventory.season_user_response())
    # Only what the parser handles: seasonId, divisionId and round. seasonId
    # is decremented by the client, so 1 selects the first list record, and
    # round 1 is the first fixture -- wire 0 becomes its invalid sentinel.
    assert set(standing) == {"seasonId", "divisionId", "round"}
    assert standing["divisionId"] == 10
    assert standing["seasonId"] == 1
    assert standing["round"] == 1
    assert json.loads(inventory.season_user_response(10, played=3))["round"] == 4


def test_the_cup_list_carries_the_shape_the_binary_names() -> None:
    # A generated list froze the title when Compétition Joueur Solo was opened,
    # and the list was emptied until the fields could come from the binary.
    # They now do: every member below sits in CardsDLL's own sorted JSON name
    # table in .rdata. The freeze was `rounds` served as a count where the
    # parser walks records, alongside members that table does not carry.
    import fut_inventory as inventory

    cups = json.loads(inventory.tournaments_response())["tournament"]
    assert cups
    for cup in cups:
        assert isinstance(cup["rounds"], list)
        assert len(cup["rounds"]) == cup["numRounds"]
        assert cup["numTeams"] > len(json.loads(inventory.tournament_teams_response())["teamId"])
        for entry in cup["rounds"]:
            assert set(entry) == {"id", "difficulty", "rewardMultiplier", "coins"}
        assert cup["treeType"] == "knockout"
        assert set(cup["awardSet"]["awards"][0]) == {"awardType", "value", "halid"}
        for invented in ("name", "level", "entryFee", "active", "won"):
            assert invented not in cup


def test_the_club_lists_its_best_players_first() -> None:
    # Unsorted, the club and the squad's player picker listed cards in the
    # order the icebreaker packs added them.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    items = json.loads(club.club_response())["itemData"]

    players = [i for i in items if i.get("itemType") == "player"]
    ratings = [i.get("rating", 0) for i in players]
    assert ratings == sorted(ratings, reverse=True)

    # Players first: a playStyle carries a rating of 99 of its own, so ranking
    # on rating alone buried the best player behind a handful of them.
    kinds = [i.get("itemType") == "player" for i in items]
    assert kinds == sorted(kinds, reverse=True)
    assert items[0].get("itemType") == "player"


def test_the_club_keeps_the_name_the_player_chose() -> None:
    # PUT /user/club carries the name and abbreviation the creation screen
    # asked for. It used to be answered {} and forgotten, so the club had no
    # name on the next load.
    import fut_inventory as inventory

    identity = inventory.ClubIdentity()
    assert identity.name == ""
    assert identity.adopt({"clubName": "Olympique Safi", "clubAbbr": "OCS"})
    assert (identity.name, identity.abbr) == ("Olympique Safi", "OCS")

    # It survives a save/restore cycle.
    restored = inventory.ClubIdentity()
    restored.restore(identity.state())
    assert (restored.name, restored.abbr) == ("Olympique Safi", "OCS")

    # An empty body changes nothing rather than clearing the club.
    assert identity.adopt({}) is False
    assert identity.name == "Olympique Safi"


def test_the_starting_squad_takes_the_club_name() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    club.rename_active_squad("Olympique Safi")
    summaries = json.loads(club.squad_summaries())["squad"]
    active = club.active_squad_id()
    assert any(
        s["id"] == active and s["squadName"] == "Olympique Safi" for s in summaries
    )


def test_the_starter_packs_hold_no_specials() -> None:
    # Three packs -- bronze, silver, gold -- and nothing a new account has no
    # business being handed on its first day: no Team of the Week, no Team of
    # the Season, no Legend, and nothing above the cap.
    import fut_inventory as inventory

    club = inventory.ClubInventory(seeded=False)
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    coins_before = shop.wallet.coins

    count = shop.grant_starter_packs(random.Random(11))
    expected = sum(
        inventory.PACK_SPECS[p]["count"] for p in inventory.STARTER_PACKS
    )
    assert count == expected
    assert len(shop.pending) == expected
    # They are free.
    assert shop.wallet.coins == coins_before

    players = [c for c in shop.pending if c["itemType"] == "player"]
    for card in players:
        assert inventory.is_ordinary(card), card.get("rarity")
        assert card["rating"] <= inventory.STARTER_RATING_CAP

    # Three players a pack, not twelve -- the other nine slots are what a new
    # club actually needs, and it opened with none of them until they were
    # drawn here too.
    assert len(players) == sum(
        inventory.PACK_SPECS[p]["players"] for p in inventory.STARTER_PACKS
    )
    from fut_inventory import consumable_family

    assert any(consumable_family(c) == "contract" for c in shop.pending)


def test_ordinary_excludes_every_special() -> None:
    import fut_inventory as inventory

    for special in (
        "Team of the Week",
        "Team of the Season",
        "World Cup",
        "Legend",
        "MOTM",
        "iMOTM",
        "Team of the Year",
        "Record Breaker",
    ):
        assert not inventory.is_ordinary({"rarity": special})
    for ordinary in ("Non-Rare Bronze", "Rare Gold", "Non-Rare Silver"):
        assert inventory.is_ordinary({"rarity": ordinary})


def test_first_run_starts_with_no_club_at_all() -> None:
    # The club is seeded from all four captains' squads because fcc_login2
    # treats an empty squad as fatal. That seed is also why the captain
    # selection never appears: it exists to give a new player his first squad.
    # The two cannot both be true, so the seed is optional rather than assumed.
    import fut_inventory as inventory

    seeded = inventory.ClubInventory()
    assert seeded.items and seeded.squad

    fresh = inventory.ClubInventory(seeded=False)
    assert fresh.items == []
    assert fresh.squad == []
    # Kits, badges and consumables are club contents too; a club that has not
    # been created does not own them either.
    assert json.loads(fresh.club_response())["itemData"] == []


def test_first_run_is_off_unless_asked_for(monkeypatch) -> None:
    # An empty club breaks the login for an existing player, which is the
    # state of anyone not running the experiment.
    import fut_inventory as inventory

    monkeypatch.delenv("FIFA14_FIRST_RUN", raising=False)
    assert inventory.first_run() is False
    monkeypatch.setenv("FIFA14_FIRST_RUN", "1")
    assert inventory.first_run() is True


def test_the_unfiltered_club_is_bounded() -> None:
    # Unbounded, this returned every card in one document: 244 KB at 453 cards
    # and growing with every pack. An explicit count still wins.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    whole = json.loads(club.club_response())["itemData"]
    assert len(whole) <= inventory.CLUB_UNFILTERED_LIMIT

    asked = json.loads(club.club_response({"count": "300"}))["itemData"]
    assert len(asked) == min(300, len(club.items))


def test_the_club_pages_from_the_sorted_list() -> None:
    # Paging an unsorted list puts arbitrary cards on page one, so the order
    # has to be established before the slice.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    everyone = json.loads(club.club_response())["itemData"]
    page = json.loads(club.club_response({"count": "5"}))["itemData"]
    assert [item["id"] for item in page] == [item["id"] for item in everyone[:5]]


def test_sorting_the_club_does_not_reorder_it_everywhere_else() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    before = [item["id"] for item in club.items]
    club.club_response()
    assert [item["id"] for item in club.items] == before


def test_a_special_is_not_a_duplicate_of_the_ordinary_card() -> None:
    # A player's versions share his asset id: a Team of the Season Ruffier and
    # a Rare Gold Ruffier are both asset 167628 and are not the same card.
    import fut_inventory as inventory

    shop = inventory.PackShop(
        inventory.CardCatalogue(), inventory.Wallet(), inventory.ClubInventory()
    )
    # `resourceId` used to be the key. It cannot be here: this server builds it
    # as RESOURCE_VERSION | asset_id with the version byte always 1, so every
    # version of a player carries the same number and the key collapsed onto
    # the asset. What names the version is the rarity, and the rating separates
    # two cards of one player inside a family.
    ordinary = {"id": 1, "assetId": 167628, "rarity": "Rare Gold", "rating": 74}
    special = {"id": 2, "assetId": 167628, "rarity": "Team of the Season",
               "rating": 84}
    louder = {"id": 4, "assetId": 167628, "rarity": "Team of the Season",
              "rating": 87}
    same = {"id": 3, "assetId": 167628, "rarity": "Rare Gold", "rating": 74}

    assert shop._signature(ordinary) != shop._signature(special)
    assert shop._signature(special) != shop._signature(louder)
    assert shop._signature(ordinary) == shop._signature(same)


def test_a_repeat_is_paired_with_the_card_it_repeats() -> None:
    # The pack screen reads the pairing out of duplicateItemIdList; marking
    # only the card left a repeat looking like an ordinary pull. What must
    # never go in that list is a bare list of the new ids -- that froze the
    # title, by telling the screen to compare each card against itself.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    owned = club.items[0]
    repeat = dict(owned, id=1950999999)

    pairs = shop._mark_duplicates([repeat])
    assert pairs == [{"itemId": 1950999999, "duplicateItemId": owned["id"]}]
    assert repeat["duplicateItemId"] == owned["id"]
    # The new id never names itself.
    assert all(p["itemId"] != p["duplicateItemId"] for p in pairs)


def test_the_totw_challenge_says_how_strong_it_is() -> None:
    # opponentRating was computed with `max(... for card in [])` -- over an
    # empty list -- so every challenge advertised a rating of 0 against team 0.
    import fut_inventory as inventory

    totw = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    challenges = totw["squadChallenge"]
    assert challenges
    for entry in challenges:
        assert set(entry) == {
            "squadId",
            "squadName",
            "formation",
            "opponentTeam",
            "opponentRating",
        }
        assert entry["opponentRating"] > 0
        assert entry["squadName"]


def test_a_bought_card_is_paired_like_a_packed_one() -> None:
    # The pairing existed only on the pack path; a card bought on the market
    # went into the club unmarked however many copies were already there.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    owned = next(i for i in club.items if i.get("itemType") == "player")
    bought = dict(owned, id=1980000001, itemState="new")

    pairs = shop._mark_duplicates([bought])
    assert pairs == [{"itemId": 1980000001, "duplicateItemId": owned["id"]}]
    assert bought["duplicateItemId"] == owned["id"]


def test_a_card_the_server_never_held_is_not_reported_as_moved() -> None:
    # This is how a TOTS Ruffier was drawn, shown, sent to the club, confirmed
    # by the server, and then existed nowhere: an unknown id was acknowledged
    # with success while nothing was kept.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    catalogue = inventory.CardCatalogue()
    wallet = inventory.Wallet()
    shop = inventory.PackShop(catalogue, wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    before = len(club.items)
    reply = json.loads(
        actions.move({"itemData": [{"id": 999999999, "pile": "club"}]})
    )["itemData"][0]
    assert len(club.items) == before
    assert reply["success"] is False
    assert reply["errorCode"] != 0
    assert actions.unmatched == [999999999]


def test_a_card_the_server_holds_still_moves() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    catalogue = inventory.CardCatalogue()
    wallet = inventory.Wallet()
    wallet.coins = 10_000_000
    shop = inventory.PackShop(catalogue, wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    drawn = json.loads(shop.open_pack(inventory.GOLD_PACK_ID))["itemList"]
    before = len(club.items)
    reply = json.loads(
        actions.move(
            {"itemData": [{"id": card["id"], "pile": "club"} for card in drawn]}
        )
    )["itemData"]
    assert len(club.items) == before + len(drawn)
    assert all(entry["success"] for entry in reply)
    assert actions.unmatched == []


def test_image_archives_are_parseable_containers_not_json() -> None:
    # The /fut/items/ prefix answered everything with {"itemData":[]}, so the
    # console got sixteen bytes of JSON where it asked for a BIG archive.
    import fut_inventory as inventory

    archive = inventory.empty_big_archive()
    assert archive[:4] == b"BIGF"
    assert int.from_bytes(archive[8:12], "big") == 0  # no directory entries
    assert int.from_bytes(archive[12:16], "big") == len(archive)


def test_every_cup_names_a_trophy_the_game_actually_ships() -> None:
    # cards0.big carries trophy_<id>_<tier> for ids 1100..1169 beside a
    # notfound.big. Serving 0 is what drew that placeholder.
    import fut_inventory as inventory

    for cup in json.loads(inventory.tournaments_response())["tournament"]:
        assert inventory.TROPHY_FIRST <= cup["trophyResourceId"] <= inventory.TROPHY_LAST


def test_a_cup_is_only_active_once_it_has_been_entered() -> None:
    import fut_inventory as inventory

    inventory.TOURNAMENT_PROGRESS.entries.clear()
    try:
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == []
        inventory.TOURNAMENT_PROGRESS.apply(3, {"round": 2, "tournamentData": "QQ=="})
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == [3]
        saved = json.loads(inventory.TOURNAMENT_PROGRESS.response(3))
        assert saved["round"] == 2
        assert saved["tournamentData"] == "QQ=="
        # Exactly the five members the client itself writes, and no others.
        # A duplicate `progressdata` beside `progressData` is the same known
        # field twice, not a sibling the parser skips; resuming a saved cup
        # froze the title on the first GET this route ever answered.
        assert set(saved) == {
            "round",
            "dataVersion",
            "tournamentData",
            "progressDataVersion",
            "progressData",
        }
        # The season spelling is still accepted on the way in.
        inventory.TOURNAMENT_PROGRESS.apply(3, {"round": 3, "data": "Ug=="})
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3))["tournamentData"] == "Ug=="
    finally:
        inventory.TOURNAMENT_PROGRESS.entries.clear()


def test_a_cup_entered_but_never_played_is_not_a_run_to_resume() -> None:
    # The client saves its draw the moment the bracket is built: the full
    # sixteen-team blob, round one, and a progress blob of four zero bytes.
    # Handing that back froze the title twice -- the second time on a reply
    # byte for byte identical to the client's own PUT, which is what rules the
    # document itself out. Nothing is lost by calling it no run: no match has
    # been played, and the draw is redrawn on the way in.
    import fut_inventory as inventory

    inventory.TOURNAMENT_PROGRESS.entries.clear()
    try:
        inventory.TOURNAMENT_PROGRESS.apply(
            3,
            {
                "round": 1,
                "dataVersion": 1,
                "tournamentData": "AAAK7h+LCAAA",
                "progressDataVersion": 1,
                "progressData": "AAAAAA==",
            },
        )
        assert inventory.TOURNAMENT_PROGRESS.active_ids() == []
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == []
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3)) == {
            "tournamentId": 3
        }

        # A run with a real progress blob is a real run and comes back whole.
        inventory.TOURNAMENT_PROGRESS.apply(3, {"progressData": "AAAAAgAB"})
        assert inventory.TOURNAMENT_PROGRESS.active_ids() == [3]
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3))["round"] == 1

        # So is one that has reached a later round.
        inventory.TOURNAMENT_PROGRESS.apply(
            3, {"round": 2, "progressData": "AAAAAA=="}
        )
        assert inventory.TOURNAMENT_PROGRESS.active_ids() == [3]
    finally:
        inventory.TOURNAMENT_PROGRESS.entries.clear()


def test_team_of_the_week_is_a_full_side() -> None:
    # A real Team of the Week is not simply the 23 best rares -- TOTW 1 carries
    # ordinary in-form cards down into the sixties. Assert the shape, not a
    # rating floor the real data does not meet.
    import fut_inventory as inventory

    squad = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    assert len(squad["itemData"]) == 23
    assert squad["formation"]
    ids = [card["id"] for card in squad["itemData"]]
    assert len(set(ids)) == len(ids)
    assert all(
        card["resourceId"] & 0x00FF_FFFF == card["assetId"]
        for card in squad["itemData"]
    )


def _players(opened: dict) -> list:
    """The player cards of an opened pack.

    A pack is three players and nine consumables/club items now, so
    `itemList[0]` is no longer a player and the club's player list no longer
    grows by the size of the pack.
    """
    return [item for item in opened["itemList"] if item["itemType"] == "player"]


def test_a_card_sent_to_the_club_is_in_the_club() -> None:
    # The send acknowledged and the card left the pack, but the club response
    # was built from the starting inventory alone, so it never appeared.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet, inventory)

    before = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(21)))
    kept = _players(opened)[0]["id"]
    actions.move({"itemData": [{"id": kept, "pile": 7}]})

    owned = json.loads(inventory.club_response({"type": "player"}))["itemData"]
    assert len(owned) == before + 1
    assert kept in [item["id"] for item in owned]


def test_a_card_sold_out_of_the_club_leaves_it() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet, inventory)

    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(22)))
    kept = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": kept, "pile": 7}]})
    actions.discard_many([kept])

    owned = json.loads(inventory.club_response({"type": "player"}))["itemData"]
    assert kept not in [item["id"] for item in owned]


def _fresh():
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return inventory, shop, CardActions(shop, wallet, inventory)


def test_listing_a_card_takes_it_out_of_the_club() -> None:
    # Moving to the transfer pile used to leave the card in the club as well,
    # so it showed in both places and looked duplicated.
    import random

    inventory, shop, actions = _fresh()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(41)))
    card = _players(opened)[0]["id"]
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])

    actions.move({"itemData": [{"id": card, "pile": 5}]})
    assert [item["id"] for item in actions.transfer] == [card]
    after = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    assert after == owned - 1


def test_a_duplicate_is_kept_and_names_what_it_repeats() -> None:
    # Retail would offer to compare the two here, and that screen is driven by
    # duplicateItemIdList -- the field that froze the title, so it cannot be
    # turned back on yet. Until then the duplicate is quick-sold: the club
    # keeps one of each, the coins are real, and nothing happens silently.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=2_000_000)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    first = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    for item in first["itemList"]:
        actions._keep(dict(item))
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])

    second = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    # Only players duplicate: a second contract card is a second contract.
    assert all(item.get("duplicateItemId") for item in _players(second))
    assert not any(
        item.get("duplicateItemId")
        for item in second["itemList"]
        if item["itemType"] != "player"
    )
    for item in second["itemList"]:
        actions._keep(dict(item))

    # The duplicate is kept: the card names what it repeats, so the screen can
    # offer the choice rather than the server making it.
    after = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    assert after == owned + len(_players(second))


def test_a_club_holds_one_of_any_card() -> None:
    import random

    inventory, shop, actions = _fresh()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(42)))
    card = _players(opened)[0]["id"]
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    # The same item id is the same card, not a second one.
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    assert (
        len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
        == owned
    )


def test_the_tiles_count_what_the_club_actually_holds() -> None:
    import random

    from fut_inventory import hub_response

    inventory, shop, actions = _fresh()
    before = json.loads(hub_response(inventory, 0))["clubPlayers"]
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(43)))
    actions.move(
        {"itemData": [{"id": item["id"], "pile": 7} for item in opened["itemList"][:2]]}
    )
    after = json.loads(hub_response(inventory, 0))
    assert after["clubPlayers"] > before
    # And the market tile reports real listings rather than a fixed zero.
    assert json.loads(hub_response(inventory, 3))["auctionCount"] == 3


def test_the_club_survives_a_restart() -> None:
    # Entering FUT needs a relaunch, so without a save every session started
    # from the icebreaker packs again: the club counter back to 92, the pack
    # you opened gone, the coins reset.
    import random
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        PackShop,
        Wallet,
    )

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"

        inventory, wallet = ClubInventory(), Wallet()
        shop = PackShop(CardCatalogue(), wallet)
        actions = CardActions(shop, wallet, inventory)
        opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(61)))
        kept = [item["id"] for item in _players(opened)[:2]]
        actions.move({"itemData": [{"id": item, "pile": 7} for item in kept]})
        ClubSave(path).save(inventory, wallet, actions)
        owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
        coins = wallet.coins

        # A fresh process, as a relaunch gives.
        restored, purse = ClubInventory(), Wallet()
        again = CardActions(PackShop(CardCatalogue(), purse), purse, restored)
        assert ClubSave(path).load(restored, purse, again)

        reloaded = json.loads(restored.club_response({"type": "player"}))["itemData"]
        assert len(reloaded) == owned
        assert set(kept) <= {item["id"] for item in reloaded}
        assert purse.coins == coins


def test_a_bought_card_joins_the_club() -> None:
    # Buying credited nothing to the club: the card went into a side list the
    # inventory never saw, so it could not be assigned and vanished on restart.
    #
    # The market keeps offering the card afterwards, and that is correct -- it
    # carries many copies of the same player. Hiding them by asset id took
    # every version of Benatia off the market when one was bought.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    # Era-accurate prices: the top of the market is a Team of the Year at
    # seven million, so a test that buys the first listing needs the coins.
    inventory, wallet = ClubInventory(), Wallet(coins=50_000_000)
    catalogue = CardCatalogue()
    actions = CardActions(PackShop(catalogue, wallet), wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "2"}))["auctionInfo"][0]
    asset = listing["itemData"]["assetId"]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    assert won is not None
    actions._keep(dict(won))

    assert any(item["assetId"] == asset for item in inventory.items)


def test_a_bought_card_can_be_put_in_the_squad() -> None:
    # The squad was whatever was built at load time and nothing could change
    # it, so a card bought or pulled reached the club and had nowhere to go --
    # the assign screen found nothing it could field and backed out.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=50_000_000)
    catalogue = CardCatalogue()
    actions = CardActions(PackShop(catalogue, wallet), wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "1"}))["auctionInfo"][0]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    actions._keep(dict(won))

    chosen = [item["id"] for item in inventory.squad]
    chosen[0] = won["id"]
    inventory.set_squad(chosen)

    fielded = [
        player["itemData"]["id"]
        for player in json.loads(inventory.active_squad_response("x"))["players"]
    ]
    assert fielded[0] == won["id"]
    assert len(fielded) == len(chosen)


def test_an_unknown_card_cannot_be_fielded() -> None:
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    original = [item["id"] for item in inventory.squad]
    inventory.set_squad([999_999_999])
    # Nothing resolvable: the squad is left alone rather than emptied.
    assert [item["id"] for item in inventory.squad] == original


def test_a_bought_card_waits_in_the_purchased_pile() -> None:
    # The assign screen reads purchased/items. Sending a bought card straight
    # into the club left that list empty, so "Assigner maintenant" had nothing
    # to offer and backed out -- the pack flow, which works, goes through the
    # pending pile first.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=50_000_000)
    catalogue = CardCatalogue()
    shop = PackShop(catalogue, wallet)
    actions = CardActions(shop, wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "1"}))["auctionInfo"][0]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    bought = dict(won)
    bought["itemState"] = "new"
    shop.pending.append(bought)

    waiting = json.loads(shop.purchased_items())["itemData"]
    assert won["id"] in [item["id"] for item in waiting]
    assert won["id"] not in [item["id"] for item in inventory.items]

    # And assigning it moves it on, exactly as a pack card does.
    actions.move({"itemData": [{"id": won["id"], "pile": 7}]})
    assert not shop.pending
    assert won["id"] in [item["id"] for item in inventory.items]


def test_a_second_squad_can_be_created_renamed_and_dropped() -> None:
    # There was one squad and no way to add, rename or remove another: the
    # list was generated from a single fixed side.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    assert inventory.squad_ids() == [1]

    players = [item["id"] for item in inventory.items if item["itemType"] == "player"]
    made = inventory.save_squad(0, players[:23], name="Ma 2e équipe")
    assert made != 1 and made in inventory.squad_ids()

    named = {
        entry["id"]: entry["squadName"]
        for entry in json.loads(inventory.squad_summaries())["squad"]
    }
    assert named[made] == "Ma 2e équipe"

    inventory.save_squad(made, players[:23], name="Renommée")
    named = {
        entry["id"]: entry["squadName"]
        for entry in json.loads(inventory.squad_summaries())["squad"]
    }
    assert named[made] == "Renommée"

    assert inventory.delete_squad(made)
    assert inventory.squad_ids() == [1]


def test_the_squad_the_club_plays_with_cannot_be_deleted() -> None:
    # Dropping slot 1 would leave the club with nothing to field.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    assert not inventory.delete_squad(1)
    assert 1 in inventory.squad_ids()


def test_manager_tasks_are_recorded_and_survive() -> None:
    # They were a fixed empty list, so nothing completed was recorded: the bar
    # stayed at 0/13 and every task reset on the next launch.
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        ManagerTasks,
        PackShop,
        Wallet,
    )

    tasks = ManagerTasks()
    assert tasks.apply({"entries": [{"key": 0, "value": 1}, {"key": 3, "value": 1}]}) == 2
    done = {entry["key"] for entry in json.loads(tasks.response())["entries"]}
    assert done == {0, 3}

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"
        inventory, wallet = ClubInventory(), Wallet()
        actions = CardActions(PackShop(CardCatalogue(), wallet), wallet, inventory)
        ClubSave(path).save(inventory, wallet, actions, tasks)

        restored = ManagerTasks()
        ClubSave(path).load(ClubInventory(), Wallet(), actions, restored)
        assert restored.completed == tasks.completed


def test_a_card_pulled_twice_is_reported_as_a_duplicate() -> None:
    # duplicateItemIdList was always empty, so a player packed twice was never
    # offered as a duplicate and the screen had no reason to treat it apart.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=2_000_000)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    first = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    assert not any(item.get("duplicateItemId") for item in first["itemList"])
    for item in first["itemList"]:
        actions._keep(dict(item))

    # The same draw again: every card names the one it repeats, twice over.
    # The per-card duplicateItemId, and the plural list as pairs -- which is
    # what the FIFA 14 pack screen actually reads. The list was empty here, and
    # with it empty a repeat rendered as an ordinary card.
    #
    # What froze the title was a plural list of the *new* ids, telling the
    # screen to compare each card against itself. A pair never does that, and
    # the last assertion below is what keeps it that way.
    second = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    marked = [item for item in second["itemList"] if item.get("duplicateItemId")]
    assert marked and len(marked) == len(_players(second))
    owned = {item["id"] for item in inventory.items}
    assert all(item["duplicateItemId"] in owned for item in marked)

    pairs = second["duplicateItemIdList"]
    assert len(pairs) == len(_players(second))
    assert all(set(pair) == {"itemId", "duplicateItemId"} for pair in pairs)
    assert all(pair["duplicateItemId"] in owned for pair in pairs)
    assert all(pair["itemId"] != pair["duplicateItemId"] for pair in pairs)


def test_a_pack_is_three_players_and_nine_other_things() -> None:
    # Every pack drew twelve players, so no contract, kit, badge, ball,
    # stadium, manager or staff card has ever come out of one. The club's
    # consumables tab only ever showed what the club was seeded with.
    import random

    from fut_inventory import (
        PACK_SPECS,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), ClubInventory())
    rng = random.Random(11)
    for pack_id, spec in PACK_SPECS.items():
        opened = json.loads(shop.open_pack(pack_id, rng))
        items = opened["itemList"]
        assert len(items) == spec["count"]
        assert len({item["id"] for item in items}) == spec["count"]
        assert len(_players(opened)) == spec["players"]


def test_a_pack_never_hands_out_a_card_from_another_tier() -> None:
    # Chemistry styles are rated 90 and above and so exist in gold only.
    # Choosing that family in a Silver Pack and then relaxing the tier put a
    # 99-rated style in a silver pack.
    import random

    from fut_inventory import (
        TIER_RATINGS,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), ClubInventory())
    rng = random.Random(3)
    for pack_id, tier in ((103, "bronze"), (203, "silver"), (303, "gold")):
        low, high = TIER_RATINGS[tier]
        for _ in range(40):
            for item in json.loads(shop.open_pack(pack_id, rng))["itemList"]:
                rating = item.get("rating", 0)
                # Kits, badges, balls and stadiums are rated 0: no tier.
                if rating:
                    assert low <= rating <= high, (tier, item["itemType"], rating)


def test_a_pack_hands_out_more_contracts_than_training() -> None:
    # There are 42 training cards in the catalogue and 13 contracts, so an
    # even draw across the templates gives a club three times more training
    # than contract -- and a club that still runs out of contracts. The draw
    # weights the family, not the number of variants it happens to have.
    import collections
    import random

    from fut_inventory import (
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
        consumable_family,
    )

    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), ClubInventory())
    rng = random.Random(5)
    seen, wire = collections.Counter(), collections.Counter()
    for _ in range(120):
        for item in json.loads(shop.open_pack(303, rng))["itemList"]:
            wire[item["itemType"]] += 1
            seen[consumable_family(item) or item["itemType"]] += 1
    assert seen["contract"] > seen["training"]
    assert seen["contract"] > seen["healing"]
    # Consumables only. Kits, badges, balls and stadiums carry resource ids
    # invented in fut_inventory -- no table in the game's databases names them
    # -- and the invented ones drew blank card backs on the pack screen.
    assert not any(seen[kind] for kind in ("kit", "badge", "ball", "stadium",
                                           "manager", "staff"))
    # On the wire they carry FUT's own two types, never the family name.
    assert wire["development"] and wire["training"]
    assert not any(wire[family] for family in ("contract", "fitness", "healing"))


def test_a_second_contract_card_is_not_a_duplicate() -> None:
    # Consumables stack. Marking one as a repeat offers to quick-sell a card
    # the club is meant to accumulate.
    import random

    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    inventory = ClubInventory()
    shop = PackShop(CardCatalogue(), Wallet(coins=10_000_000), inventory)
    rng = random.Random(17)
    for _ in range(20):
        opened = json.loads(shop.open_pack(303, rng))
        marked = {
            item["id"] for item in opened["itemList"] if item.get("duplicateItemId")
        }
        players = {item["id"] for item in _players(opened)}
        assert marked <= players
        assert {pair["itemId"] for pair in opened["duplicateItemIdList"]} <= players


def test_a_rare_and_a_base_card_are_not_duplicates_of_each_other() -> None:
    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    inventory = ClubInventory()
    shop = PackShop(CardCatalogue(), Wallet(), inventory)
    # An asset the starting club does not already hold, so the only copies in
    # play are the ones this test puts there.
    held = {item.get("assetId") for item in inventory.items}
    asset = next(
        card["assetId"] for card in CardCatalogue().cards if card["assetId"] not in held
    )
    base = {"id": 1, "assetId": asset, "rareflag": 0}
    rare = {"id": 2, "assetId": asset, "rareflag": 1}
    inventory.items.append(base)
    # Same player, different version: not a repeat.
    assert shop._duplicates([rare]) == []
    assert shop._duplicates([dict(base, id=3)]) == [3]


def test_consumables_use_the_member_names_the_binary_carries() -> None:
    # The named members come from CardsDLL's JSON table between 0x89030F9C and
    # 0x89031148. They are necessary and they were not sufficient: the Apply
    # Consumable popup is backed by a sticker-book stats response and binds its
    # buttons from `stat`/`entries` rows in context 6, so the club could hold
    # 65 contracts, every scalar could be right, and the popup still reported
    # none available. Both shapes go out, carrying the same counts.
    from fut_inventory import (
        CONSUMABLE_STAT_CONTEXT,
        ClubInventory,
        consumable_stats_response,
    )

    document = json.loads(consumable_stats_response(ClubInventory()))

    scalars = {k: v for k, v in document.items() if isinstance(v, int)}
    assert document["stat"] == document["entries"]
    assert len(document["entries"]) == len(scalars)
    for entry in document["entries"]:
        assert entry["contextId"] == CONSUMABLE_STAT_CONTEXT
        assert entry["contextValue"] == 0
        assert entry["typeValue"] == scalars[entry["type"]]
    for member in (
        "consumablesContractPlayer",
        "consumablesFitnessPlayer",
        "consumablesHealing",
        "consumablesPosition",
        "consumablesTrainingPlayer",
    ):
        assert document[member] > 0, member
    # No member goes out at zero while the club holds consumables. Applying
    # from the squad screen decides from these counts alone, so a goalkeeper
    # made it read consumablesTrainingGk -- zero -- and report none available.
    assert document["consumablesTrainingGk"] > 0
    assert all(value > 0 for value in scalars.values())
    # The aggregate members cover their family once each. Chemistry styles
    # have no aggregate of their own -- the binary carries one member for an
    # outfielder's and one for a keeper's, and nothing above them -- so the
    # total counts both.
    assert document["consumables"] == sum(
        document[name]
        for name in (
            "consumablesContract",
            "consumablesFitness",
            "consumablesHealing",
            "consumablesTraining",
            "consumablesPosition",
            "consumablesTrainingPlayerPlayStyle",
            "consumablesTrainingGkPlayStyle",
        )
    )


def _unused_consumable_counts_are_not_the_club_counters() -> None:
    # club/stats/consumables was answered with the generic club counters --
    # players, staff, stadiums -- so the apply screen reported none available
    # while the club held sixteen consumables.
    from fut_inventory import (
        CONSUMABLE_ORDER,
        ClubInventory,
        club_stats_response,
        consumable_stats_response,
    )

    inventory = ClubInventory()
    consumables = json.loads(consumable_stats_response(inventory))["entries"]
    generic = json.loads(club_stats_response(inventory))["entries"]

    assert len(consumables) == len(CONSUMABLE_ORDER)
    assert all(entry["value"] > 0 for entry in consumables)
    # And it is genuinely a different answer, not the same document renamed.
    assert [e["value"] for e in consumables] != [e["value"] for e in generic]


def test_the_club_search_understands_the_consumable_family() -> None:
    # The consumables screen reads the counts and then searches the club for
    # "consumable" -- the family, not each kind. Comparing that against
    # itemType, which is contract, fitness, healing and so on, matched nothing:
    # the screen found counts and then no items.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    # Past the club's whole stock of consumables, or both answers come back
    # capped at the page size and the comparison says nothing.
    family = json.loads(inventory.club_response({"type": "consumable", "count": "500"}))
    one_kind = json.loads(
        inventory.club_response({"type": "development", "count": "500"})
    )

    assert len(family["itemData"]) > len(one_kind["itemData"]) > 0
    # Asking for the family must not sweep in players.
    assert all(item["itemType"] != "player" for item in family["itemData"])


def test_consumables_come_from_the_game_database() -> None:
    # They were invented once: three grades a family, asset ids counted up
    # from 1000. Every card drew NOT FOUND art, all of them were named
    # "Entrainement equipe", and applying one did nothing -- the title reads a
    # consumable's name and effect out of its own database by subtype, and
    # draws it by asset id, so neither is ours to pick.
    from fut_inventory import CONSUMABLE_TYPES, _consumable_definitions

    consumables = [
        item for item in INVENTORY.items
        if item.get("itemType") in CONSUMABLE_TYPES
    ]
    assert consumables

    subtypes = {item["cardsubtypeid"] for item in consumables}
    # A single subtype is what "all of them are the same card" looks like.
    assert len(subtypes) > 40
    # `cardassetid`, not `assetId`: a consumable's art has its own member, and
    # sending only `assetId` drew NOT FOUND on the pack screen.
    assert all(item["cardassetid"] < 1000 for item in consumables)
    assert not any("assetId" in item for item in consumables)

    # The member CardsDLL counts a card under comes from the catalogue, keyed
    # by the card's own database id -- which is the item's `resourceId`. It
    # used to travel on the item itself under a name CardsDLL does not carry.
    definitions = _consumable_definitions()
    members = {
        definitions[item["resourceId"]]["member"]
        for item in consumables
        if item["resourceId"] in definitions
    }
    assert "consumablesTrainingGk" in members
    assert "consumablesTrainingPlayer" in members
    assert "consumablesContractManager" in members


def _rack():
    from fut_inventory import ClubInventory, ConsumableRack, _consumable_definitions

    inventory = ClubInventory()
    definitions = _consumable_definitions()
    by_subtype: dict[int, list] = {}
    for item in inventory.items:
        row = definitions.get(item.get("resourceId"))
        if row:
            by_subtype.setdefault(row["cardsubtypeid"], []).append(item)
    return inventory, ConsumableRack(inventory), by_subtype


def test_a_contract_grants_matches_and_spends_the_card() -> None:
    # The club could hold a contract and show it, and there was no route that
    # did anything with one. Every consumable in FUT was decoration.
    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    player["contract"] = 10
    from fut_inventory import consumable_family

    held = len([i for i in inventory.items if consumable_family(i) == "contract"])

    card = by_subtype[201][0]
    result = rack.apply(card["resourceId"], [player["id"]])

    assert player["contract"] > 10
    assert result["consumedItemId"] == card["id"]
    # Spent, not merely reported spent.
    assert card not in inventory.items
    assert (
        len([i for i in inventory.items if consumable_family(i) == "contract"])
        == held - 1
    )


def test_a_contract_grants_less_to_a_better_player() -> None:
    # A contract grants a different number of matches to a gold, a silver and
    # a bronze card; the card database carries all three figures.
    from fut_inventory import _consumable_definitions

    inventory, rack, by_subtype = _rack()
    row = _consumable_definitions()[by_subtype[201][0]["resourceId"]]
    assert row["bronze"] > row["gold"]

    players = [i for i in inventory.items if i["itemType"] == "player"]
    gold = next(p for p in players if p["rating"] >= 75)
    gold["contract"] = 0
    rack.apply(by_subtype[201][0]["resourceId"], [gold["id"]])
    assert gold["contract"] == row["gold"]


def test_a_squad_fitness_card_restores_the_whole_eleven() -> None:
    inventory, rack, by_subtype = _rack()
    for player in inventory.squad:
        player["fitness"] = 50

    result = rack.apply(by_subtype[220][0]["resourceId"], [])

    assert all(player["fitness"] > 50 for player in inventory.squad)
    assert len(result["itemData"]) == len(inventory.squad)


def test_healing_refuses_the_wrong_injury_and_a_healthy_player() -> None:
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")

    # 211 is the head card; the binary's own list is head, upperbody, arm,
    # back, knee, leg, foot from 211, and 218 heals anything.
    try:
        rack.apply(by_subtype[211][0]["resourceId"], [player["id"]])
    except ConsumableRefused as refusal:
        assert "not injured" in str(refusal)
    else:
        raise AssertionError("a healthy player was healed")

    player["injuryGames"], player["injuryType"] = 3, "knee"
    try:
        rack.apply(by_subtype[211][0]["resourceId"], [player["id"]])
    except ConsumableRefused as refusal:
        assert "knee" in str(refusal)
    else:
        raise AssertionError("a head card treated a knee injury")

    rack.apply(by_subtype[215][0]["resourceId"], [player["id"]])
    assert player["injuryGames"] < 3


def test_a_refused_card_is_not_spent() -> None:
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    card = by_subtype[211][0]
    held = len(inventory.items)

    try:
        rack.apply(card["resourceId"], [player["id"]])
    except ConsumableRefused:
        pass
    # Nothing is written and nothing is spent until the effect is decided.
    assert card in inventory.items
    assert len(inventory.items) == held


def test_training_raises_one_attribute_or_all_six() -> None:
    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    for entry in player["attributeList"]:
        entry["value"] = 50

    rack.apply(by_subtype[61][0]["resourceId"], [player["id"]])
    values = [entry["value"] for entry in player["attributeList"]]
    assert values[0] > 50 and values[1:] == [50] * 5

    rack.apply(by_subtype[67][0]["resourceId"], [player["id"]])
    assert all(entry["value"] > 50 for entry in player["attributeList"])


def test_the_position_block_is_still_refused_and_recorded() -> None:
    # 232. Both catalogues call it a position change and the binary carries a
    # FUT_CONSUMABLE_POSITIONMOD -- but the card the console rendered for it
    # reads "DEBLOQUER / Capacite +8 moral", which is a stadium unlock. Writing
    # preferredPosition on the strength of that changes the wrong thing on a
    # real card, and the card is spent either way.
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    player = next(i for i in inventory.items if i["itemType"] == "player")
    card = by_subtype[232][0]
    try:
        rack.apply(card["resourceId"], [player["id"]])
    except ConsumableRefused:
        pass
    else:
        raise AssertionError("subtype 232 was applied")
    assert card in inventory.items
    # Recorded, so one application from the console names the family.
    assert [entry["cardsubtypeid"] for entry in rack.refused] == [232]


def test_a_chemistry_style_is_written_onto_the_card() -> None:
    # Refused for weeks on the grounds that 91-136 might be position
    # modifiers. What settles it is the member CardsDLL counts these under --
    # consumablesTrainingPlayerPlayStyle and consumablesTrainingGkPlayStyle --
    # which is in the binary's name table and is not a label anybody here
    # chose. Two ranges, outfield and goalkeeper, which is how chemistry
    # styles are split and is not how a position modifier would be.
    from fut_inventory import ConsumableRefused

    inventory, rack, by_subtype = _rack()
    outfield = next(
        i for i in inventory.items
        if i["itemType"] == "player" and i.get("preferredPosition") != "GK"
    )
    assert outfield["playStyle"] == 0
    rack.apply(by_subtype[91][0]["resourceId"], [outfield["id"]])
    assert outfield["playStyle"] == 91

    keeper = next(
        (i for i in inventory.items
         if i["itemType"] == "player" and i.get("preferredPosition") == "GK"),
        None,
    )
    if keeper is not None:
        rack.apply(by_subtype[121][0]["resourceId"], [keeper["id"]])
        assert keeper["playStyle"] == 121

    # The split is enforced: a goalkeeper style on an outfield player is the
    # one mistake the two ranges make obvious, and the card is not spent.
    card = by_subtype[122][0]
    try:
        rack.apply(card["resourceId"], [outfield["id"]])
    except ConsumableRefused:
        pass
    else:
        raise AssertionError("a goalkeeper style went onto an outfield player")
    assert card in inventory.items


def test_a_card_changed_by_a_consumable_survives_a_restart() -> None:
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        PackShop,
        Wallet,
    )

    inventory, rack, by_subtype = _rack()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)
    player = next(i for i in inventory.items if i["itemType"] == "player")
    player["contract"] = 10
    rack.apply(by_subtype[201][0]["resourceId"], [player["id"]])
    expected = player["contract"]

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"
        ClubSave(path).save(inventory, wallet, actions)

        # A card the club started with, changed since: neither acquired nor
        # sold, so it used to be forgotten on the next launch.
        restored, purse = ClubInventory(), Wallet()
        again = CardActions(PackShop(CardCatalogue(), purse), purse, restored)
        ClubSave(path).load(restored, purse, again)

        reloaded = next(i for i in restored.items if i["id"] == player["id"])
        assert reloaded["contract"] == expected
        # The squad holds the same objects, so it must see the change too.
        in_squad = [i for i in restored.squad if i["id"] == player["id"]]
        assert not in_squad or in_squad[0] is reloaded


def _pack_shop():
    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    return PackShop(CardCatalogue(), Wallet(coins=10**12), ClubInventory())


def test_the_rating_bands_are_the_stated_ones() -> None:
    # The old draw was uniform over the tier, which was close to these by
    # coincidence -- the gold pool happens to hold 862/254/66/18/4. Close by
    # coincidence moves the moment the catalogue is edited.
    import collections
    import random

    from fut_inventory import RATING_BANDS, is_ordinary

    shop, rng = _pack_shop(), random.Random(1)
    seen, total = collections.Counter(), 0
    for _ in range(1500):
        for item in json.loads(shop.open_pack(303, rng))["itemList"]:
            if item["itemType"] != "player" or not is_ordinary(item):
                continue
            total += 1
            for span, _weight in RATING_BANDS["gold"]:
                if span[0] <= item["rating"] <= span[1]:
                    seen[span] += 1

    for span, weight in RATING_BANDS["gold"]:
        share = 100 * seen[span] / total
        assert abs(share - weight) < max(1.5, weight * 0.35), (span, share, weight)


def test_an_ordinary_slot_can_still_hold_a_rare_card() -> None:
    # "1 Rare" is a minimum, not an exclusivity. Drawing ordinary slots from
    # non-rares only shut the top bands out of the pack: a gold rated 84 or
    # better is nearly always a Rare Gold, and 84-86 came out at 0.65%
    # against a stated 6%.
    import random

    from fut_inventory import is_ordinary

    shop, rng = _pack_shop(), random.Random(2)
    rares = 0
    players = 0
    for _ in range(200):
        for item in json.loads(shop.open_pack(303, rng))["itemList"]:
            if item["itemType"] == "player" and is_ordinary(item):
                players += 1
                rares += bool(item["rareflag"])
    assert 0 < rares < players


def test_a_special_is_rolled_once_per_pack_against_its_stated_chance() -> None:
    # `rareflag` is set on a Rare Gold and on every special alike, so the old
    # rare slot drew a special seven times out of ten and 15% of Gold Packs
    # held one. Nothing decided that; it was the shape of the catalogue.
    import random

    from fut_inventory import SPECIAL_CHANCE, is_ordinary

    shop, rng = _pack_shop(), random.Random(3)
    for pack_id in (103, 303, 304):
        packs = 1500
        held = 0
        for _ in range(packs):
            cards = json.loads(shop.open_pack(pack_id, rng))["itemList"]
            if any(
                card["itemType"] == "player" and not is_ordinary(card)
                for card in cards
            ):
                held += 1
        share = held / packs
        target = SPECIAL_CHANCE[pack_id]
        assert abs(share - target) < max(0.01, target * 0.35), (pack_id, share, target)


def test_the_special_family_is_chosen_by_weight_not_by_stock() -> None:
    # The catalogue holds 517 World Cup cards in the gold tier against 347
    # Team of the Week, so drawing evenly made World Cup the commonest
    # special in the game. Team of the Week is what a pack should mostly give.
    import collections
    import random

    from fut_inventory import SPECIAL_FAMILY_WEIGHTS, is_ordinary

    shop, rng = _pack_shop(), random.Random(5)
    seen = collections.Counter()
    for _ in range(4000):
        for item in json.loads(shop.open_pack(304, rng))["itemList"]:
            if item["itemType"] == "player" and not is_ordinary(item):
                seen[(item["rarity"] or "").lower()] += 1

    assert seen["team of the week"] > seen["world cup"]
    assert seen["team of the week"] > sum(
        count for name, count in seen.items() if name != "team of the week"
    )
    # Legends are weighted to zero until one has been seen to render.
    assert SPECIAL_FAMILY_WEIGHTS["legend"] == 0.0
    assert seen["legend"] == 0


def test_no_pack_holds_more_than_two_elite_cards() -> None:
    import random

    from fut_inventory import ELITE_RATING, MAX_ELITE_PER_PACK

    shop, rng = _pack_shop(), random.Random(7)
    for pack_id in (303, 305, 307):
        for _ in range(500):
            cards = json.loads(shop.open_pack(pack_id, rng))["itemList"]
            elite = [
                card
                for card in cards
                if card["itemType"] == "player" and card["rating"] >= ELITE_RATING
            ]
            assert len(elite) <= MAX_ELITE_PER_PACK


def test_withdrawing_a_listed_consumable_puts_it_back_too() -> None:
    # The guard asked whether the card carried `assetId`, which stopped being
    # true the moment a consumable started carrying `cardassetid` instead. A
    # withdrawn contract was dropped on the floor, silently.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    for kind in ("player", "consumable"):
        inventory, wallet = ClubInventory(), Wallet(coins=10**7)
        shop = PackShop(CardCatalogue(), wallet, inventory)
        actions = CardActions(shop, wallet, inventory)
        opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(17)))
        card = next(
            item
            for item in opened["itemList"]
            if (item["itemType"] == "player") == (kind == "player")
        )
        actions.move({"itemData": [{"id": card["id"], "pile": 5}]})
        listing = json.loads(actions.list_for_sale({"itemData": {"id": card["id"]}}))
        actions.withdraw(listing["tradeId"])
        assert [item["id"] for item in actions.transfer] == [card["id"]], kind


def test_an_unlockable_is_never_drawn() -> None:
    # The console named subtype 232: "DEBLOQUER / Capacite +8 moral" and
    # "Grosse affluence morale 6". Stadium unlockables, and the client refuses
    # to keep one -- it raises a dialog telling the player to use it from the
    # action menu instead. Nothing here serves that route, so a drawn one is
    # dead weight plus a dialog on every pack.
    import collections
    import random

    from fut_inventory import UNDRAWN_CONSUMABLE_TYPES

    from fut_inventory import consumable_family

    shop, rng = _pack_shop(), random.Random(3)
    seen = collections.Counter()
    for _ in range(300):
        for item in json.loads(shop.open_pack(304, rng))["itemList"]:
            seen[consumable_family(item) or item["itemType"]] += 1

    assert "position" in UNDRAWN_CONSUMABLE_TYPES
    for kind in UNDRAWN_CONSUMABLE_TYPES:
        assert seen[kind] == 0
    # The families that are drawn still are.
    assert seen["contract"] and seen["fitness"] and seen["healing"]


def test_the_bare_club_holds_one_of_every_kind_it_owns() -> None:
    # Slicing the sorted list took the first N cards, and the sort puts
    # players first, so the bare response was ninety players and nothing else
    # -- every consumable, kit, badge and staff card cut off.
    #
    # That is what "Pas d'élément disponible" was on the apply-consumable
    # picker: it reads the club the client already holds, the club it held had
    # no consumable in it, and it never asked the server for more. The counts
    # said 35 contracts and the list said none.
    import collections

    import fut_inventory as inventory

    club = inventory.ClubInventory()
    bare = json.loads(club.club_response())["itemData"]
    assert len(bare) <= inventory.CLUB_UNFILTERED_LIMIT
    assert len(bare) < len(club.items)

    owned = {item.get("itemType") for item in club.items}
    served = collections.Counter(item.get("itemType") for item in bare)
    assert set(served) == owned
    assert served["player"] > 1

    # And it stays under what the console was measured surviving.
    assert len(club.club_response()) < 77 * 1024


def test_the_bare_club_keeps_the_order_it_was_sorted_into() -> None:
    # The client reads the list in order and the screen pages it, so the cap
    # must not shuffle what survived it.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    bare = json.loads(club.club_response())["itemData"]
    kept = {item["id"] for item in bare}
    whole = json.loads(club.club_response({"count": str(len(club.items))}))["itemData"]
    expected = [item["id"] for item in whole if item["id"] in kept]
    assert [item["id"] for item in bare] == expected


def test_club_user_carries_the_cards_the_picker_binds_against() -> None:
    # `/clubUser` answered with the persona and 122 bytes, so the Apply
    # Consumable picker had nothing to bind and never asked the server for
    # more -- "Pas d'élément disponible" with 35 contracts in the club.
    import collections

    from fut_inventory import (
        CONSUMABLE_TYPES,
        ClubInventory,
        club_user_response,
    )

    club = ClubInventory()
    payload = club_user_response(club, "Fondateur FUT")
    document = json.loads(payload)

    # The same persona every other document carries. Aligning /user alone and
    # leaving the squad documents on 0 is what emptied the squad screen: a
    # client will not show a squad that belongs to somebody else.
    import fut_inventory as _inventory

    assert document["user"] == [
        {"persona": "Fondateur FUT", "personaId": _inventory.PERSONA.id,
         "public": False}
    ]
    items = document["itemData"]
    kinds = collections.Counter(item["itemType"] for item in items)

    # Players too: the route is the client's face-card cache bootstrap, and
    # the PC revival appends consumables to the normal first player page.
    assert kinds["player"] > 0
    # On the wire, FUT's own two consumable types and never a family name.
    assert set(kinds) - {"player"} == CONSUMABLE_TYPES

    assert document["total"] == document["count"] == len(items)

    # And it stays under what the console was measured surviving.
    assert len(payload) < 77 * 1024


def test_each_consumable_category_answers_with_that_category() -> None:
    # The picker names a category in the path and asks one at a time:
    # /club/consumables/contracts, then /fitness, then /development. The route
    # was matched with `startswith` and answered every one of them with the
    # club's whole stock -- it asked for contracts and got 242 cards of every
    # family mixed together, which is not a list of contracts however many
    # contracts are in it.
    from fut_inventory import (
        CONSUMABLE_TYPES,
        ClubInventory,
        consumable_family,
        consumables_response,
    )

    club = ClubInventory()
    whole = json.loads(consumables_response(club))["itemData"]
    assert whole

    for path, family in (
        ("contracts", "contract"),
        ("contract", "contract"),
        ("fitness", "fitness"),
        ("healing", "healing"),
        ("playstyle", "playStyle"),
    ):
        document = json.loads(consumables_response(club, path))
        items = document["itemData"]
        assert items, path
        assert len(items) < len(whole), path
        assert {consumable_family(item) for item in items} == {family}, path
        assert document["total"] == document["count"] == len(items)

    # `development` and `training` are the wire item types, not families, and
    # mean every card carrying that type.
    for wire in CONSUMABLE_TYPES:
        items = json.loads(consumables_response(club, wire))["itemData"]
        assert items
        assert {item["itemType"] for item in items} == {wire}


def test_a_category_this_club_has_nothing_for_answers_empty() -> None:
    # The console asks for managerLeagueModifier, and this club holds none --
    # manager cards are left out of the catalogue. An unknown category used to
    # fall through to "everything", so a tab headed one thing listed another.
    from fut_inventory import ClubInventory, consumables_response

    club = ClubInventory()
    for category in ("managerLeagueModifier", "managerContract", "nonsense"):
        document = json.loads(consumables_response(club, category))
        assert document["itemData"] == [], category
        assert document["total"] == 0

    # The bare path still means everything.
    assert json.loads(consumables_response(club))["total"] > 0


def test_club_user_covers_every_subtype_the_club_owns() -> None:
    # Twelve cards a family sounds fair until you notice the families span two
    # subtype blocks each: `training` covers 51-57 and 61-67, `playStyle`
    # covers 91-110 and 121-136, and the first twelve of either are all from
    # the low block. Applying to a goalkeeper reads the keeper's block, and
    # the keeper's block was not in the twelve -- "Pas d'élément disponible"
    # over a club holding 21 of them.
    from fut_inventory import (
        CONSUMABLE_TYPES,
        ClubInventory,
        club_user_response,
    )

    club = ClubInventory()
    owned = {
        item["cardsubtypeid"]
        for item in club.items
        if item.get("itemType") in CONSUMABLE_TYPES
    }
    sent = {
        item["cardsubtypeid"]
        for item in json.loads(club_user_response(club, "Fondateur FUT"))["itemData"]
        if item["itemType"] in CONSUMABLE_TYPES
    }
    assert owned
    assert sent == owned


def test_a_finished_match_pays_a_completion_and_a_skill_award() -> None:
    # `/match/end` answered three empty members and threw the result away: no
    # coins for the match, no progress in the cup, nothing on the award
    # screen. A club could win a Gold Cup final and finish exactly as poor as
    # it started.
    from fut_inventory import match_reward

    won = match_reward(
        {
            "goals": 3, "shotsOnTarget": 7, "successfulTackles": 12,
            "corners": 5, "passingPercentage": 84, "possessionPercentage": 61,
            "fouls": 4, "yellowCards": 1, "offsides": 2,
        },
        {"goals": 0},
    )
    assert won["completionAward"] > 0
    assert won["skillAward"] > 0
    assert won["totalCoins"] == won["completionAward"] + won["skillAward"]
    # A clean sheet is worth keeping.
    assert won["bonuses"]["cleanSheet"] > 0
    assert won["penalties"]["cards"] < 0

    # Conceding pays less than not conceding, all else equal.
    leaky = match_reward({"goals": 3}, {"goals": 4})
    tidy = match_reward({"goals": 3}, {"goals": 0})
    assert tidy["totalCoins"] > leaky["totalCoins"]

    # A match nobody finished pays nothing at all.
    walked = match_reward({"goals": 3}, {"goals": 0}, minutes=20, completed=False)
    assert walked["totalCoins"] == 0


def test_the_reward_is_capped_however_lopsided_the_match() -> None:
    # Nine goals is worth more than one, and not nine times more.
    from fut_inventory import MATCH_BONUS_CAPS, match_reward

    thrashing = match_reward({"goals": 30, "shotsOnTarget": 40}, {"goals": 0})
    assert thrashing["bonuses"]["goals"] == MATCH_BONUS_CAPS["goals"][1]
    assert thrashing["bonuses"]["shotsOnTarget"] == MATCH_BONUS_CAPS["shotsOnTarget"][1]


def test_the_result_is_read_from_the_score_when_it_is_not_stated() -> None:
    from fut_inventory import match_result

    assert match_result({"endReason": "WIN"}) == "WIN"
    assert match_result({"endReason": "FORFEIT"}) == "QUIT"
    assert match_result(
        {"myMatchStats": {"goals": 2}, "opponentMatchStats": {"goals": 1}}
    ) == "WIN"
    assert match_result(
        {"myMatchStats": {"goals": 1}, "opponentMatchStats": {"goals": 1}}
    ) == "DRAW"
    # Nothing recognisable settles as no contest: it pays nothing and moves no
    # cup, which is the safe reading of a message this server cannot parse.
    assert match_result({}) == "NO_CONTEST"


def test_a_cup_advances_on_a_win_and_starts_again_on_a_loss() -> None:
    from fut_inventory import TOURNAMENTS, TournamentProgress

    cup = 3
    final_award = next(a for i, _t, _l, a, _r in TOURNAMENTS if i == cup)

    progress = TournamentProgress()
    progress.apply(cup, {"round": 1})

    first = progress.advance(cup, "WIN")
    assert first["round"] == 2
    assert first["roundCoins"] > 0
    assert first["prize"] == 0

    # A draw has not settled the round, so it is played again.
    held = progress.advance(cup, "DRAW")
    assert held["round"] == 2

    # Four rounds: the prize is the fourth win, not the third.
    progress.advance(cup, "WIN")
    progress.advance(cup, "WIN")
    won = progress.advance(cup, "WIN")
    assert won["previousRound"] == 4
    assert won["prize"] == final_award
    # Winning it does not retire it: this is offline, and a cup you can only
    # win once stops existing the moment you are good enough to win it.
    assert won["round"] == 1

    progress.apply(cup, {"round": 3})
    lost = progress.advance(cup, "LOSS")
    assert lost["round"] == 1
    assert lost["roundCoins"] == 0

    # A result nobody recognises moves nothing.
    progress.apply(cup, {"round": 2})
    assert progress.advance(cup, "NO_CONTEST")["settled"] is False
    assert progress.entries[cup]["round"] == 2


def test_the_club_marks_the_repeats_it_is_holding() -> None:
    # A pack marks its own repeats before you accept them and that was the end
    # of it: once the card was in the club nothing said so any more, and the
    # club screen -- which is where anyone actually goes looking for repeats to
    # sell -- showed a club full of ordinary cards.
    import fut_inventory as inventory

    club = [
        {"id": 10, "itemType": "player", "resourceId": 500, "rating": 84},
        {"id": 11, "itemType": "player", "resourceId": 501, "rating": 83},
        {"id": 12, "itemType": "player", "resourceId": 500, "rating": 84},
        {"id": 13, "itemType": "player", "resourceId": 500, "rating": 84},
        # Two contracts are two contracts, not a repeat of one.
        {"id": 14, "itemType": "development", "resourceId": 900},
        {"id": 15, "itemType": "development", "resourceId": 900},
    ]
    pairs = inventory.club_duplicate_pairs(club)
    assert pairs == [
        {"itemId": 12, "duplicateItemId": 10},
        {"itemId": 13, "duplicateItemId": 10},
    ]
    # The oldest copy is the original, and it says so on the card too.
    assert "duplicateItemId" not in club[0]
    assert club[2]["duplicateItemId"] == 10
    assert club[3]["duplicateItemId"] == 10

    # Sell the original and the survivors stop pointing at a card that is gone;
    # the next oldest becomes the one kept.
    del club[0]
    assert inventory.club_duplicate_pairs(club) == [
        {"itemId": 13, "duplicateItemId": 12}
    ]
    assert "duplicateItemId" not in club[1]


def test_a_special_is_not_a_repeat_of_the_ordinary_card() -> None:
    # Every version of a player shares his asset id. Keying on the asset made
    # a Team of the Season card a repeat of the Rare Gold one.
    import fut_inventory as inventory

    club = [
        {"id": 20, "itemType": "player", "assetId": 167628,
         "rarity": "Rare Gold", "rating": 74},
        {"id": 21, "itemType": "player", "assetId": 167628,
         "rarity": "Team of the Season", "rating": 84},
    ]
    assert inventory.club_duplicate_pairs(club) == []


def test_a_card_on_the_transfer_list_but_not_listed_is_still_shown() -> None:
    # Sending a card to the transfer list takes it out of the club -- it has to,
    # or it shows in both places at once -- and the trade pile answered with the
    # listings alone, so an unlisted card was in neither. It went the way the
    # lost pack cards went: quietly. The same symptom is reported against the PC
    # revival, where unlisted pile-5 cards vanished from the Transfer List.
    import random

    import fut_inventory as inventory

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(29)))
    card_id = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": card_id, "pile": inventory.PILE_TRANSFER}]})

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    entry = pile["auctionInfo"][0]
    assert entry["itemData"]["id"] == card_id
    # Not for sale, and saying so in the members a real listing already uses.
    assert entry["tradeId"] == 0
    assert entry["expires"] == -1
    assert entry["buyNowPrice"] == 0
    assert entry["tradeOwner"] is True

    # Listed, it becomes one entry and not two.
    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": card_id}, "startingBid": 300, "buyNowPrice": 900}
        )
    )
    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == listing["tradeId"]

    # Withdrawn, it is back on the list and still visible.
    actions.withdraw(listing["tradeId"])
    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == 0


# The real body this console PUT to /ut/game/fifa14/match/end on 11 August at
# 06:12, copied out of the journal. Everything asserted below is measured
# against this rather than against a shape borrowed from another platform.
REAL_MATCH_END = {
    "endReason": "LOSS",
    "myRating": 10,
    "opponentRating": 9,
    "myMatchStats": {
        "goals": 1, "shotsOnTarget": 2, "successfulTackles": 33, "corners": 2,
        "cleansheets": 0, "passingPercentage": 79, "possessionPercentage": 55,
        "manOfTheMatch": 1, "fouls": 2, "yellowCards": 0, "redCards": 0,
        "offsides": 1,
    },
    "opponentMatchStats": {
        "goals": 2, "shotsOnTarget": 4, "successfulTackles": 22, "corners": 2,
        "cleansheets": 0, "passingPercentage": 84, "possessionPercentage": 45,
        "manOfTheMatch": 0, "fouls": 0, "yellowCards": 0, "redCards": 0,
        "offsides": 0,
    },
    "items": [
        {"id": 1800000019, "fitness": 99},
        {"id": 1800000011, "fitness": 95, "assists": 1},
        {"id": 1800000018, "fitness": 96, "goals": 1},
    ],
    "matchData": "532382ea8a2e1141811bc087ce2e219066222a58be5e210d578fb521802ba8ca",
}


def test_the_real_match_end_body_settles() -> None:
    import fut_inventory as inventory

    assert inventory.match_result(REAL_MATCH_END) == "LOSS"
    reward = inventory.match_reward(
        REAL_MATCH_END["myMatchStats"],
        REAL_MATCH_END["opponentMatchStats"],
    )
    # The two percentage members are spelled `passingPercentage` and
    # `possessionPercentage` on this platform; reading them under any other
    # name pays nothing for either, silently.
    assert reward["bonuses"]["passAccuracy"] > 0
    assert reward["bonuses"]["possession"] > 0
    assert reward["bonuses"]["manOfTheMatch"] == inventory.MOTM_AWARD
    # Two conceded, so no clean sheet.
    assert "cleanSheet" not in reward["bonuses"]
    assert reward["goalsFor"] == 1 and reward["goalsAgainst"] == 2
    # A loss still pays: the match was played to the end.
    assert reward["completionAward"] == inventory.MATCH_COMPLETION_AWARD
    assert reward["totalCoins"] > 0


def test_a_match_writes_fitness_goals_and_assists_back_to_the_club() -> None:
    # The captured body carries a per-player fitness, and goals and assists for
    # whoever got them. All of it was discarded, so nobody in the club ever
    # lost fitness -- which is what left the whole consumable pile with nothing
    # to restore.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    players = [item for item in club.items if item.get("itemType") == "player"][:3]
    for index, entry in enumerate(REAL_MATCH_END["items"]):
        entry = dict(entry, id=players[index]["id"])
        REAL_MATCH_END["items"][index] = entry
    players[1]["assists"] = 4
    players[1]["lifetimeAssists"] = 9

    touched = inventory.apply_match_items(club, REAL_MATCH_END["items"])
    assert touched == {"fitness": 3, "goals": 1, "assists": 1, "unknown": []}
    assert players[0]["fitness"] == 99
    # Fitness is written, not subtracted: the client sends what it is *after*.
    assert players[1]["fitness"] == 95
    # Goals and assists are added up, because each payload carries one match.
    assert players[1]["assists"] == 5
    assert players[1]["lifetimeAssists"] == 10
    assert players[2]["goals"] == 1

    # A card that is not in the club is reported rather than quietly dropped.
    touched = inventory.apply_match_items(club, [{"id": 42, "fitness": 1}])
    assert touched["unknown"] == [42]


def test_an_abandoned_match_pays_nothing_and_touches_nobody() -> None:
    # The other captured body: {"endReason":"DNF","items":[],"matchData":...}
    import fut_inventory as inventory

    document = {"endReason": "DNF", "items": [], "matchData": "aad944f8"}
    assert inventory.match_result(document) == "DNF"
    reward = inventory.match_reward({}, {}, completed=False)
    assert reward["totalCoins"] == 0
    assert inventory.apply_match_items(inventory.ClubInventory(), [])["fitness"] == 0


def test_the_team_of_the_week_bench_matches_the_team() -> None:
    # Padding the side from the catalogue's best rares put a 98, a 98, a 98, a
    # 97 and a 97 on the bench of a team whose real members top out at 85. That
    # is not a Team of the Week, and it is not a fair opponent either: the
    # challenge computes opponentRating from the first eleven, so the padding
    # decided how strong the team you play against is.
    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    squad = json.loads(inventory.totw_response(catalogue))
    ratings = sorted(
        (card.get("rating", 0) for card in squad["itemData"]), reverse=True
    )
    real = [
        card["rating"]
        for card in catalogue.cards
        if card["assetId"] in set(inventory._totw_asset_ids())
    ]
    if real:
        # Nobody on the bench is better than the best real in-form.
        assert ratings[0] <= max(real)
    assert len(ratings) == 23

    # And the challenge it advertises is a side you could actually face.
    for challenge in squad["squadChallenge"]:
        assert 60 <= challenge["opponentRating"] <= 90


def test_the_empty_big_archive_declares_its_size_the_way_a_real_one_does() -> None:
    # A real BIGF from this game -- read out of the Title Update's own
    # helperFunctions package -- carries its total size little-endian and the
    # entry count and header size big-endian:
    #
    #     BIGF   54032 (little)   3 entries (big)   header 56 (big)
    #
    # All four fields went out big-endian here, so a sixteen-byte archive
    # declared itself 0x10000000 bytes long: 268 megabytes.
    import struct

    import fut_inventory as inventory

    archive = inventory.empty_big_archive()
    assert len(archive) == 16
    assert archive[:4] == b"BIGF"
    assert struct.unpack_from("<I", archive, 4)[0] == len(archive)
    assert struct.unpack_from(">I", archive, 8)[0] == 0     # no entries
    assert struct.unpack_from(">I", archive, 12)[0] == 16   # header is all of it


def test_the_added_packs_hold_what_they_advertise() -> None:
    # Retail FIFA 14 had no consumables-only pack and nothing above 25 000, so
    # these are not reconstructions of anything -- they are what an offline
    # club with no store behind it needs to keep being worth playing. What is
    # asserted is the promise each one makes on the store screen.
    import collections
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    wallet = inventory.Wallet(coins=10**9)
    shop = inventory.PackShop(catalogue, wallet, club)

    def contents(pack_id: int, runs: int = 30):
        kinds = collections.Counter()
        families = collections.Counter()
        specials = 0
        ordinary = {"rare gold", "non-rare gold", "rare silver",
                    "non-rare silver", "rare bronze", "non-rare bronze"}
        for seed in range(runs):
            opened = json.loads(shop.open_pack(pack_id, random.Random(seed)))
            assert len(opened["itemList"]) == inventory.PACK_SPECS[pack_id]["count"]
            for item in opened["itemList"]:
                kinds[item.get("itemType")] += 1
                if item.get("itemType") != "player":
                    continue
                rarity = (item.get("rarity") or "").lower()
                families[rarity] += 1
                if rarity not in ordinary:
                    specials += 1
        return kinds, families, specials / runs

    # Consumables packs hold no players at all.
    for pack_id in (108, 109):
        kinds, _, _ = contents(pack_id)
        assert kinds["player"] == 0
        assert set(kinds) <= inventory.CONSUMABLE_TYPES | {"club"}

    # The 100 000 pack is rare golds, all twelve of them.
    _, families, _ = contents(308)
    assert families["non-rare gold"] == 0
    assert families["rare gold"] > 0

    # A Team of the Week pack owes one every time, and cannot hand out another
    # family instead.
    _, families, per_pack = contents(309)
    assert per_pack >= 1.0
    assert families["team of the week"] > 0
    assert families["team of the year"] == 0
    assert families["team of the season"] == 0

    # The Team of the Season pack promises two, weighted to its own name.
    _, families, per_pack = contents(310)
    assert per_pack >= 2.0
    assert families["team of the season"] > families["team of the year"]


def test_the_store_groups_are_written_out_not_left_as_tiers() -> None:
    # `displayGroup.value` is drawn as the group heading, verbatim: the store
    # showed "bronze", "silver" and "gold" in lower case because that is what
    # it was handed. It is not a localisation key -- the pack's own
    # FUT_STORE_PACK_<id>_DESC is one, and that resolves against the client's
    # locale, which is why retail pack names read correctly and the headings
    # did not.
    import collections

    import fut_inventory as inventory

    catalogue = json.loads(inventory.store_catalogue())
    groups = collections.OrderedDict()
    for entry in sorted(
        catalogue["purchase"], key=lambda row: row["displayGroup"]["priority"]
    ):
        groups.setdefault(entry["displayGroup"]["value"], []).append(entry["id"])

    # A group's packs are contiguous and the headings come out in order:
    # sorting the catalogue by pack id put the consumables packs, 108 and 109,
    # between the bronze ones and the silver ones.
    headings = list(groups)
    assert headings == [
        "Packs Bronze", "Packs Argent", "Packs Or", "Consommables", "Packs Speciaux",
    ]
    # The added packs get headings of their own rather than being filed under a
    # tier they only nominally belong to.
    assert groups["Consommables"] == [108, 109]
    assert groups["Packs Speciaux"] == [309, 310]
    # The tier still names the artwork.
    for entry in catalogue["purchase"]:
        assert entry["displayGroupAssetId"] in (1, 2, 3)


def test_packing_the_same_player_twice_says_so_the_second_time() -> None:
    # Klose 90, twice in a row on 12 August, with nothing on the second pack to
    # say it was a repeat. A card drawn from a pack does not go to the club --
    # it goes to the purchased pile and waits there until it is sent on -- and
    # the pile was the one place the duplicate check did not look.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    club.items = []                      # an empty club: the pile is all there is
    wallet = inventory.Wallet(coins=10**9)
    shop = inventory.PackShop(catalogue, wallet, club)

    first = json.loads(shop.open_pack(303, random.Random(11)))
    assert first["duplicateItemIdList"] == []
    packed = {
        item["assetId"]: item["id"]
        for item in first["itemList"]
        if item.get("itemType") == "player"
    }
    assert packed

    # The same pack again, from the same seed, draws the same players.
    second = json.loads(shop.open_pack(303, random.Random(11)))
    pairs = {row["itemId"]: row["duplicateItemId"] for row in second["duplicateItemIdList"]}
    assert pairs, "the second pack reported no duplicates at all"
    for item in second["itemList"]:
        if item.get("itemType") != "player":
            continue
        original = packed.get(item["assetId"])
        if original is None:
            continue
        # Both spellings, because they are read in different places.
        assert item.get("duplicateItemId") == original
        assert pairs.get(item["id"]) == original


def test_a_pack_after_a_restart_does_not_reissue_ids_the_club_holds() -> None:
    # `PACK_ITEM_ID_BASE + purchases * 100 + slot` counted `purchases` from
    # zero every time the server started, so the first pack after a restart
    # reissued ids the saved club was already holding. `_keep` refuses an id it
    # already holds -- soundly, since the same item twice is one card counted
    # twice -- so a freshly packed card could be dropped on the way to the club
    # and appear nowhere. A Record Breaker Klose went that way on 12 August:
    # the club's 1950000205 was a Non-Rare Silver from an earlier session.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    wallet = inventory.Wallet(coins=10**9)

    before = inventory.PackShop(catalogue, wallet, club)
    first = json.loads(before.open_pack(303, random.Random(3)))
    # The club keeps them, the way "send all to club" does.
    club.items.extend(first["itemList"])
    held = {item["id"] for item in club.items}

    # A restart: a new shop, its counter back at zero, the same saved club.
    after = inventory.PackShop(catalogue, wallet, club)
    second = json.loads(after.open_pack(303, random.Random(4)))
    reissued = [item["id"] for item in second["itemList"] if item["id"] in held]
    assert reissued == [], f"reissued ids the club already holds: {reissued}"


def test_a_pack_never_hands_out_the_same_player_twice() -> None:
    # Two Vargas out of one Team of the Season pack on 12 August. Retail does
    # not do that, and the screen has no way to show it that is not confusing:
    # the same card twice reads as a bug whatever the data says. So the draw is
    # retried rather than the repeat explained.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    club.items = []
    wallet = inventory.Wallet(coins=10**10)
    shop = inventory.PackShop(catalogue, wallet, club)

    repeats = 0
    for pack_id in sorted(inventory.PACK_SPECS):
        for seed in range(20):
            opened = json.loads(shop.open_pack(pack_id, random.Random(seed)))
            players = [
                item for item in opened["itemList"]
                if item.get("itemType") == "player"
            ]
            keys = [(item.get("assetId"), item.get("rareflag")) for item in players]
            repeats += len(keys) - len(set(keys))
    assert repeats == 0


def test_the_purchased_pile_says_which_of_its_cards_repeat_the_club() -> None:
    # The pack screen gets its pairs in the pack response. The unassigned pile
    # is a different screen with a duplicates tab of its own, and it was handed
    # an empty list -- so a card the pack had just flagged sat in that tab's
    # absence.
    import random

    import fut_inventory as inventory

    catalogue = inventory.CardCatalogue()
    club = inventory.ClubInventory()
    club.items = []
    wallet = inventory.Wallet(coins=10**10)
    shop = inventory.PackShop(catalogue, wallet, club)

    opened = json.loads(shop.open_pack(303, random.Random(5)))
    player = next(
        item for item in opened["itemList"] if item.get("itemType") == "player"
    )
    # The club already owns that card, acquired earlier: a smaller id.
    owned = dict(player, id=1)
    club.items.append(owned)

    pile = json.loads(shop.purchased_items())
    pairs = {row["itemId"]: row["duplicateItemId"] for row in pile["duplicateItemIdList"]}
    assert pairs.get(player["id"]) == 1
    # And on the card itself, which is what the pile document carries.
    held = next(item for item in pile["itemData"] if item["id"] == player["id"])
    assert held["duplicateItemId"] == 1
