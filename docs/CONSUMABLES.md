# Applying a consumable

Until now the club could hold a contract, show it, filter on it and count it,
and there was no route that did anything with one. Every consumable in this
FUT was decoration.

## The call

    POST /ut/game/fifa14/item/resource/<resourceId>
    {"apply":[{"id":<targetItemId>}]}

    200 {}                                   applied
    400 {"code":"400","reason":"..."}        refused, and nothing was spent

`apply` is in CardsDLL's own member-name table, immediately beside `applyTo`
— the same descending `.rdata` run that settled the tournament document, see
`docs/TOURNAMENTS.md`. Both spellings are accepted. Retail answers this call
by status, so success is an empty document rather than a card list.

The path names the *definition*, not one particular card, so any owned copy is
spent. `resourceId` is the card's own database id — 5001001 and up — and
identifies exactly one definition. It used to be derived from the card art,
which gave every training card in the club a single shared id and left this
call unable to tell a +5 card from a +15 one.

## What each card does

The subtypes are the game's own, read out of `cards_ng_db.db` by
`tools/build_consumables.py`.

| subtype | card | effect |
|---|---|---|
| 201 | player contract | `contract` += the row's gold/silver/bronze figure for the **target's** quality |
| 202 | manager contract | the same, on a manager or staff card |
| 211–217 | healing, by injury | `injuryGames` -= amount, if the injury matches |
| 218 | healing, any injury | the same, whatever the injury |
| 219 | player fitness | `fitness` += amount |
| 220 | squad fitness | the same, on every player in the active squad |
| 51–56, 61–66 | training | one attribute += amount |
| 57, 67 | training, all | all six attributes += amount |

A contract grants a different number of matches to a gold, a silver and a
bronze card, and `fcc_contractcards` carries all three columns. A bronze
contract on a gold player is worth 1 match and on a bronze player 8. The card
is named for its gold figure.

The healing order is the binary's, not a guess: `FUT_HEAD_HEALING`,
`FUT_UPPERBODY_HEALING`, `FUT_ARM_HEALING`, `FUT_BACK_HEALING`,
`FUT_KNEE_HEALING`, `FUT_LEG_HEALING`, `FUT_FOOT_HEALING`, in that order from
211. 214 (back) is simply not in the card database.

## Which training block is the keeper's — unresolved

`build_consumables.py` calls 51–57 the outfield block and 61–67 the keeper's.
The PC revival's catalogue says exactly the opposite. `fcc_trainingcards`
settles neither: it has no name column, only a card art id (3 against 1).

It does not matter for the effect. A player's `attributeList` holds six
entries indexed 0 to 5, and for a keeper those six slots hold the keeper's
attributes, so the offset inside the block *is* the attribute index either
way. What it would change is eligibility — whether a keeper training card may
be applied to an outfield player. This applies the card without enforcing a
rule it cannot prove, rather than refusing valid applications on a coin flip.

## What is refused, and why that is the right answer

Subtypes 91–136 and 232 are **not applied**. They are refused with a reason,
and the request is recorded.

This server's catalogue calls 91–110 and 121–136 play styles
(`consumablesTrainingPlayerPlayStyle`, `consumablesTrainingGkPlayStyle` — both
are real CardsDLL member names). The PC revival's catalogue calls 91–110
position changes with an explicit transition (`CM→CAM`, `ST→CF`, …) and marks
121–136 unsupported. The binary carries both families:

    FUT_CONSUMABLE_PLAYERSTYLE      FUT_CONSUMABLE_GK_PLAYERSTYLE
    FUT_CONSUMABLE_POSITIONMOD      FUT_CONSUMABLE_FORMATIONMOD
    FUT_PLAYSTYLE_%d

`FUT_PLAYSTYLE_%d` proves play styles are keyed by an integer in some range.
It does not prove which range. Writing `playStyle` or `preferredPosition` on
the strength of a coin flip changes the wrong field on a real card, and the
card is spent either way.

So each refusal appends to `ConsumableRack.refused` and the server journals it
as `fut_consumable_refused` with the subtype, the target, and the target's
current position and play style. **One application from the console names the
family**: the screen tells the player what the card was, and the journal says
what was asked. That converts a coin flip into an observation.

## Persistence

A card the club started with, changed since, is neither `acquired` nor `sold`
— it is still owned and it was never bought. The save now carries a `changed`
list for exactly that, and loads it *in place*, because the squad holds the
same objects and replacing them would leave the eleven pointing at the old
ones.

Without it, a contract applied to a seeded player was spent — the consumable
lands in `sold` — and the contract it bought was forgotten on the next launch.

## Refusing costs nothing

Nothing is written and nothing is spent until the effect has been decided.
A refusal leaves the club exactly as it was, which is what
`test_a_refused_card_is_not_spent` holds in place.

## "Pas d'élément disponible"

The apply route worked over HTTP and the picker in game offered nothing. The
counts were right -- `club/stats/consumables` reported 35 contracts, 63 healing
cards, 42 training -- and the picker never asked the server for the items at
all.

It did not need to. It reads the club the client already holds, and the club
the client held was **ninety players and nothing else**.

`club_response` sorts players first, then everything else by rating, and the
bare no-parameter response was capped by slicing that sorted list. With 92
players in the club, a cap of 90 cut off every consumable, kit, badge and
staff card. The counts said 35 contracts and the cached list contained none,
so the picker had nothing to offer.

`_bounded_club` now spends half the cap on players, best rated first, and
deals the rest round by round across every other kind the club holds. Nothing
is wiped out, and any share a kind cannot fill goes back to the players.

A mixed card is cheaper than a player card -- about 330 bytes against 860 --
so the same 77 KB budget holds more. Measured, not scaled: 90 mixed cards came
out at 48.4 KB where 90 players had been 75.8 KB, so the cap moved to 130,
which measures 70.5 KB and returns 65 players plus every other kind.

## What the picker actually reads

Four separate things had to be true, and each one on its own left the screen
saying "Pas d'élément disponible" over a club holding 65 contracts.

**1. FUT has two consumable item types, not six.** `development` and
`training`. The family lives in `cardsubtypeid`. This server sent `contract`,
`fitness`, `healing`, `playStyle` and `position` as `itemType` -- names
invented here to match the club screens' own filters. The club tab worked
because it filtered on the same invented names; nothing native did.

**2. The category is a path, and it is answered one at a time.** The picker
asks `/ut/game/fifa14/club/consumables/contracts`, then `/fitness`, then
`/development`. The route was matched with `startswith` and answered every one
of them with the club's whole stock -- it asked for contracts and got 242
cards of every family, which is not a list of contracts however many contracts
are in it. Read off the journal, the categories it names are: `development`,
`contracts`, `fitness`, `playStyle`, `healing`, `position`, `training`,
`managerLeagueModifier`. A category this club has no cards for answers empty
now, not everything.

**3. The cards have to cover every subtype the club owns.** `/clubUser`
carried a sample -- twelve a family -- and the families here span two subtype
blocks each: `training` covers 51-57 and 61-67, `playStyle` covers 91-110 and
121-136. The first twelve of either are all from the low block, so applying to
a goalkeeper read the keeper's block and the keeper's block was not there.
Cards are dealt by subtype now, a round at a time, capped at three copies:
every subtype before any second copy, and the players take what is left of the
budget.

**4. The counts are not read where they are written.** This is the one that
took longest. `/club/stats/consumables` served seventeen named scalar members,
every one of them correct and non-zero, and the popup read none of them.

The popup is backed by a sticker-book stats response, and it binds its
consumable-type buttons from `stat`/`entries` rows in context 6:

    {"contextId": 6, "contextValue": 0,
     "type": "consumablesContractPlayer", "typeValue": 38}

`contextId`, `contextValue`, `type`, `typeValue`, `stat` and `entries` are all
in CardsDLL, `StickerBook` with them. Both shapes go out now, carrying the same
counts -- other screens do read the scalars, so this is the same numbers twice
rather than a choice between them.

## How this was found, and how it should have been

By reading the PC revival's source and reconstructing what its client must be
receiving. That took several rounds of getting it wrong.

What actually ended it was making the journal log the **values** of query
parameters and the full sub-path, not just the parameter names. The category
routes appeared on the first menu opened afterwards. The journal had been
recording `query_keys` alone for the whole project, which makes every question
about what a screen asked for answerable only by guessing.
