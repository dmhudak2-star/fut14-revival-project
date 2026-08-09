# Milestone: buy a player and put him in the club

Screenshots:
* `runtime/screens/ronaldo-000958.png` -- the purchase confirmed
* `runtime/screens/asg2-004222.png` -- the assign screen with the card on it
* `runtime/screens/asg3-004307.png` -- back to the market, card gone

The full loop works: search, buy, confirm, assign, keep -- and it survives a
relaunch.

```text
NOUV. ÉLÉMENTS 1 | ÉQUIPE ACTIVE 23/23 | LISTE TRANSFERTS 0/20000
A Choisir   X Envoyer au club   Y Placer sur la Liste des transferts
```

Cristiano Ronaldo, 98, bought for 20 200 and sent to the club. The journal:
`fut_item_move -> club: 37, pending: 0`.

## Three faults, one after the other

**`EXPIRE_TIME`.** The actions panel opened empty and "Temps restant" read
`--`. Four expiry shapes had been tried, all reasoned by analogy with the FUT
web app, two of which emptied the detail panel outright. The member CardsDLL
reads is `EXPIRE_TIME`, sitting directly beside `FUT_AUCTION_EXPIRED` -- the
localisation key of the message on screen. With it, the timer filled in and the
panel offered Faire offre, Acheter maintenant, Offre échange.

**`/offer`, not `/bid`.** Buying then failed with "cette liste a expiré" while
the timer read 23 h 59. The journal named the real cause in one line: `POST
/ut/game/fifa14/trade/{id}/offer`, unhandled.

**`purchased/items`.** The purchase went through, the coins were taken, and
"Assigner maintenant" backed out to the search screen without ever asking the
server to save anything. The trace showed the client reading `squad/active` and
`purchased/items` -- and that pile was empty, because the bid handler put the
card straight into the club. The pack flow, which has worked all along, goes
through the pending pile first. Bought cards now take the same route.

## What this keeps confirming

An error message on this title names a plausible cause, not the real one:

* the loading popup was not a broken login -- it was the tutorial feed;
* the expired listing was not expired -- the timer read 23 hours;
* "Assigner maintenant" reported nothing at all, and the fault was two screens
  away in a list nobody had thought to fill.

Every fix that landed first time came from the binary's strings or the journal.
Every guess reasoned from the web app cost a relaunch and produced nothing.


## Confirmed on console: the player who could not be bought

One particular player -- Benatia -- could never be bought into the club while
everyone else could. Two separate faults, neither of them about him:

**Market item ids came from the page position.** `MARKET_ITEM_ID_BASE + offset
+ index`, so the same slot in the same search always produced the same item id.
The club refuses an id it already holds, so the second attempt at that slot was
dropped on arrival. Ids are now issued per listing served.

**Buying hid every version of the player.** A purchase excluded the whole asset
id from later searches, and the three Benatia cards -- 90, 86 and 84 -- share
one. Buying the 90 took all three off the market. That exclusion is gone: a
market carries many copies of the same card, which is what a market is.

A test had encoded the wrong behaviour here, requiring a bought card to vanish
from the market. It was corrected rather than the code.

Confirmed working by the user: Benatia bought and held.
