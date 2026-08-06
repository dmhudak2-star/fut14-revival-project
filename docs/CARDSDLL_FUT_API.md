# CardsDLL native FUT API

`CardsDLLzf.xex.dll` is mapped at `0x89000000` only once the title enters FUT.
Its FUT surface is published as a table of 12-byte records `{?, handler, name}`
built by the initializer at `0x89107480`. The table below was reconstructed
statically from a runtime dump of the module by emulating that initializer's
`lis`/`addi`/`stw` sequence.

Addresses are build-specific and belong to this supported image; rediscover them
after any title update.

`tools/fifa14_fut_api_trace.py` hooks these handlers by name, and its
`arm-on-load` action arms on the module's `modload` notification so the first
call of a run is not missed.

## Service interface

`LoginToFUT` and its neighbours are glue: they load the FUT service object from
the global at `0x892213A0` and call one of its vtable slots. The live object
observed was `0xB5AA3018` with vtable `0x89008E90`:

| Slot | Address | Reached from |
| --- | --- | --- |
| `+0x00` | `0x8908F5E0` | |
| `+0x04` | `0x8908D350` | `LoginToFUT` |
| `+0x08` | `0x8908D3D0` | `FirstTimeInit` |
| `+0x0C` | `0x8908B4F0` | |
| `+0x10` | `0x8908B540` | |
| `+0x14` | `0x8908B518` | |
| `+0x18` | `0x8908D438` | |
| `+0x1C` | `0x8908D4A8` | `CardsDownloaded` |
| `+0x20` | `0x8908F630` | |
| `+0x24` | `0x8908ED78` | |
| `+0x28` | `0x8908B568` | |
| `+0x2C` | `0x8908D520` | |
| `+0x30` | `0x8908D5A0` | |
| `+0x34` | `0x8908FD28` | |
| `+0x38` | `0x89090270` | |
| `+0x3C` | `0x890906B0` | |

`GetIdentityData` is the exception: it resolves a different object through
`0x89069F20` and calls its `+0x44` and `+0x14` slots.

## Operation table

| Record | Operation | Handler |
| --- | --- | --- |
| `0x000` | `LoginToFUT` | `0x89105D18` |
| `0x00C` | `FirstTimeInit` | `0x89105D50` |
| `0x018` | `FirstTimeInitWC` | `0x89105D88` |
| `0x024` | `InitFUTDatabaseFromWC` | `0x89105DC0` |
| `0x030` | `ExitFUTWC` | `0x89105DF8` |
| `0x03C` | `FinalShutdown` | `0x89105E30` |
| `0x048` | `CardsDownloaded` | `0x89105E68` |
| `0x054` | `GetIdentityData` | `0x89105EA0` |
| `0x060` | `GetUserStatsData` | `0x89105F48` |
| `0x06C` | `CreateClub` | `0x891061E0` |
| `0x078` | `GetNumCardsWithXOrLessGamesLeft` | `0x89106470` |
| `0x090` | `GetCardInfoForCardsWithXOrLessGamesLeft` | `0x89106530` |
| `0x09C` | `CreateMatch` | `0x89106218` |
| `0x0A8` | `MatchReady` | `0x89226270` |
| `0x0B4` | `CancelMatch` | `0x892262C8` |
| `0x0C0` | `SetDivision` | `0x89105FF0` |
| `0x0CC` | `SetTournamentID` | `0x89106048` |
| `0x0D8` | `SetTournamentRound` | `0x891060A0` |
| `0x0E4` | `AddFUTMatchmaking` | `0x891060F8` |
| `0x0F0` | `ServiceQuickMatch` | `0x89106130` |
| `0x0FC` | `ServiceCreateSession` | `0x89106188` |
| `0x108` | `GetAwardedCredits` | `0x89106320` |
| `0x114` | `GetRandomOpponent` | `0x891063C8` |
| `0x120` | `RecordTelemetryData` | `0x89107378` |
| `0x12C` | `SetHighlightedCardId` | `0x892265D8` |
| `0x150` | `EnableControllerVibration` | `0x8922CCB8` |
| `0x15C` | `DisableControllerVibration` | `0x89226718` |
| `0x180` | `StopFUTAudioCommentary` | `0x89106800` |
| `0x18C` | `CheckIfStringIsProfane` | `0x89106838` |
| `0x198` | `FileExists` | `0x891068A0` |
| `0x1A4` | `GetFutVersion` | `0x891068F8` |
| `0x1B0` | `SetPOW` | `0x891069A0` |
| `0x1BC` | `CheckPOWUnlockable` | `0x891069F8` |
| `0x1C8` | `CheckCoinBoostUnlockable` | `0x89106A50` |
| `0x1D4` | `ShowPOWOverlay` | `0x89106A88` |
| `0x1E0` | `GetMaxPileSize` | `0x89106AE0` |
| `0x1EC` | `GetConsumablePileSize` | `0x89106B88` |
| `0x1F8` | `GetFutPlayerPositions` | `0x89106BC0` |
| `0x210` | `GetFifaToFutPositionMapping` | `0x8910D898` |
| `0x21C` | `GetAuctionTunables` | `0x89106DB8` |
| `0x228` | `RetrieveEventFeed` | `0x89226E60` |
| `0x234` | `GetEventFeed` | `0x89106EB8` |
| `0x240` | `MarkFeedAsRead` | `0x89106F60` |
| `0x24C` | `RetrieveUTStats` | `0x89106F98` |
| `0x258` | `GetUTStats` | `0x89106FF0` |
| `0x264` | `GetPositionsForFormation` | `0x89107098` |
| `0x270` | `GetPlayerLinksForPosition` | `0x89107140` |
| `0x27C` | `GetMyFutTeamId` | `0x891071F8` |
| `0x288` | `GetOpponentFutTeamId` | `0x89107230` |
| `0x294` | `IsFutCustomTeam` | `0x89107268` |
| `0x2A0` | `GetLegendStats` | `0x891072C0` |

## Route table

CardsDLL carries its own HTTP route table, separate from the `pow/auth` that
`powdllzf.xex.dll` uses for EA Sports Football Club. The `%s` is the game
segment, so `ut/%s/club` resolves to `ut/game/fifa14/club`.

Notably CardsDLL's own authentication route is `ut/auth`, and it has never been
observed on the wire: the only auth request the local server receives is
powdllzf's `pow/auth`. Whatever stalls the FUT bootstrap does so before
CardsDLL issues any request of its own.

| Name | Route |
| --- | --- |
| `V2STORE` | `ut/v2/%s/store` |
| `PHISHING` | `ut/%s/phishing` |
| `DELETE_AUTH` | `ut/delete/auth` |
| `AUTH` | `ut/auth` |
| `CLIENTDATA` | `ut/%s/clientdata` |
| `DELETETRADE` | `ut/delete/%s/trade` |
| `TRADE` | `ut/%s/trade` |
| `TRADEPILE` | `ut/%s/tradePile` |
| `DELETEWATCHLIST` | `ut/delete/%s/watchList` |
| `WATCHLIST` | `ut/%s/watchList` |
| `STORE` | `ut/%s/store` |
| `PURCHASED` | `ut/%s/purchased` |
| `SEASONRESET` | `ut/%s/season/%%s/reset` |
| `SEASONUSER_ALTER` | `ut/%s/season/%%s/user` |
| `SEASONUSER` | `ut/%s/season/user` |
| `SEASON` | `ut/%s/season` |
| `TOURNAMENTDELETE` | `ut/delete/%s/tournament/user` |
| `TOURNAMENTUSER` | `ut/%s/tournament/user` |
| `TOURNAMENT` | `ut/%s/tournament` |
| `DELETEITEMS` | `ut/delete/%s/item` |
| `ITEMS_BY_RES` | `ut/%s/item/resource` |
| `ITEMS` | `ut/%s/item` |
| `DELETEUSER` | `ut/delete/%s/user` |
| `USER` | `ut/%s/user` |
| `UT` | `ut/%s` |
| `PAFPRACTICE` | `ut/%s/activeMessage` |
| `LBDEFAULT` | `ut/%s/leaderboards` |
| `LBOPTIONS` | `ut/%s/leaderboards/options` |
| `DELETE_SQUAD` | `ut/delete/%s/squad` |
| `SQUAD` | `ut/%s/squad` |
| `CLUB_USER` | `ut/%s/clubUser` |
| `AUCTIONHOUSE` | `ut/%s/auctionhouse` |
