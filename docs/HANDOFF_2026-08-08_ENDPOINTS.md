# Handoff: the FUT endpoint sweep

Working copy `~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival`, branch
`main`. 255 tests pass. Console `192.168.1.25`, server `192.168.1.36`.

## Delivered since the last handoff

**The store sells all nine packs.** Bronze 400, premium bronze 750, silver
2500, premium silver 3750, gold 5000, premium gold 7500, jumbo gold 10000, gold
players 15000, premium gold players 25000 -- with their own counts and
guaranteed rares. Drawing respects the tier, so a bronze pack cannot hand you
Messi: bronze draws ratings 0-64, silver 65-74, gold 75-99, and the rare slots
fill first. Purchases name the pack, so the bronze costs 400 rather than the
gold default.

**The club holds 132 items, not 97.** Contracts, fitness, healing, training,
position and chemistry-style consumables with real grades and amounts, plus
kits, badges, stadiums, balls, managers and staff. The Consommables, Éléments
club and Personnel tabs each filter on an item type and had nothing to filter.

**Seasons, cups and Team of the Week.** Ten divisions with match counts,
promotion thresholds and win bonuses; the club starts in Division 10; four
active cups with rounds and prizes; a Team of the Week of 23 rare cards rated
80 and above. All three screens treat an empty list as an error rather than as
"nothing available" -- the same behaviour as `fcc_login2` with an empty squad --
so an empty fixture is not a neutral answer.

**The club search filters.** A search for a Cameroonian centre back used to
list the whole squad, because every card carried nation 0 and league 0: the
icebreaker packs have no position, nation or league. Cards are enriched from
the catalogue, and the club search honours its own parameter names -- `level`,
`nation`, `league`, `team`, `position`, `count` -- which are not the market's.

**Bidding and buying.** `/trade/{id}/bid`. At or above buy-now the auction
closes and the card joins the club; below it the bid stands; beyond the balance
it is refused with `INSUFFICIENT_COINS`. Listings are remembered when served,
because they are generated per search.

## Open, and honestly so

**The market's action panel opens empty.** Pressing A on a listing swaps the
Actions entry for Cancel and shows nothing. "Temps restant" reads `--`. Four
variants of the expiry were tried against the screen:

| sent | result |
|---|---|
| `expires` alone, seconds | prices shown, no Actions entry |
| plus `startTime`, `endtime` | prices shown, **Actions entry appears** |
| plus `duration`, `endDateTime` | detail panel entirely empty |
| `expires` as an absolute instant | detail panel entirely empty |

The relative form with the two integer bounds is the best of them, and it is
what is committed. Permuting this field further is not the next step: the next
step is to trace what the auction parser actually reads. Adding four members at
once was a mistake -- it made it impossible to say which one broke the panel.

**The FUT home header shows a zero balance** while the store and market show
the true figure. Bisected: adding the balance to every FUT reply freezes the
login at `clientdata/tutorialpopups`, and adding it to `clientdata/userHubData`
freezes it there instead. Those parsers reject unknown members. `hub`,
`eventfeed` and `clubUser` tolerate them and carrying it there changes nothing,
so the header reads something else.

**EAS FC is still disconnected.** It is a second Blaze connection to
`pal.gt.easfc.ea.com:8094`, a hostname the launch patch does not rewrite. Five
POW config keys now point it here; unverified.

**No match has been played.**

## The console, right now

Stuck in its attract-video loop and not reaching the main menu. START is being
delivered -- the mailbox packet counter advances while the frame counter drains
-- and the loop simply does not exit. The screen navigator also matches dark
video frames against the `fut_error` signature, so its readings during the
intro are unreliable.

Two things that recur and are worth checking before diagnosing anything else:

* the virtual pad keeps its last `buttons` value after `remaining_frames`
  reaches zero. `restore` then `apply` clears it; `status` should read
  `buttons=0x0000` at rest.
* restarting the Blaze server during a FUT session ejects the title back to the
  FIFA main menu. Restart it before entering FUT, never during.

## The sequence that works, when it works

```text
clear runtime/local-account.json
restart the Blaze server on the cleared file
fifa14_early_local_server.py --launch-title
navigate to the FIFA main menu
fifa14_tu3_helperfunctions_runtime_patch.py     <- here, not later
select Ultimate Team
```

## The two remaining blockers, and the names that matter

Both were guessed at twice and both guesses failed. The strings say where to
look next, and neither answer is a response-field permutation.

**"Désolé, cette liste a expiré."** The localisation key is
`FUT_AUCTION_EXPIRED`, and sitting near it in CardsDLL:

```text
EXPIRE_TIME
TIME_SALE_EXPIRED
XMINUTES_BEFORE_EXPIRY
WARN_EXPIRY
/expired
```

`EXPIRE_TIME` is a member name this server has never sent. The auction record
carries `expires`, `startTime` and `endtime`; none of those is `EXPIRE_TIME`.
That is the first thing to try, and unlike the four expiry variants already
tried it comes from the binary rather than from analogy with the web app.

**Team of the Week.** `clientdata/totw` is served and accepted; the screen then
rejects what `user/list` returns. The relevant names are:

```text
GetTOTWSquads
GetGameHubTOTWData
EVENT_CARDS_REQUEST_TOTW_CHALLENGE_DATA_SUCCESS
EVENT_CARDS_REQUEST_TOTW_CHALLENGE_DATA_FAILURE
TOTW_CHALLENGE
GOTO_TOTW_CHALLENGE
external/ion_fut/components/Tile/MetroPanel_TOTWChallenge.swf
```

So the screen is not asking for a squad and being given the wrong shape -- it
is asking for **challenge data**, which is a different dataset entirely, and
the failure event has its own name. Serving a better-shaped squad will not fix
it; the challenge document has to be found.

Neither of these needs another relaunch to investigate. Both need more of
CardsDLL dumped than the 192 KB read so far -- the member table runs past it,
which is how `expires` was found at `0x89030D30` only after extending the dump.

## Team of the Week: what is known, and what is not

Every TOTW string in CardsDLL, with addresses:

```text
0x89012148  GetGameHubTOTWData
0x8901224C  GetTOTWSquads
0x89012554  EVENT_CARDS_REQUEST_TOTW_CHALLENGE_DATA_FAILURE
0x89012584  EVENT_CARDS_REQUEST_TOTW_CHALLENGE_DATA_SUCCESS
0x89015D10  GOTO_TOTW_CHALLENGE
0x89016184  external/ion_fut/components/Tile/MetroPanel_TOTWChallenge.swf
0x890161C4  CentralTOTW
0x890161D0  TOTWDefault
0x890161DC  FUT_GH_TOTW_C_0
0x8902D7DC  /totw
```

The only URL fragments for the feature are `/totw` and `/userHubData`, both of
which are `clientdata` paths this server already answers. **So the endpoint was
never missing.** `clientdata/totw` is fetched and accepted; the screen still
reports none available.

What the names say is that the screen wants *challenge* data --
`TOTW_CHALLENGE`, `GOTO_TOTW_CHALLENGE`, `MetroPanel_TOTWChallenge.swf`, and a
request event with its own success and failure cases. A squad is not that.

**No shape for that document has been found**, and it is not being guessed at.
Two invented shapes froze the title outright tonight -- `duplicateItemIdList`
filled with the wrong ids, and a generated cup list -- each costing a relaunch.
The squad document previously placed on `/ut/game/fifa14/user/list` was itself
a guess and has been withdrawn; that path is very likely the FUT user list.

The next step is to disassemble around `GetTOTWSquads` (`0x8901224C`) and the
challenge request event, and read what the response parser expects. Not to try
another shape.
