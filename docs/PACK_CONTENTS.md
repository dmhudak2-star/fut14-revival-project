# What is in a pack

A FIFA 14 pack is twelve items and only three of them are players. The other
nine are consumables and club items. Every pack this server sold drew twelve
players, which is why no contract, kit, badge, ball, stadium, manager or staff
card has ever come out of one, and why the club's consumables tab has only
ever shown what the club was seeded with at boot.

## The composition

`PACK_SPECS` carries `players` alongside `count` and `rares`:

| pack | items | rares | players |
|---|---|---|---|
| Bronze / Premium Bronze | 12 | 1 / 3 | 3 |
| Silver / Premium Silver | 12 | 1 / 3 | 3 |
| Gold / Premium Gold | 12 | 1 / 3 | 3 |
| Jumbo Gold | 24 | 7 | 8 |
| Gold Players / Premium Gold Players | 12 | 1 / 3 | 12 |

A players pack is the exception its name promises. The slot kinds are shuffled,
so the players are not always the first three cards on the screen.

The rare markers sit on the first `rares` slots and the kinds are shuffled
independently, so a rare slot may hold a rare consumable rather than a rare
player. That is retail behaviour: a Bronze Pack advertises one rare, not one
rare player.

## Where the non-player cards come from

The same two sources the club is seeded from, so a card drawn from a pack and
a card the club started with are the same object -- `_consumable_item` and
`_club_item` build both. A card shaped one way in the pack screen and another
in the club is a card that disappears on the way there.

## Why the draw is weighted by family

The catalogue holds 42 training cards and 13 contracts. Drawing evenly across
the 124 templates hands out three times more training than contract, and a
club that opens packs still runs out of contracts. `CONSUMABLE_DRAW_WEIGHT`
weights the *family*, and the card is then chosen inside it, so the number of
variants a family happens to have does not decide how often it appears.

Measured over 150 gold packs: contract 25%, player 25%, fitness 13%, healing
6%, play style 6%, badge 5%, training 5%, position 4%, kit 4%, ball 3%,
stadium 2%, staff 1%, manager under 1%.

## Tier gating, and the trap in it

The family is chosen only from families that hold a card of the pack's tier,
and the tier never relaxes afterwards. Only the rare flag does.

This is not a detail. The play style block (subtypes 91-136, whatever those
cards turn out to be -- see `docs/CONSUMABLES.md`) is rated 90 and above, so
it exists in gold only. The first implementation chose the family first and
then relaxed the tier when the family had nothing to offer, which put a
99-rated card of that block in a Silver Pack. `test_a_pack_never_hands_out_a_card_from_
another_tier` opens 120 packs across the three tiers and checks every rated
card against its tier.

Kits, badges, balls and stadiums are rated 0 and carry no tier: a kit is a kit
in any pack.

## Duplicates apply to players only

A second contract card is not a repeat of the first, it is a second contract.
Consumables stack. `_mark_duplicates` skips everything whose `itemType` is not
`player`, on both sides -- the owned index and the drawn cards -- because
marking a consumable as a duplicate offers to quick-sell a card the club is
meant to accumulate. See `docs/DUPLICATES.md` for the pairing itself.

## The starter packs too

`grant_starter_packs` draws the same nine non-player slots. A new club needs
contracts more than it needs a fourth striker, and it opened with none.

## What was measured and rejected

A byte ceiling on every club response, to replace the card count in
`CLUB_UNFILTERED_LIMIT`. The reasoning looked sound -- `type=consumable`
returns 76.7 KB when asked with no count, and packs now add about nine
consumables each.

The traffic says otherwise. Across 374 club responses in the journals, the
median is 20.9 KB and the 90th percentile 21.8 KB; exactly two exceeded 77 KB,
and both were the bare no-parameter request that the card count already
bounds. Every filtered request the console has ever sent carries `count`, and
usually `start` -- there is no such thing in practice as a filtered
count-less request.

Worse, trimming a paginated response by bytes silently drops the tail of a
page while the client's next `start` skips past it, so cards fall into a gap
and become invisible. That is the same class of bug as the card that was
acknowledged and lost. The ceiling was removed.

# The odds

The player draw used to be: split the tier's cards on `rareflag`, take the
first `rares` slots from the set list and the rest from the unset one,
uniformly. That is not a set of odds. It is an accident of what the catalogue
holds, and the accident was expensive.

`rareflag` is set on a Rare Gold **and** on every special. In the gold tier
that pool is 453 rare golds against 1120 specials, so a rare slot was a
special seven times out of ten. Measured over 400 Gold Packs before the
change: 15% held a special, and the commonest family was World Cup — because
the catalogue holds 517 World Cup cards in that tier against 347 Team of the
Week. Nothing decided any of that.

## Rating bands

`RATING_BANDS` states the rating distribution per tier. Gold: 75-79 at 72%,
80-83 at 20%, 84-86 at 6%, 87-89 at 1.7%, 90+ at 0.3%. Silver and bronze carry
the same shape over their own ranges.

A uniform draw over the gold ordinaries was already close to this — the pool
happens to hold 862/254/66/18/4 — but close by coincidence moves the moment
the catalogue is edited.

A rare slot leans higher through `RARE_BAND_MULTIPLIER` (×1.2 at 84-86, ×1.45
at 87-89, ×1.8 at 90+), and the lean is bounded rather than open.

**An ordinary slot draws from every ordinary card of the tier, rare included.**
"1 Rare" is a minimum, not an exclusivity. Drawing ordinary slots from
non-rares only shut the top bands out of the pack entirely: a gold rated 84 or
better is nearly always a Rare Gold, and 84-86 measured 0.65% against a stated
6%. With the union pool it measures 6.1%.

## The special

Rolled **once per pack** against `SPECIAL_CHANCE`, not once per card. Rolling
per card makes a pack's real chance a function of how many players it happens
to hold, which is not what a stated chance means.

| pack | stated | measured over 1500 |
|---|---|---|
| Bronze | 0.6% | 0.5% |
| Silver | 1.5% | 1.9% |
| Gold | 8% | 9.0% |
| Premium Gold | 16% | 17.4% |
| Jumbo Gold | 25% | 24.1% |
| Premium Gold Players | 35% | 36.0% |

A second special is conditional on the first and much rarer
(`SECOND_SPECIAL_CHANCE`), capped at `MAX_SPECIALS_PER_PACK`.

The family comes from `SPECIAL_FAMILY_WEIGHTS` — Team of the Week 58, Team of
the Season 14, World Cup 10, MOTM 8, iMOTM 5, TOTY 3, Record Breaker 1 — not
from how many of each the database holds.

**Legend is weighted to zero.** FUT Legends were an Xbox exclusive so they
belong in a 360 pack, but nothing here has ever drawn one and whether the card
renders is unknown. An unknown card on the pack screen is how screens freeze.
Raise it deliberately, with the console in front of you.

## The elite cap

No pack hands out more than `MAX_ELITE_PER_PACK` cards rated 90 or better,
however the bands fall. Odds are a statement about a card; this is the
statement about the pack. The cap drops the top bands from the draw rather
than redrawing, so it costs nothing and cannot loop.


# What the console showed

The first Premium Gold Pack opened after the composition change came out right
on the wire -- three players and nine other cards -- and wrong on the screen.
Five of the nine drew NOT FOUND art, all five labelled "Entraînement", all
showing +0. Two more drew blank card backs.

## Three members CardsDLL has no name for

The consumable item carried `definitionId`, `consumableType` and
`consumableMember`. **None of the three appears anywhere in CardsDLL** -- not
in the JSON member-name table, not anywhere else in the module.
`consumableMember` was invented in this file outright. A parser has no name for
them, so they were never read.

That is not merely wasted bytes. The PC revival hit a freeze inside the
CardsDLL purchased-items parser from exactly this shape -- an extra descriptive
field on a pack consumable -- and it froze on the **second** pack, not the
first.

Two more that the reference sends are absent from this build too,
`resourceGameYear` and the `rareFlag` capitalisation, so they are not sent
either.

## `cardassetid`, not `assetId`

A consumable's art id has its own member. Sending only `assetId` is why five of
nine cards drew NOT FOUND under one generic name: nothing resolved, so the
screen fell back to a family default.

## `resourceId` is the card's own database id

5001001 and up, straight out of `fcc_*`. It used to be derived from the art
(`RESOURCE_VERSION | assetId`), which gave every training card in the club one
shared id -- and that id is what `POST /item/resource/<id>` addresses, so the
apply route could not tell a +5 training card from a +15 one.

## Club items are out of the draw

Kits, badges, balls and stadiums carry resource ids invented in this file,
6000000 and up, because no table in `cards_ng_db` or `fifa_ng_db` names them.
Those were the two blank card backs. The non-player slots are consumables only
until real identities turn up; the club is still seeded with kits and badges,
which is where they came from.

## And a card that vanished

`withdraw` put a listing back only `if "assetId" in item`. The guard means "is
this a card at all", and `assetId` stopped answering that the moment a
consumable started carrying `cardassetid`. Withdrawing a listed contract
dropped it on the floor without a word -- the same failure as the pack cards
that were acknowledged and lost. It asks for `id` now.
