# Milestone: the FUT hub is up

Screenshot: `runtime/screens/cycle-club-024754.png`

FIFA 14 Ultimate Team's manager-task hub is on screen:

```text
Fondateur FUT      CRÉDITS 0   POINTS FIFA 0   BILAN 0-0-0
PROGRÈS  0/13
  1. Changer le nom du club
  2. Disputer le premier match d'une compétition
  3. Disputer un match Saisons
  4. Acheter un élément sur le Marché des transferts
  5. Lister un joueur sur le Marché des transferts
  6. Défier l'Équipe de la semaine
```

with the Messi FUT 14 reward card and the retail tabs TÂCHES / DIDACTICIELS.
This is the real interface, with a club, a balance and a record -- not a
loading screen, not an error dialog.

## The last change that got here

`PUT /ut/game/fifa14/user/club`, answered `{}` instead of 404.

Confirming the club name PUTs to that path, and a 404 surfaces as "une erreur
s'est produite lors de la connexion à FIFA 14 Ultimate Team". The PC revival's
route list does not contain it -- the PC client renames through `clubUser` --
so it could only come from the live trace.

## The full path, in the order it was found

1. probe at `0x8910AAF8` removed -- it was suppressing `/ut/auth` outright
2. trusted device answered `trusted/exists true` -- removes the security
   question, and with it the account-state drift that killed `/ut/auth`
3. `accountinfo` serves an empty persona list -- no pre-existing FUT account
4. `/tutorials` answered **404** instead of an invented empty `<MESSAGES>` --
   this is what let `DoInitialLoginSteps` complete
5. `squad/list` and `squad/active` serve one squad summary -- an empty list is
   fatal to `fcc_login2`
6. `PUT user/club` answered `{}` -- the club rename

## Native trace

```text
LoginToFUT        1     GetIdentityData   4     CardsDownloaded   1
FirstTimeInit     1     GetUserStatsData  0     CreateClub        0
```

`CreateClub` staying at zero is consistent: the club already exists by the time
this screen renders, so the flow renames rather than creates.

## Next

Task 2, "Disputer le premier match d'une compétition", is the route to a first
match. The FUT API entries still at zero for that are `CreateMatch`,
`ServiceQuickMatch`, `ServiceCreateSession` and `GetRandomOpponent`.
