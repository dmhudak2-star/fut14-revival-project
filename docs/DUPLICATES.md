# Duplicates in a pack

## The trap

Filling `duplicateItemIdList` with the ids of the **newly opened** cards is
wrong. Here it froze the title outright — the screen was being told to compare
each card against itself. A PC revival of the same game reports the other
symptom from the same mistake: the card rendered as a normal item, and *Send
All to Club* then failed with error 472.

That is the one thing that must never go back.

## What goes out now

Both spellings, because they are read in different places:

* `duplicateItemId` on the card itself, naming the owned card it repeats;
* `duplicateItemIdList` carrying the same pairing as records:

      {"itemId": <new>, "duplicateItemId": <owned>}

The list was empty here for a long time, on the reasoning that the per-card
field was enough. It is not. From the author of the PC build, about FIFA 14
specifically:

> don't rely on `"duplicate": true` — on 14 the pack UI only really recognised
> it when i returned a `duplicateItemIdList` pairing the new pack item ID with
> the existing club item ID

So a repeat rendered as an ordinary card however carefully the card itself was
marked.

## Detect on resourceId, not assetId

Same source, and it was a real fault here:

> detect it by exact `resourceId`, not just `assetId`, otherwise specials can
> get falsely flagged

A player's versions share his asset id. A Team of the Season Ruffier and a Rare
Gold Ruffier are both asset `167628` and are not the same card. This server
keyed on `(assetId, rareflag)`, which flags the special as a repeat of the
ordinary one. `PackShop._signature` now keys on `resourceId` exactly, falling
back to the asset and rare flag only for a card that carries no resource id.

## The card that vanished

Separate fault, found while chasing a TOTS Ruffier that was drawn, shown, sent
to the club, confirmed — and existed nowhere afterwards.

`CardActions.move` answered every id with

    {"id": N, "success": true, "reason": "", "errorCode": 0}

including ids it had never held. Nothing was kept and the client was told it
worked. An unknown id now answers `success: false` with error 461, the ids are
collected in `CardActions.unmatched`, and `fut_item_move` journals them.

`fut_pack_opened` also journals what was actually drawn — id, asset, rating,
rarity. Without that a card lost between the pack screen and the club cannot be
identified afterwards, which is why that Ruffier had to be restored from his
catalogue entry rather than from any record of the pull.

Still open: **why** the id was unknown. The session dropped shortly before, and
the client may hold cards the server has already taken out of `pending`. The
new journal should say, next time it happens.

## On Xbox the flag is not served at all

Tested on the console: a pack containing a repeat, correctly detected and sent
out with both the per-card `duplicateItemId` and the paired
`duplicateItemIdList`. **The screen still showed it as an ordinary card.**

The binary says why. Two strings, neither of them a JSON member name:

`HAS_DUPLICATE` at 79580 sits in a run of frontend property keys —
`CALLBACK`, `CONSUMABLE_TYPE`, `FIRST_WON`, `TOURNAMENT_ID`, `TIMES_WON`,
`SUB_TYPE`, `IS_ACTIVE`, `HAS_DUPLICATE`.

`GetCardDuplicate` at 82252 sits in a run of native binding names —
`GetPlayerCardInfo`, `GetSpecialCardInfo`, `GetCardDetails`,
`GetCardCategory`, `GetUserCardInfo`, `GetCardDuplicate`.

So the pack screen asks CardsDLL whether a card is a duplicate and CardsDLL
answers from its own state. No server response shape can produce that flag —
not the per-card member, not the paired list, not `duplicate: true`. The PC
finding that the paired list is what the UI reads does not transfer; that
client's CardsDLL is a different binary.

The next step is a passive trace of `GetCardDuplicate` to see what it consults
— most likely CardsDLL's own club collection, which would make this a question
of when and how completely the club is loaded before a pack is shown, not a
question of JSON at all.

## What the three changes were worth anyway

None of them made the flag appear, and only one was aimed at it.

* **resourceId detection** — correct on its own merits. Keying on
  `(assetId, rareflag)` flags a Team of the Season Ruffier as a repeat of the
  ordinary Ruffier. That was a live fault regardless of what the UI reads.
* **the paired list** — no observable effect. It did not freeze the pack
  screen, which is at least evidence that pairs are safe where bare new ids
  were not. Kept, because it is the shape a sibling build validated and it
  costs nothing; an unrecognised sibling is skipped.
* **the lost-card fix** — unrelated to duplicates and the most valuable of the
  three. See above.
