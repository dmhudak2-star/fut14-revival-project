# The cups

`Compétition Joueur Solo` was the one FUT mode still served empty. This records
where its shape came from, because the last attempt at it froze the title.

## Why the list was empty

An earlier catalogue was generated from a guessed schema — `tournamentId`,
`name`, `level`, `prize`, `rounds`, `currentRound`, `entryFee`, `active`,
`won` — and opening the mode froze the console outright. The list was emptied
and left that way, with the note that the fields had to come from the binary
first.

They now do.

## Where the shape comes from

`CardsDLL`'s `.rdata` carries its own JSON member-name table: a contiguous run
of null-terminated names in descending sort order, between `trophiesOffline`
and `kitsHome`. Every member served below appears in it:

    treeType  numTeams  numRounds  matchlength  rounds  roundId
    rewardMultiplier  awardSet  awardType  halid  elgReq
    eligibilityOperation  aigroup  unlockreq  lock
    triesMax  triesPeriod  triesRemaining  nextReset
    starttime  timeUntilStart  timeUntilEnd  visStart  visEnd
    trophyResourceId  trophyUserCount  teamId  knockout

None of `name`, `level`, `entryFee`, `active` or `won` appears anywhere in it.

The freeze itself is the first line of that list against the old one: `rounds`
is an **array of round records**, each `{id, difficulty, rewardMultiplier,
coins}`. The old catalogue served it as a count. A number where the parser
walks records is a sufficient explanation on its own, and the shape was
confirmed independently by a FIFA 17 revival hitting the same crash from the
same simplification.

## What the client sends back

The progress body is built by the client, not by us, and `.rdata` carries the
format string it is assembled from:

    {"round":%d,"dataVersion":%d,"tournamentData":"
    ","progressDataVersion":%d,"progressData":"

There are **two** near-identical strings here and telling them apart matters.
The one above sits among the cup constants — `TOO_MANY_TOURNAMENTS`, `JOINED`,
`LOCKED_TROPHIES`, `LOCKED_RETRY` — which is what identifies it. The other,

    {"round":%d,"dataVersion":%d,"data":"

is followed immediately by `%d/division/%d` and belongs to seasons. Reading it
as the cup's format was a misidentification, corrected here: the tournament
blob is `tournamentData`, exactly as the FIFA 17 shape has it. `data` is still
accepted on the way in, and the reply also spells the progress blob
`progressdata`, which is how the name table carries it beside the camel-cased
`progressDataVersion`. An unrecognised sibling at the top level is skipped, as
it is everywhere else in this protocol.

## The routes

The URL template table gives `ut/%s/tournament`, `ut/%s/tournament/user` and
`ut/delete/%s/tournament/user`. The Xbox client was journalled asking for
`tournament/list`, which is not in the table, so both spellings serve the
catalogue.

    GET  /ut/game/fifa14/tournament              the catalogue
    GET  /ut/game/fifa14/tournament/list         the same document
    GET  /ut/game/fifa14/tournament/teams        the draw, {"teamId":[...]}
         ?groupId=&count=                        the module's own query
    GET  /ut/game/fifa14/tournament/user/list    ids with a saved run
    GET  /ut/game/fifa14/tournament/user/<id>    one saved run
    PUT  /ut/game/fifa14/tournament/user/<id>    save it
    POST /ut/delete/game/fifa14/tournament/user/<id>   quit the cup

A cup never entered answers `{"tournamentId": <id>}` and nothing else.
Inventing a round or an empty blob for a cup that was never played would put
the screen into a tournament that does not exist.

`tournament/user/list` used to name every cup in the catalogue. That told the
screen the club was mid-run in all of them while no progress existed for any;
it now names only the cups actually entered.

## Seasons: three shapes, three failures

Not solved. Served empty by default; see `docs/SEASONS.md` for what was tried
and what each attempt cost.

## Still unverified

The catalogue has not yet been consumed by the console — the mode has not been
opened since the change. Until it has, the claim here is that the shape matches
the binary's own names, not that the screen renders.

Round counts, coin values and the team draw are choices, not findings: the
binary names the fields, it does not say what a cup should pay. The team ids
are real EA club ids but were not read out of `fifa_ng_db`.
