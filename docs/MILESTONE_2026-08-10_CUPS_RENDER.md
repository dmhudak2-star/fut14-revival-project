# The cup screen opens

`Compétition Joueur Solo` renders. Three cups are listed and the console does
not freeze.

This is the screen that froze the title outright when a generated catalogue was
last served, and the reason the tournament list had been answered empty ever
since, with the note that its fields had to come from the binary first.

## What settled it

One line. `rounds` is an array of round records:

    "rounds": [{"id":1,"difficulty":1,"rewardMultiplier":1,"coins":150}, ...]

The catalogue that froze served it as a count -- `"rounds": 5` -- alongside
`name`, `level`, `entryFee`, `active` and `won`, none of which appear in
CardsDLL's own JSON member-name table. That table, a contiguous sorted run in
`.rdata` between `trophiesOffline` and `kitsHome`, is where every member of the
document served now comes from.

## The traffic

    11:50:37  GET /ut/game/fifa14/tournament/list          fut_mode_request
    11:50:37  GET /fut/items/images/trophies/xbl2/item.big
    11:50:37  GET /ut/game/fifa14/tournament/user/list      fut_mode_request

No unhandled route in the whole run. `tournament/teams` was not requested at
this point; the draw is fetched on entering a cup, not on listing them.

## The trophies had no art, and the cause was the same fault in miniature

The cups listed without trophy images.

The obvious suspect was wrong. The client does fetch
`/fut/items/images/trophies/xbl2/item.big`, and the server's `/fut/items/`
handler answers every path under that prefix with `{"itemData":[]}` -- sixteen
bytes of JSON where a binary archive was asked for, measurable as
`status=200 bytes=16 type=application/json`. That is a real defect and it is
still there. It is not this one.

The art is local. `cards0.big` carries seventy trophies under

    data/ui/external/ion_fut/artassets/fcctournamenttrophies/

named `trophy_<id>_<tier>.big` for ids **1100..1169**, each in bronze, silver,
gold and dark, with matching `item` thumbnails -- and, beside them, a
`notfound.big`.

The catalogue served `trophyResourceId: 0` for every cup. Zero is not a
trophy, so the screen drew the placeholder. The same shape of error as the
freeze: a field filled with a plausible default instead of a value read out of
what the game ships.

Real ids are now served, and the console's behaviour confirms the field: with
`trophyResourceId` 0 it asked for `/fut/items/xbl2/0.json` once per cup; with
1100, 1101 and 1102 it asked for those three. The field was right all along --
it was called wrong here first, on the strength of the art not changing, while
the journal was already showing the request follow it.

**The art still does not appear, and probably cannot.** A PC revival of the
same game, shared independently, answers the trophy bundle with a structurally
valid but empty BIGF and says so in as many words: *"Both are retired trophy
CDN bundles... This is intentionally an empty compatibility response, not
invented trophy art."* It also records the same degenerate
`/trophies/pc/.big` request from a cup with `trophyResourceId` 0 that was seen
here as `/trophies/xbl2/.big`. Two builds, two platforms, the same dead CDN.

What that build does not have is the Xbox's own `cards0.big`, which carries
seventy real trophies. Whether the FUT cup screen can be pointed at them is the
one lead left, and it is untested.

## A defect fixed on the way, from the same source

Everything under `/fut/items/` was answered with `{"itemData":[]}`, including
paths ending in `.big` -- sixteen bytes of JSON where a binary container was
asked for. `/fut/items/images/**.big` now returns a real, empty BIGF: magic,
declared size, zero directory entries, header size. It makes the response
parseable rather than wrong. It does not paint a trophy.

## What is still untested

`tournament/teams` and `tournament/user/<id>` have never been exercised by the
console -- they are reached on entering a cup, not on listing them. Both are
implemented and covered by tests against the local server only.
