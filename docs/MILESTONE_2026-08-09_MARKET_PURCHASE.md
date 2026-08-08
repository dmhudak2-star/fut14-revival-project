# Milestone: a player bought on the transfer market

Screenshot: `runtime/screens/ronaldo-000958.png`

> Votre achat sur le Marché des transferts a été effectué.

Cristiano Ronaldo, 98 rated, for 20 200 coins. The balance went from 988 750 to
968 550 -- exactly the buy-now price -- and the listing shows the bid standing
at 20 200 with a green tick.

## The two things that were wrong, and why they took so long

**`EXPIRE_TIME`.** Four expiry variants had been tried against the screen and
all four were reasoned by analogy with the FUT web app: `expires` as seconds,
plus absolute bounds, plus `duration` and `endDateTime`, then `expires` as an
instant. Two of them emptied the detail panel outright.

The member CardsDLL actually reads is `EXPIRE_TIME`, and it sits directly
beside `FUT_AUCTION_EXPIRED` in the binary -- the localisation key of the very
message being shown. It had never been sent. With it, "Temps restant" filled in
at 23 h 59 and the actions panel opened complete: Faire offre, Acheter
maintenant, Offre échange, Objectifs de transferts, Comparer prix.

**`/offer`, not `/bid`.** With the panel working, buying still failed with
"Désolé, cette liste a expiré" -- while the timer read 23 hours 59 minutes. The
message named a cause that was visibly false.

The journal named the real one: `POST /ut/game/fifa14/trade/{id}/offer`,
unhandled. The handler matched only `/bid`.

## What this says about method

Every guess reasoned from the web app cost a relaunch and produced nothing.
Every answer read out of the binary or the journal landed first time:

* `trade/status?tradeIds=` came from the string sitting beside "Auction state
  is invalid for bidding";
* `EXPIRE_TIME` came from the string beside `FUT_AUCTION_EXPIRED`;
* `/offer` came from the journal, in one line.

An error message on this title names a plausible cause, not the actual one. The
loading popup was not a broken login, the expired listing was not expired, and
"aucune Équipe de la semaine" is not a missing squad.
