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

### Testé, et non : `tournamentId` n'était pas la cause

Mesuré le 14 août à 02:56 sur la console, avec un parcours neuf.

| heure | ce qui s'est passé |
|---|---|
| 02:55:27 | match de coupe gagné, coupe 3 passée au round 2, 1 244 crédits |
| 02:55:40 | le client **PUT** son blob de round 2 ; notre écho lui rend le document complet — **rien ne gèle** |
| 02:56:00 | écran des compétitions redessiné, tuile « EN COURS » — rien ne gèle |
| 02:56:10 | accueil FUT, fil d'actualité — rien ne gèle |
| 02:56:21 | **GET** `tournament/user/3` → les six membres, `tournamentId` compris → **gel** |

C'est donc le GET, et lui seul. Le PUT porte le même document dans l'autre
sens et passe très bien.

Ce que ça élimine en plus :

- **la forme** — la réponse était exactement celle du revival PC,
  `tournamentId` inclus ;
- **la corruption** — le blob servi est identique octet pour octet à celui
  reçu (968 caractères base64), le gzip se décompresse aux 2 798 octets qu'il
  annonce, et l'entête de longueur de `progressData` correspond à sa charge ;
- **les en-têtes HTTP** — identiques à ceux d'une route du même écran qui
  marche, à `Content-Length` près.

Le document est fidèle et la forme est celle d'un serveur où ça marche. Ce
build ne reprend pas, l'autre si.

`cup_resume_mode()` en tire la conséquence : par défaut (`off`) un parcours
sauvegardé n'est **jamais** rendu, donc une coupe abandonnée recommence. C'est
une vraie perte de fonction, et c'est le seul réglage qui ne coûte pas une
console gelée.

`FIFA14_CUP_RESUME` garde les autres lectures à portée d'un relancement plutôt
que d'une modification, parce que chaque essai coûte un gel et une
récupération :

| valeur | ce qui sort | état |
|---|---|---|
| `off` | `{"tournamentId": id}` | défaut, ne gèle pas |
| `round` | l'id et le round, aucun blob | à essayer |
| `noblob` | tous les membres, blobs vides | à essayer |
| `full` | tout | **gèle**, mesuré deux fois |

Le prochain essai le plus informatif est `round` : si le client accepte un
round sans blob, il reconstruit son tableau lui-même et la reprise est acquise
sans jamais lui rendre ses octets.

### La cause : le client ne relit pas ce qu'il écrit

Le blob était fidèle, la forme était celle d'un serveur où ça marche, les
en-têtes étaient bons. La cause n'était pas sur le fil, elle est dans le
binaire.

Son sérialiseur, `.rdata` 0xa1c4, écrit **`"progressData"`**, D majuscule :

```
0x0a1c4  ","progressDataVersion":%d,"progressData":"
```

Sa table de noms JSON — anti-alphabétique, et complète — contient
`progressDataVersion` à 0x103cc, puis **`progressdata`**, tout en minuscules, à
0x103e0 :

```
0x103cc progressDataVersion
0x103e0 progressdata
0x103f0 productId
```

Il n'y a **aucune** entrée `progressData` avec une majuscule. Tous les autres
membres qu'on envoyait sont bien dans la table : `round` 0xb5c0, `dataVersion`
0x10f04, `tournamentData` 0xfd58, `tournamentId` 0xfd48.

Autrement dit, le seul membre dont la reconstruction du tableau a besoin était
le seul nom que le parseur ne savait pas résoudre. Il l'ignore, garde ce que
l'emplacement de progression contenait déjà, et marche dedans.

C'est aussi pour ça que le PUT n'a jamais rien gelé : à la montée, c'est ce
serveur qui parse, et lui n'est pas regardant.

La réponse épelle donc `progressdata`. `FIFA14_CUP_RESUME=off` reste la sortie
de secours.

### Les saisons ont exactement la même faute

Le sérialiseur de saison, 0xa35a, écrit `"data"` :

```
0x0a35a  {"round":%d,"dataVersion":%d,"data":"
```

La table ne contient pas `data` du tout — entre `dataVersion` (0x10f04) et
`customData1`, il n'y a rien. Ce qu'elle contient, c'est **`seasonData`** à
0x101d0, au milieu des autres membres de saison :

```
0x101a4 seasonGamesDraw
0x101b4 seasonId
0x101c0 seasonEndResult
0x101d0 seasonData
0x101dc seasonCompleted
0x101ec seasonCoins
```

`SeasonProgress.response` épelle donc `seasonData` et `progressdata`. Les deux
dossiers ouverts — reprendre une coupe, reprendre une saison — avaient la même
cause.

### `progressdata` ne suffit pas non plus — et ce qu'il fallait lire

Servi le 14 août à 16:22:37, sur le parcours au round 2 : gel, exactement comme
sur les deux orthographes précédentes. Dernière trace de la console à cette
seconde-là, rien après.

Ce qui manquait, c'est que **la table est groupée**, et le groupe est
l'information. `progressdata` (0x103e0) est au milieu de membres génériques —
`productId`, `prizeSet`, `progressDataVersion`. Les membres de la coupe sont
ensemble, ailleurs :

```
0x0fd34 tournamentProgress
0x0fd48 tournamentId
0x0fd58 tournamentData
```

La paire depuis laquelle une coupe se reconstruit est donc probablement
`tournamentData` + `tournamentProgress`, et `progressdata` appartient à ce qui
écrit `progressDataVersion` à côté de lui.

`cup_progress_members()` en fait un réglage plutôt qu'une modification, parce
que chaque essai coûte un gel et une relance :

| `FIFA14_CUP_PROGRESS` | membre servi | état |
|---|---|---|
| *(défaut)* | `tournamentProgress` | à tester |
| `progressdata` | `progressdata` | **gèle**, mesuré |
| `progressData` | `progressData` | **gèle**, mesuré (ce que le client écrit) |
| `both` | les deux noms connus | à éviter sauf besoin : deux noms connus dans un seul emplacement est la forme accusée d'un gel antérieur |

### Quatre documents, quatre gels : la piste des noms est épuisée

| membre portant le blob de progression | mesuré |
|---|---|
| `progressData` — ce que le client écrit | gel |
| *(cinq membres, sans `tournamentId`)* | gel |
| `progressdata` — table, voisinage générique | gel, 16:22:37 |
| `tournamentProgress` — table, voisinage coupe | gel, 16:30:20 |

À chaque fois : dernière requête de la console sur ce GET, **zéro** après.

Deviner le nom depuis la table ne marche pas, et chaque essai coûte un gel et
une relance. `cup_resume_mode()` revient donc à `off` par défaut : une coupe
abandonnée recommence, et la console ne gèle plus.

Ce qui reste à essayer, par ordre d'intérêt :

1. **`FIFA14_CUP_RESUME=round`** — l'id et le round, aucun blob. C'est la seule
   forme qualitativement différente : si le client accepte un round seul, il
   reconstruit son tableau lui-même et la reprise est acquise sans jamais lui
   rendre ses octets. Non testé.
2. **Tracer le parseur** plutôt que le deviner : poser un point d'arrêt sur la
   fonction qui consomme cette réponse et lire l'adresse du plantage, au lieu
   de proposer des noms un par un. C'est ce qu'il fallait faire après le
   deuxième gel.

## Ce n'est pas un parseur qui plante, c'est un tampon de 3 Go

Tracé le 14 août sur la console gelée, avec `tools/fifa14_where_is_it_stuck.py`.

Le gel est un **blocage**, pas un plantage : XBDM répond, `xbeinfo running`
nomme toujours FIFA. Donc un thread est coincé, et stock XBDM sait dire où.

### La carte des modules, qu'on devinait faux

`modules` la donne, et elle corrige deux erreurs de nos notes :

```
default.xex               0x82000000 +0x01f20000   (et non 0x023EC400, qui est l'osize)
FootballCompEngzf.xex.dll 0x88000000 +0x00120000   ← jamais mentionné jusqu'ici
CardsDLLzf.xex.dll        0x89000000 +0x002b0000
powdllzf.xex.dll          0x89700000 +0x00150000
JRPC2.xex                 0x91900000 +0x0002c000   ← chargé, contrairement à AUTOMATIC_PATCH.md
```

### Le thread coincé

Sur 56 threads, un seul exécute du code du titre :

```
thread 4177526852   iar=0x8912d9a0  CardsDLLzf+0x12d9a0
                    gpr30 = 0xbd2e2eb4 = 3 173 920 436
```

`0x8912d9a0` est `stbu r10, 1(r11)` suivi de `bdnz` — une boucle de remplissage
octet par octet, de longueur `r30`.

Deux pièges à connaître pour refaire la mesure :

- XBDM imprime les identifiants de thread **signés** et ne les accepte
  qu'**non signés** : `threads` répond `-83886068`, et `getcontext` sur cette
  valeur dit `400- missing thread` là où `4211081228` marche.
- `getcontext` sur un thread **qui tourne** renvoie `0xffffffff` pour la
  plupart des registres. Il faut `suspend thread=N` d'abord. `setcontext`, lui,
  exige un arrêt de débogueur complet (`408- not stopped`) — une suspension ne
  suffit pas.

### La chaîne d'appels, remontée hors console

Le déroulement de pile ne passe pas : les trames intermédiaires ne sauvegardent
pas leur LR, et `lr` pointait dans la fonction elle-même — signature du
prologue `mflr r12` / `bl __savegprlr`. La vraie entrée était donc 0x8912d898,
pas 0x8912d8a0.

Le reste s'obtient sans console, en balayant `work/cardsdll-text.bin` (2,8 Mo,
tirés par XBDM) à la recherche des `bl` qui visent chaque cible :

```
0x8912d898   append(n, caractère)      1 appelant
0x8912da50   resize(n)                 1 appelant   ← si n > taille, ajoute n-taille zéros
0x8912dac8   « n octets remis à zéro »  9 appelants  ← resize(n) puis memset(begin, 0, n)
```

Autrement dit : le client demande un tampon de **3,17 milliards d'octets** et
le remplit de zéros, sur une console qui a 512 Mo. Le titre ne plante pas, il
part pour un temps infini.

`0xbd2e2eb4` a la forme d'un **pointeur de tas**, pas d'une longueur. Quelque
chose passe un pointeur là où un compte est attendu.

### Ce qui reste, et qui ne coûte plus de gel

Les neuf appelants de 0x8912dac8 sont dans le dump :

```
0x8912dc78  0x8912e0a4  0x8912e224  0x89143888  0x8914972c
0x8914988c  0x89149d4c  0x891b3e70  0x891c43f0
```

Il faut trouver lequel est sur le chemin de la reprise de coupe, et d'où sort
son `n`. Ça se fait entièrement hors console maintenant que
`work/cardsdll-text.bin` existe — c'est ce qu'il fallait faire au lieu de
proposer un cinquième nom de membre.

## La cause, enfin : le jeu ne sait pas relire ce qu'il écrit

Trouvé hors console, en désassemblant `work/cardsdll-text.bin` avec
`tools/ppc_xref.py`.

### La chaîne, de la boucle jusqu'au dispatcher

```
0x8912d898  append(n, caractère)        1 appelant
0x8912da50  resize(n)                   1 appelant
0x8912dac8  « n octets remis à zéro »   9 appelants
0x891b3dd0  décodeur de blob            4 appelants
0x891be840  lecteur de coupe
```

Le décodeur lit les **quatre premiers octets** du blob comme longueur, alloue
autant, puis décompresse dedans :

```
0x891b3e64  bl 0x89063fe8     ; memcpy(&local, source, 4)
0x891b3e6c  lwz r4, 0x50(r1)  ; ces quatre octets = le compte
0x891b3e70  bl 0x8912dac8     ; alloue et met à zéro
0x891b3e90  bl 0x891afdc0     ; décompresse
```

### Les membres que le lecteur connaît

Le lecteur compare des **identifiants numériques**, pas des noms. La table
d'identifiants est à `0x8921E498`, 613 entrées, indexée directement
(`0x891d9c78` : `lwzx r3, id*4, 0x8921E498`). Trois identifiants seulement sont
comparés :

| id | nom |
|---|---|
| 134 | `dataVersion` |
| 429 | `round` |
| 535 | `tournamentData` |

**Aucun membre de progression.** `progressdata` (395) et `progressDataVersion`
(396) ne sont jamais comparés. Une coupe se reprend depuis son tableau et son
round, c'est tout. Toute la recherche sur l'orthographe du blob de progression
portait sur un membre que ce lecteur ne lit pas.

### Le défaut

- la branche `tournamentData` remplit deux registres : un tampon et sa longueur
- la branche `dataVersion` parse le nombre et, s'il vaut 1, **décode en
  utilisant ces deux registres**

Or le sérialiseur du client (`.rdata` 0xb9ec) écrit :

```
{"round":%d,"dataVersion":%d,"tournamentData":"
```

`dataVersion` **avant** `tournamentData`. Au moment du décodage, les registres
n'ont jamais été écrits. Le 14 août ils contenaient 0xbd2e2eb4 — un pointeur de
tas — pris pour une longueur : CardsDLL a demandé 3,17 Go remis à zéro, octet
par octet, sur une console qui a 512 Mo.

Ce n'est pas un échec de parsing et aucun nom de membre ne pouvait le corriger.
**Le jeu ne sait pas relire sa propre sauvegarde.** Nos quatre tentatives
reproduisaient toutes son ordre.

### Le correctif

L'ordre des membres nous appartient. La réponse envoie `tournamentData`
**avant** `dataVersion`, et rien d'autre que ce que le lecteur consomme :

```json
{"tournamentId":3,"round":2,"tournamentData":"AAAK7h+LCAAA…","dataVersion":1}
```

La saison suit la même règle, par prudence : son sérialiseur (0xa35c) a le même
ordre, et son lecteur n'a pas encore été lu.
