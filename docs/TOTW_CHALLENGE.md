# L'Équipe de la semaine : l'écran ne nous demande rien

`JOUER → ÉQUIPE DE LA SEMAINE` répond

> Il n'y a aucune Équipe de la semaine disponible pour le moment. Veuillez
> réessayer plus tard.

Quatre formes de document ont été essayées contre la console le 12 août, chacune
avec un relancement complet et une conduite jusqu'à l'écran à la manette
virtuelle. Les quatre donnent exactement le même message.

## Ce qui est établi

**L'écran ne fait aucune requête quand on l'ouvre.**

C'est la mesure qui compte, et elle est simple : ouvrir la tuile, compter les
requêtes `clientdata/totw` dans le journal, fermer la boîte, rouvrir la tuile,
recompter. Le compteur ne bouge pas. Une seule récupération dans toute la
session, dans la salve de connexion.

Donc **aucun document servi à `clientdata/totw` ne peut faire fonctionner cet
écran à lui seul.** Il décide à partir d'un état qu'il détient déjà.

Le reste concorde :

* aucune route de défi n'est jamais apparue dans aucun journal — 136 sessions ;
* aucune trame Blaze sans réponse au moment où l'écran s'ouvre ;
* `/totw` est le seul fragment que CardsDLL porte pour ça, à 0x8902D7DC, au
  milieu de `/tutorialpopups`, `/userHubData` et `/pileSize` — donc la route est
  la bonne et il n'y en a pas d'autre ;
* `STATUS.md` décrit déjà une opération sans nom, `0xDF`, soumise par
  l'emplacement `+0x4C` de l'objet `0x8908CA10`, qui **revient sans que rien ne
  suive et sans qu'aucune route HTTP ni trame Blaze n'atteigne le serveur**.

C'est très probablement la même chose : la demande de données de défi TOTW se
termine côté natif et ne sort jamais de la console.

## Les quatre hypothèses éliminées

Écrites pour que personne n'y repasse une matinée.

1. **Une fenêtre de validité.** Une Équipe de la semaine est celle *de cette
   semaine*, et le document ne disait rien de quand elle tourne. Les six membres
   qu'une coupe porte — `starttime`, `endtime`, `timeUntilStart`,
   `timeUntilEnd`, `visStart`, `visEnd` — tous présents dans la table de noms.
   Aucun changement.

2. **La liste plutôt que l'équipe.** `totw_index_with_squad` avait été écrite
   pour ça et jamais branchée : la *liste* des Équipes de la semaine en plus des
   onze. « Aucune disponible » ressemble beaucoup à une liste vide. Aucun
   changement. **Gardée** : c'est un sur-ensemble de l'autre document.

3. **Une vraie note.** Chaque entrée annonçait `rating: 0`, et une équipe sans
   note n'est pas une équipe qu'un écran peut proposer. 80 et 81 maintenant,
   calculées sur les onze. Aucun changement. **Gardée** : 0 était faux.

4. **L'enveloppe d'une équipe qui s'affiche.** L'Équipe de la semaine est un
   *onze*, dessiné sur un terrain, et ce document servait ses vingt-trois cartes
   dans un tableau `itemData` plat — qui ne dit pas qui joue où. Le document
   d'équipe qui s'affiche vraiment sur cette console emballe chaque carte dans
   un emplacement : `players: [{index, kitNumber, itemData}]`, plus `rating`,
   `chemistry`, `starRating`, `manager`, `actives`. Aucun changement.
   **Annulée** : du volume non prouvé sur un document du chemin de connexion,
   qui est exactement la catégorie qui a déjà figé la connexion une fois
   (`docs/HOME_HEADER_BALANCE.md`).

## La suite

Ce n'est pas une cinquième forme de document. C'est l'opération `0xDF` :
retrouver ce que fait `RequestChallengeData` côté natif et pourquoi elle rend la
main sans rien demander. Les noms sont là — `GetGameHubTOTWData`,
`GetTOTWSquads`, `GetTotwKits`, `GetChallengeData`, `GetTotalChallenges`,
`SetSelectedChallengeInfo`, `EVENT_CARDS_REQUEST_TOTW_CHALLENGE_DATA_FAILURE` —
et la table d'opérations à `0x890A6980` ne couvre que les identifiants 0 à 81,
donc `0xDF` appartient à une autre énumération qui reste à nommer.

## Ce que la matinée a quand même donné

Le cycle « modifier, relancer, conduire jusqu'à l'écran, regarder » prend
maintenant environ six minutes et ne demande personne. C'est ce qui a permis
d'éliminer quatre hypothèses avant huit heures du matin ; chacune aurait coûté
un aller-retour avec un humain, et le résultat aurait été le même.


## Le désassemblage, 12 août après-midi

Pas trouvé. Mais le terrain est déblayé, et les négatifs sont exploitables.

`tools/ppc_disasm.py` et le scanner de références marchent : `FirstTimeInit`,
dont le nom est à `0x89017240`, est référencé **exactement une fois**, à
`0x891074BC`. C'est le constructeur qui enregistre les opérations du service
FUT, et on y lit le motif en clair :

```
0x891074CC  addi  r9, r9, 0x7240     ; le nom  FirstTimeInit   (0x89017240)
0x891074DC  stw   r9, 0x14(r31)
0x891074FC  addi  r9, r21, 0x5d18    ; l'entrée LoginToFUT     (0x89105D18)
```

Des noms en `0x89017xxx` appariés à des entrées en `0x89105xxx`. C'est la table
des opérations du service FUT — celle que `tools/fifa14_fut_api_trace.py`
connaît déjà.

**Les noms TOTW ne sont pas dedans.** Ils vivent ailleurs, en `0x89012xxx` :

```
0x89012148  GetGameHubTOTWData
0x890121E4  RequestChallengeData
```

Deux régions de noms distinctes, donc deux tables distinctes. Avec
`external/ion_fut/components/Tile/MetroPanel_TOTWChallenge.swf`, la lecture qui
tient est que l'écran TOTW appelle des **liaisons natives depuis le Flash, par
leur nom** — et non des opérations du service FUT. Ce qui explique pourquoi
aucune des opérations tracées ne se déclenche quand on ouvre la tuile.

Ce qui a été balayé sans rien trouver, en `lis`/`addi` **et** en table de
pointeurs, de `0x89020000` à `0x89190000` — soit à peu près tout CardsDLL :

* `0x89012148` `GetGameHubTOTWData`
* `0x890121E4` `RequestChallengeData`
* `0x8902D7DC` `/totw`, et aussi `/userHubData` et `/tutorialpopups`, qui sont
  pourtant des routes que le client demande à chaque connexion

Ce dernier point est le plus parlant : **même les fragments d'URL que le client
utilise réellement ne sont référencés nulle part par une adresse immédiate.**
Ils ne sont donc pas chargés comme des constantes. Ils sont soit comparés comme
chaînes à l'exécution, soit atteints par un décalage calculé dans une table
indexée par une énumération — et dans les deux cas un scanner de références ne
les verra jamais.

## La mesure qui vaut le prochain tour

Servir `{}` à `clientdata/totw` et rouvrir l'écran. Si le message ne change pas
d'un caractère, le document n'est pas consulté du tout, et toute recherche du
côté du JSON est à abandonner. C'est une relance et six minutes, et ça
réoriente tout le reste.
