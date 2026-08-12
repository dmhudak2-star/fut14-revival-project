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
