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

## Reprendre une coupe *jouée* gèle aussi — 14 août 2026

`TournamentProgress.unplayed` traitait le gel comme un cas particulier : une
coupe ouverte puis quittée avant le premier match, rendue telle quelle, fige le
titre. Ce que ce garde-fou disait implicitement, c'est qu'un vrai parcours, lui,
se reprendrait.

Il ne se reprend pas. Le 14 août à 01:53:30, la coupe 3 était au **round 2** —
un match gagné, `progressData` de quarante octets, tout sauf vide. Le client a
demandé `GET /ut/game/fifa14/tournament/user/3`, le serveur a répondu les cinq
membres qu'il avait lui-même écrits, 1 121 octets, et le titre s'est figé sur
l'écran des compétitions, la tuile marquée « EN COURS ».

Rien d'autre n'a été demandé ensuite. XBDM répondait toujours et
`xbeinfo running` donnait encore `default.xex` : façade gelée, pas console
morte.

Ce que ça élimine :

- **la forme du document** — déjà éliminée deux fois (une réponse identique
  octet pour octet à ce que le client avait envoyé figeait déjà) ;
- **le fait que le parcours soit vide** — c'est justement ce que `unplayed`
  couvrait, et ce parcours-là ne l'était pas.

Ce qui reste : le client **ne sait pas reprendre une coupe depuis ce serveur**,
quel que soit son contenu. `unplayed` ne corrigeait pas le défaut, il cachait
le seul cas qu'on avait vu.

La remise en route a consisté à retirer le parcours de la sauvegarde
(`runtime/club-save.avant-gel-coupe3.json` le conserve) et à relancer. La
coupe est repartie du round 1 sans broncher : le client a réécrit un parcours
neuf et notre GET a répondu `{"tournamentId":3}`.

À décider : si aucune coupe ne se reprend, `response` peut ne plus jamais
rendre de parcours — le gel disparaît, le prix étant qu'une coupe quittée
recommence. C'est le comportement effectif aujourd'hui, mais par accident,
pas par choix.

### Ce que le revival PC fait, et que nous ne faisions pas

`KyroGeorge2/FIFA-14-Local-FUT` (build PC, serveur Python) répond à la même
route. Sa règle de « parcours reprenable » est la nôtre, aux quatre octets nuls
près :

```python
# server/beta_identity.py
def _tournament_progress_is_resumable(round_value, tournament_data, progress_data):
    if int(round_value) > 1:
        return True
    ...
    return bool(decoded and any(byte != 0 for byte in decoded))
```

La seule différence de fond est dans la réponse :

```python
def offline_tournament_user(self, tournament_id):
    ...
    return {
        "tournamentId": tournament_id, "round": int(row["round_value"]),
        ...
    }
```

Il envoie **`tournamentId`**. Nous l'avions retiré, en raisonnant que l'id est
déjà dans le chemin.

Ce raisonnement ne tenait pas debout, et l'expérience qui était censée le
valider non plus. Elle avait retiré `tournamentId` **et** un doublon
`progressdata` en même temps — or cette seconde orthographe est dans la table
des noms, donc c'est le même champ connu deux fois, décodé deux fois dans le
même emplacement, ce qui suffit à faire tomber le titre à soi seul. Retirer les
deux ensemble ne prouve rien sur l'un ou sur l'autre. Et l'essai portait sur un
parcours **non joué**, le cas qu'il faut refuser quelle que soit la forme.

`tournamentId` est donc revenu. Le README du projet PC demande au testeur de
rouvrir une coupe et de vérifier que le round 2 est actif, donc sur ce
build-là, la reprise marche avec l'id présent.

Frontend différent — PC contre Xbox 360 — donc c'est un indice, pas une preuve.
C'est en revanche la seule différence concrète entre une réponse qui reprend et
celle qui a gelé ce titre le 14 août à 01:53:30.
