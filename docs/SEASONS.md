# Seasons: three shapes, three failures

Unsolved. `/ut/game/fifa14/season/list` answers `{"seasons":[]}` and
`/season/user` answers `{}` unless `FIFA14_SEASON_MODE=native` is set.

Empty is not a solution. It is the only answer that has never broken anything,
and it is what the PC revival carries here for the same reason.

## What was tried

**1. Invented members.** `division`, `matchesPlayed`, `matchesToPlay`,
`pointsToPromote`, `lost`, `coinsPerWin`, `trophiesWon`, plus a
`relegated`/`promoted` boolean pair. None of the nine appears anywhere in
CardsDLL's JSON member-name table, so the parser could not read one of them.
The mode opened and showed constructor defaults — no division, no record, no
reward. This was never actually observed on the console; it is what the code
served for weeks.

**2. Correct names, wrong structure.** Each name above replaced with one the
table carries: `divisionId`, `numMatches`, `thresholdPoint`, `seasonCoins`,
`gamesPlayed`, `seasonGamesLost`, `seasonTitlesWon`. The console answered

> Les saisons ne sont pas disponibles pour le moment. Veuillez réessayer plus
> tard.

The lesson is worth stating plainly, because checking the names felt like
diligence: **a name table proves a member exists, not where it lives.**
`thresholdPoint` is real — it lives inside `prizeSet`, not at the top level.
Every one of those nine names was verified present and the document was still
wrong.

**3. The full native record.** Taken from an independently built PC revival of
the same game, whose season record nests the fixture list in `matches` and the
rewards in `prizeSet`, both arrays of records — the same fault as a cup's
`rounds` served as a count, one level deeper. All 28 of its members were
checked against the Xbox name table and all 28 are present.

The FUT loader froze on entering the mode.

## What the freeze looked like

    13:17:21  GET /ut/game/fifa14/season/list      served
    13:17:21  GET /fut/items/xbl2/-1.json          x10, one per division
    13:17:21  GET /fut/items/images/trophies/xbl2/item.big
    13:17:21  GET /ut/game/fifa14/season/user      served
              nothing further

So the freeze is after both documents are served, in whatever the screen builds
from them. The console itself stayed healthy — XBDM kept answering and the
title kept running; only the FUT frontend hung.

`trophyResourceId: -1` is one confirmed mistake in that attempt. The PC build
uses -1 as a "no trophy" sentinel and notes that 0 made its client perform ten
meaningless item-0 lookups. On Xbox, -1 does exactly what 0 does: ten lookups
of `/fut/items/xbl2/-1.json`. A value proven on one platform is not evidence
for the other — which is the same reasoning that was applied correctly to the
member names in the same sitting, and then not applied here.

## What to try next

Reduce rather than guess. A freeze gives no error to read, so the only way
through is one variable at a time:

1. one division, no `matches`, no `prizeSet` — does the screen open?
2. add `prizeSet` alone;
3. add `matches` alone;
4. then both, then the remaining nine divisions.

Each step costs a server restart and a mode entry. Serving the whole record at
once, as was done here, produces a freeze and no information.

Also open: whether `trophyResourceId` should simply be absent rather than
carrying any number, and what `season/user`'s `round` should be for a season
never played — the PC build sends wire 1 for the first fixture and warns that
wire 0 becomes the client's 0xFFFF sentinel.
