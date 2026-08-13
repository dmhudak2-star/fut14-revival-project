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


## Attempt four, 13 August: the asset chain was not it

Two real defects sat on the seasons entry path and both were fixed the night
before, without knowing they were on it:

* `/fut/items/xbl2/-1.json` was answered with the blanket `{"itemData":[]}`,
  because the route matched digits only and the seasons screen asks for a
  negative id -- once per division, ten times. An empty definition is what
  makes the console build `trophies/xbl2/.big` with no basename.
* the empty BIG archive declared its own size big-endian, so sixteen bytes
  announced themselves as 0x10000000 -- 268 megabytes.

Both are visible in this attempt's journal as fixed: the ten `-1.json` requests
each come back with 151 bytes and a real `assetName`, and the console then asks
for `trophies/xbl2/**item**.big` rather than `.big`.

**The screen froze anyway**, in the same place:

    00:31:51  GET season/list                served
    00:31:51  GET /fut/items/xbl2/-1.json    x10, served
    00:31:52  GET trophies/xbl2/item.big     served
    00:31:52  GET season/user                served
              nothing further

So the asset chain is eliminated. What is wrong is in the record, which is what
the reduction ladder below is for. The console stayed healthy throughout --
XBDM answering, title running, only the frontend hung.

## The ladder, reachable by name

`FIFA14_SEASON_MODE` now takes five values rather than two:

    empty     no seasons at all -- the only answer known to break nothing
    minimal   one division, no `matches`, no `prizeSet`
    prizes    minimal plus `prizeSet`
    matches   minimal plus `matches`
    native    every division, both arrays -- the shape that freezes

One rung per relaunch, one entry into the mode each. `minimal` first: if that
opens, the record's frame is right and one of the two arrays is the fault; if
it freezes too, the fault is in the record's own members and neither array
matters yet.

## Résolu — 13 août 2026

Le mode s'ouvre, se joue, et se souvient. Trois défauts distincts, dans cet
ordre.

### 1. `divisionId` est un rang, pas un numéro

La bissection de l'échelle ci-dessus a nommé le membre en cinq relances :

    {}                                       ouvre, et tient
    {"seasonId":1}                           ouvre, et tient
    {"seasonId":1,"divisionId":10}           ouvre, puis gèle
    {"seasonId":1,"divisionId":10,"round":1} gèle
    {"seasonId":1,"divisionId":0}            ouvre, tient, propose de démarrer

Ce n'est pas « nommer une division que la liste ne contient pas » : le disque
servi à côté portait `divisionId` 10 lui-même. Ce que 10 est aussi, sur une
liste de dix, c'est un cran au-delà du dernier index. Le client lit ce membre
comme la **position** du disque dans la liste servie, comptée depuis zéro.

Le piège tient à ce que les deux suites vont en sens inverse : les ids de
disques montent de 1 à 10 pendant que les numéros de division descendent de 10
à 1. `divisionId: 0` et `division 10` désignent la même chose.

Avec ça, la liste complète — dix divisions, quatre matchs et quatre
récompenses chacune, 12 590 octets — s'ouvre sans broncher.

### 2. La route de sauvegarde est un cran plus profonde

La table de modèles d'URL porte `ut/%s/season/%%s/user` sous
`SEASONUSER_ALTER`, et lire ce `%%s` comme l'id de la saison est faux. La
chaîne de format qui sert à le construire est ailleurs, à côté du sérialiseur
de saison : `%d/division/%d`. Ce qui part sur le fil est donc

    PUT /ut/game/fifa14/season/1/division/10/user
    GET /ut/game/fifa14/season/user/history?type=offline

Les deux tombaient sur le 404 générique. Le numéro de division dans le chemin
est bien le **numéro**, pas la position : le client relit `divisionId` dans le
disque qu'il a choisi, ce qui fait que la position 0 devient `division/10`. Les
deux sens coexistent, chacun à sa place.

Le corps est celui des coupes avec un mot changé — `.rdata` épelle le blob
`data` pour les saisons contre `tournamentData` pour les coupes, avec la même
queue `progressData`. `SeasonProgress` est donc `TournamentProgress` clé par
une paire, et il hérite de la règle que les coupes ont payée : une saison
sauvegardée au round 1 avec un blob vide n'a pas de premier match à reprendre,
et se répond « pas de saison ».

### 3. `season/user` mentait sur l'avancement

Le client sauvegarde sa propre progression après chaque match — round 2 dès le
premier match abandonné. `season/user` répondait round 1 quoi qu'il arrive,
donc rentrer dans le mode proposait dix matchs sur dix quel qu'en soit le
nombre déjà joués. Il lit maintenant la saison sauvegardée.

### Ce que la console a fait, une fois les trois corrigés

    13:37:17  GET  season/list                    12 590 o, dix divisions
    13:37:18  GET  season/user                    {"seasonId":1,"divisionId":0,"round":1}
    13:37:48  PUT  season/1/division/10/user      round 1, la saison démarre
    13:39:19  POST match   {"squadId":4,"type":"OFFLINE","seasonId":1,"divisionId":10,…}
    13:48:12  PUT  match/end                      QUIT, 18 formes, 1 but, 1 passe
    13:48:12  PUT  season/1/division/10/user      round 2

L'écran de fin affiche « MATCHS RESTANTS 9 », « BILAN 0-0-1 », la forme, et la
barre montée/maintien. C'est le même appel de création de match que les
coupes, avec `seasonId`/`divisionId` à la place de `tournamentId`.

## Correction du 13 août, après-midi : `divisionId` est un index *client*

Ce qui est écrit plus haut — « `divisionId` est le rang du disque dans la
liste servie » — est faux, et l'écran l'a dit d'un coup : avec `divisionId: 0`
l'écusson de la Saison Actuelle affiche **DIV 1**, avec à côté « Matchs
restants : 10 » et « 12 PTS TITRE ». Aucune de ces valeurs n'est dans le
disque servi pour la division 10.

Donc `divisionId` indexe **la table de divisions du client**, qui va de la
Division 1 (index 0) à la Division 10 (index 9). Dix gelait parce que dix est
un cran au-delà de son dernier index — ça, c'était juste. Zéro tenait l'écran
parce que zéro est un index valide, pas parce que c'était le bon : il mettait
le club en Division 1.

`divisionId` vaut donc `division - 1`, et `SEASON_DIVISIONS` est réordonnée en
croissant pour que le même nombre soit juste des deux côtés. Vérifié sur la
console : le client sauvegarde maintenant dans
`PUT /ut/game/fifa14/season/10/division/10/user`, et le panneau de détails
affiche les valeurs de la division 10 — titre 12, montée 2, 400 / 1 500 / 300.

Le client compte **dix rencontres** par saison quoi qu'on serve : après un
match abandonné en division 10, où cette table n'en donnait que quatre,
l'écran de fin annonçait « MATCHS RESTANTS 9 ». Les dix divisions en ont dix.

## Ce qui reste : reprendre une saison entamée

Une saison au round 2 est proposée à nouveau au démarrage — « Voulez-vous
vraiment débuter cette Saison Joueur Solo ? » — et la colonne Score de la
liste des rencontres reste vide.

Ce n'est pas faute d'avoir l'information. Le serveur la tient et la sert :

    season/user  ->  {"seasonId":10,"divisionId":9,"round":2,
                      "seasonGamesWon":0,"seasonGamesDraw":0,"seasonGamesLost":1,
                      "seasonCoins":0}

Et le blob que le client s'était sauvegardé lui est rendu intact sur
`season/10/division/10/user`. Ce qu'on sait de plus, et qui délimite la
recherche :

* **le client ne redemande pas le blob.** En rouvrant le mode il ne lit que
  `season/list` puis `season/user`, puis ne parle plus au serveur du tout —
  la liste des rencontres est dessinée hors ligne. La seule fois où il a
  demandé `season/<id>/division/<div>/user`, le 13 août à 14:41:38, la route
  n'était pas encore écrite et il a pris un 404.
* **`round` n'est pas lu non plus**, ou pas là. Round 2 servi, et la première
  rencontre reste marquée « SUIV. ».
* **les quatre membres de bilan sont ignorés.** `seasonGamesWon` et
  `seasonCoins` sont partis avec les bonnes valeurs à 15:32:11 et l'en-tête
  affichait `BILAN 0-0-0` et `CRÉDITS 0`.

Ce qui reste debout, par élimination : l'état de reprise est dans les disques
de `matches`, que la colonne Score attend. `score` est dans la table de noms
du module. Ce qui manque pour le servir, c'est le score encaissé : le corps de
`match/end` porte les buts de chaque joueur — donc les miens — et rien sur
l'adversaire. Le servir demanderait d'inventer la moitié de chaque score, ce
que ce dépôt ne fait pas.

## Rectification : l'écusson ne suit pas `divisionId`

J'ai écrit plus haut que le badge **DIV 1** prouvait que `divisionId` indexe la
table du client. Il ne prouve rien : vérifié le 13 août à 16:27, avec
`divisionId: 9` servi et la liste réordonnée, **l'écusson affiche toujours
DIV 1**. Il l'affichait déjà avec `divisionId: 0`. Il ne suit donc pas ce
membre, et sa valeur est constante quoi qu'on envoie — vraisemblablement un
défaut du client, ou l'asset de trophée, que `trophyResourceId: -1` laisse
sans rien.

Ce qui reste vrai de ce raccourci :

* **dix gèle**, zéro et neuf tiennent. La borne est réelle.
* le panneau de détails affiche bien les seuils et récompenses de la division
  visée — mais il le faisait dans les deux configurations, puisque la liste a
  été réordonnée en même temps. Il ne discrimine pas non plus.

Autrement dit, entre `divisionId = 0` avec liste décroissante et
`divisionId = 9` avec liste croissante, **rien d'observé ne les sépare**. Le
second est gardé parce qu'il donne au membre le même sens des deux côtés, pas
parce que la console l'a confirmé.

Ce qui trancherait, en une relance : servir `divisionId: 5` et lire l'écusson.
`6` désignerait `divisionId + 1`, `5` désignerait `10 - divisionId`, et DIV 1
à nouveau dirait que le badge ne vient pas de nous du tout.
