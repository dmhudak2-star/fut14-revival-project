# Milestone: a working balance and a browsable market

Screenshot: `runtime/screens/coins-091418.png` — the club header reading
50 200 credits, mid-transition into a transfer search.

## The balance

50000 at the start, 50200 after one quick sell. Confirmed on the console.

What made this hard to find is that **the header does not read the balance at
login**. It reads whichever response last carried it, and that response is the
quick sell. So the symptom was: a clean zero until the first sale, then
-842150451.

-842150451 is 0xCDCDCDCD, the fill pattern for uninitialised memory. That was
the whole diagnosis: our reply to `/ut/delete/game/fifa14/item` was `{}`, so the
parser never wrote the field, and the header printed whatever the allocator had
left there. Three wrong guesses preceded it -- `{"credits":N}` on the credits
endpoint, a `currencies` array, and the account wrapped in `{"userInfo":{...}}`,
which was the worst of them because a wrapper the parser does not recognise
breaks the parse outright. An unrecognised *sibling* at the top level is simply
skipped, which is why the total now goes out as `totalCredits`, `credits` and
`coins` together. `totalCredits` is the one in CardsDLL's JSON member table, at
`0x8902FD90`, between `total` and `totalGames`.

The other half was that every card carried `discardValue: 0`, so a quick sell
would have paid nothing even once the reply was right.

## The market

`/ut/game/fifa14/transfermarket` is served from the 14019-card catalogue, with
the game's own filters -- position, rarity, nation, league, club, rating range
-- and a price derived from rating and rare flag. The club search
(`/ut/game/fifa14/club?`) still searches the club, which is 92 cards: a club is
what you own, a market is what you do not.

`trade/status` had to be added alongside it. The client polls it immediately
after a search, and its 404 raised an error popup over a search that had
otherwise worked and displayed its players.

## Still open

Buying. The listings are generated per search and nothing debits the wallet or
moves an item into the club yet.
