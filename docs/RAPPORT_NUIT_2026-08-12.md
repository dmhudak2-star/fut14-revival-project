# Rapport de nuit — 12 août 2026

## À faire en premier au réveil

**Appuie sur le bouton d'alimentation de la console.** Elle est éteinte et je ne
peux pas la rallumer d'ici.

C'est de ma faute. À 01:47 j'ai envoyé `magicboot cold` pour la ramener au
dashboard. Les deux autres formes de `magicboot` reviennent ; celle-là l'a
sortie du réseau. Vingt minutes plus tard le port 730 refusait toujours et
l'entrée ARP était incomplète. Rien de cassé — il faut juste le bouton. La
commande est maintenant interdite dans le pilote, avec la raison écrite à côté.

**Et pendant que tu y es, une fois pour toutes** : Paramètres → Système →
Stockage → disque dur → définir par défaut. Voir « le mur » plus bas.

Treize commits poussés sur `fut-cups-club-creation`. 367 tests passent.

## Le plus important : le rapport de match tuait la connexion Blaze

Seize lignes `connection_error` dans les journaux disent toutes la même chose :

    Unsupported TDF type 7 for PRVT at offset 0x5

Toutes composant 28, commande 2. C'est **le rapport de match que la console
envoie quand un match se termine**. Notre décodeur TDF n'avait aucun cas pour le
type 7 : il levait une exception, l'exception emportait la connexion Blaze, et
le rapport partait à la poubelle. À chaque match.

Un TDF « variable », c'est un drapeau, puis — s'il est mis — l'identifiant 32
bits de la classe qui suit et les champs de cette classe. Je n'ai pas supposé
cette règle, je l'ai lue sur les deux trames capturées : c'est la seule sous
laquelle les 74 octets de l'une et les 175 de l'autre se décodent jusqu'au bout
sans rien qui dépasse, et se ré-encodent **octet pour octet**. Il n'y a pas de
jeu où une mauvaise règle pourrait se cacher.

Elles se décodent en ce qu'un match FIFA rapporte : `FNSH`, un `PRVT` vide, et
`RPRT` qui porte `GAME` — `CTRY`, `PLID`, `SCOR`, `SKLG` pour `gameType21`, et
pour `gameType85` un enregistrement `GAMR` avec le `CGRT` du club dedans.

## Et j'avais déjà tes vrais corps de fin de match

Je t'avais dit qu'il fallait que tu joues un match pour que je capture ce que le
client envoie. **C'était déjà dans les journaux**, depuis le 11 août 06:12. Je
ne les avais pas lus.

```json
{"endReason":"LOSS","myRating":10,"opponentRating":9,
 "myMatchStats":{"goals":1,"shotsOnTarget":2,"successfulTackles":33,
   "corners":2,"cleansheets":0,"passingPercentage":79,
   "possessionPercentage":55,"manOfTheMatch":1,"fouls":2,
   "yellowCards":0,"redCards":0,"offsides":1},
 "opponentMatchStats":{...},
 "items":[{"id":1800000019,"fitness":99},
          {"id":1800000011,"fitness":95,"assists":1},
          {"id":1800000018,"fitness":96,"goals":1}],
 "matchData":"532382ea…"}
```

Bonne nouvelle : les noms que le calcul des gains lisait — `passingPercentage`,
`possessionPercentage` — sont les bons. Les primes étaient justes.

Mauvaise nouvelle : **le tableau `items` était jeté**. Il porte la forme de
chaque joueur après le match, et les buts et passes de ceux qui en ont fait.
Personne dans ton club n'a donc jamais perdu un point de forme. C'est ça qui
laissait toute la pile de consommables sans rien à restaurer : tout le monde à
99 pour l'éternité, et une carte de forme qui ne sert à rien.

La forme est **écrite**, pas soustraite — le client envoie la valeur d'après le
match. Les buts et les passes s'additionnent, parce que chaque envoi ne porte
qu'un match. `lifetimeGoals` n'existe ni sur les cartes ni dans la table de
noms, donc je n'ai rien inventé pour lui, et `statsList`/`lifetimeStats` sont
des tableaux index/valeur dont je ne connais pas les index : je les laisse
tranquilles plutôt que d'écrire au hasard.

Les deux sont testés contre les corps capturés tels quels.

## Les doublons

Un pack marquait ses propres doublons avant que tu les acceptes, et ça s'arrêtait
là. Une fois la carte au club, plus rien ne le disait — et le club est justement
l'endroit où on va chercher ses doublons à vendre. Sur ta vraie sauvegarde :
**75 doublons parmi 174 joueurs, invisibles.**

La carte gardée comme originale est celle qui a le plus petit identifiant, la
possédée depuis le plus longtemps. Les marques sont réécrites et non ajoutées,
donc une carte dont le jumeau a été vendu cesse de prétendre répéter une carte
qui n'est plus là.

`_signature` — resourceId exactement, jamais assetId, parce que toutes les
versions d'un joueur partagent son asset — était privé au pack. C'est
`card_signature` au niveau module maintenant, une seule réponse pour les deux.

## Les transferts

Envoyer une carte à la liste des transferts la sort du club — il le faut, sinon
elle apparaît aux deux endroits. Et la liste ne renvoyait que les enchères. Une
carte envoyée là et pas encore mise à prix n'était donc **nulle part**. Retirer
une enchère avait le même effet.

Le test existant le disait sans le voir : il vérifiait que la pile était vide à
la ligne suivant celle qui vérifiait que la carte était bien dans la liste. Le
bug écrit noir sur blanc.

C'est aussi le symptôme que Kyro a corrigé de son côté (« unlisted pile-5 cards
remain visible in Transfer List »).

## La liste de surveillance ne répondait pas

Le client demande `/ut/game/fifa14/watchList`, avec un L majuscule. Ce serveur
avait enregistré `watchlist`. **404 à chaque ouverture de la liste**, et rien ne
le signalait — un 404 sur une route FUT laisse juste l'écran vide, et une liste
de surveillance vide ressemble à une liste de surveillance vide.

Ils étaient d'accord sur `tradePile`, `clubUser` et `userHubData` par chance.
Tout le namespace se compare maintenant sans tenir compte de la casse, et un
test relit le source du module pour vérifier qu'aucune route n'y échappe — il en
a trouvé huit au premier passage.

C'est le journal des routes non traitées qui a sorti ça. **Il vaut la peine
d'être lu après chaque session** : c'est le seul endroit où le client dit, dans
ses mots, qu'il a demandé quelque chose qu'il n'a pas eu.

## Les styles de chimie s'appliquent enfin

Les sous-types 91-136 étaient refusés depuis des semaines, faute de savoir s'ils
étaient des styles ou des changements de poste. Ton journal garde une trace :
une carte 106 que tu as essayé d'appliquer le 12 août à 00:34, refusée avec
un 400.

Ce qui tranche, c'est le membre sous lequel CardsDLL les compte — dans la table
de noms du binaire, pas une étiquette choisie ici :

    91-110    consumablesTrainingPlayerPlayStyle
    121-136   consumablesTrainingGkPlayStyle

Deux plages, joueurs de champ et gardiens. C'est exactement la façon dont les
styles de chimie sont séparés dans FUT, et ce n'est pas la façon dont un
modificateur de poste le serait. `playStyle` est un membre que toutes les cartes
portent déjà, à 0 depuis toujours.

Ce qui n'est **pas** établi, c'est la numérotation : j'écris le sous-type de la
carte. Si l'énumération est décalée, la conséquence est une carte qui affiche le
mauvais nom de style, et une autre carte de style corrige — ce qui ne serait pas
vrai d'un poste écrit de travers. **À vérifier à l'œil** : applique un style et
regarde si le nom affiché correspond à la carte.

Le split gardien est appliqué : un style de gardien sur un joueur de champ est
refusé sans dépenser la carte.

232 reste refusé, et la raison est écrite : la carte que la console a réellement
affichée pour lui dit « DÉBLOQUER / Capacité +8 moral ». Les deux catalogues se
trompent dessus.

## L'Équipe de la semaine

Seuls 18 des 23 vrais joueurs en forme se retrouvent dans le catalogue, et le
reste était complété par les meilleures cartes rares trouvées. Ça mettait un 98,
un 98, un 98, un 97 et un 97 sur le banc d'une équipe dont les vrais membres
plafonnent à 85.

Ce n'est pas cosmétique : le défi calcule `opponentRating` sur les onze
premiers, donc le remplissage décidait de la force de l'équipe que tu affrontes.
Rempli depuis la bande de notes de la vraie équipe, au plus proche de sa
moyenne : 85 à 64, note d'adversaire 80. Une Équipe de la semaine, et un match.

## Trois autres défauts trouvés en relisant ce que le client demande

### L'archive BIG vide se déclarait longue de 268 Mo

Une vraie BIGF de ce jeu — lue dans le paquet helperFunctions du Title Update —
porte sa taille totale en **petit-boutien** et son nombre d'entrées et sa taille
d'en-tête en **grand-boutien** :

    BIGF   54032 (petit-boutien)   3 entrées (grand)   en-tête 56 (grand)

Les quatre champs partaient en grand-boutien ici. L'archive de seize octets que
ce serveur rend pour chaque `/fut/items/images/*.big` annonçait donc
`0x10000000` — 268 mégaoctets.

Les deux écrans qui gèlent après avoir tout reçu demandent cette archive en
entrant : la reprise d'une coupe, et les saisons. C'est une corrélation, pas une
démonstration, et je ne la présente pas comme le correctif. Le champ était faux
de toute façon, et il l'était sur une requête que fait tout écran de coupe.

### Les identifiants de trophée négatifs n'étaient pas servis

L'écran des saisons demande `/fut/items/xbl2/-1.json`, une fois par division. La
route ne reconnaissait que des chiffres, donc les dix tombaient dans le
`{"itemData":[]}` générique que ce gestionnaire existe précisément pour
remplacer — et une définition vide est exactement ce qui fait construire à la
console

    /fut/items/images/trophies/xbl2/.big

sans rien entre le préfixe et l'extension. Il y en a dix-huit dans les journaux.

**Les saisons valent la peine d'être réessayées** avec `FIFA14_SEASON_MODE=native`
maintenant que ces deux-là sont corrigés : la troisième tentative gelait
précisément après que ces deux documents aient été servis.

### Un consommable appliqué par son identifiant de carte ne faisait rien

Le client adresse un consommable de deux façons. `item/resource/<id>` nomme la
définition, `item/<id>` nomme une carte précise du club. Seule la première était
traitée. Donc ceci, le 11 août à 03:00 :

    POST /ut/game/fifa14/item/1950000106
    {"apply":[{"id":1700000004}]}

a reçu un 404 et est parti dans le journal des routes non traitées, que
personne ne lisait. De ton côté, la carte n'a simplement rien fait.

Exercé de bout en bout contre un serveur qui tourne : l'identifiant de carte se
résout en sa définition, la pile applique, et un style de gardien sur un joueur
de champ revient en 400 « a goalkeeper style needs a goalkeeper » au lieu d'un
silence.

## Le mur : la boîte de dialogue système

À chaque démarrage à froid, FIFA se met derrière le sélecteur de périphérique de
stockage du 360 — « Choisir périph. », disque dur ou clé USB. Tant que personne
ne répond, **rien n'avance**.

La manette virtuelle ne peut pas la refermer. J'ai vérifié plutôt que supposé :
le compteur d'images descend de 60 par seconde, donc le point d'accroche est
bien appelé — mais deux DOWN ne déplacent pas la sélection et A ne valide pas.
XAM dessine cette boîte et lit la manette par un autre chemin.

C'est le vrai obstacle à « ça doit marcher tout seul », et ce n'est pas un menu
du jeu. **La solution est côté console et se fait une seule fois** : Paramètres
→ Système → Stockage, disque dur, définir par défaut. Le sélecteur n'apparaît
que parce qu'il y a deux périphériques et aucun par défaut.

## Le pilote, et ce qu'il a fallu trouver

`tools/fut_drive.py` — trois sens, dans cet ordre :

1. **Le journal est un oracle d'écran.** Chaque écran a une signature de
   requêtes qui n'appartient qu'à lui.
2. **La capture d'écran marche.** `docs/AUTOMATIC_PATCH.md` la donne pour « pas
   fiable sur cette console ». Elle a répondu à chaque appel cette nuit, une
   trentaine de fois. C'est ce qui a expliqué en une image pourquoi rien ne
   répondait.
3. **La manette doit rester branchée.** L'action `press` arme la boîte aux
   lettres quelques images puis la remet à zéro — et une boîte à zéro repasse la
   main à la vraie manette, dont aucune n'est allumée à quatre heures du matin.
   Le titre voyait une manette apparaître un tiers de seconde et disparaître.
   Tenir zéro bouton entre les impulsions est ce qui a fait passer la première
   pression.

## Le TU3 statique : la piste est morte pour l'instant, et j'ai désamorcé un piège

Le paquet `tu00000003_00000000.codex-patched` qui dort sur ta console à côté de
l'original **ne doit pas être installé**. Son index dit bien que
l'enregistrement 2218 a été réécrit — 17 728 octets au lieu de 17 942 — mais son
contenu ne se décode pas : `Unsupported LZX block type 0`.

La faute est dans notre encodeur. Une trame LZX transporte au plus 32 Kio ;
`encode_container` ne contrôlait que le champ de longueur sur 16 bits et a écrit
les 54 048 octets de la ressource **en une seule trame en déclarant 54 048**. Le
décodeur du jeu ferait exactement ce que le nôtre fait. L'encodeur refuse
maintenant, avec un test.

Régénérer proprement bute ailleurs : EA fait tenir ces 54 048 octets dans
17 942, le nôtre en produit 18 843, l'emplacement en fait 17 984. Il manque
l'écriture multi-trames **et** environ 5 % de taux. Détail dans
`docs/TU3_STATIC_PATCH.md`.

Au passage, l'original retail est vérifié intégralement contre les trois
empreintes attendues, et **sauvegardé sur le Mac pour la première fois**
(`work/tu3/`).

## La suite de tests écrasait ta sauvegarde

Trouvé en cherchant pourquoi ta coupe en cours avait disparu. Importer le module
serveur construit un club vivant depuis `runtime/club-save.json`, et toute route
testée qui sauvegarde écrivait **ce fichier**. Ta coupe est entrée à 00:33 et
avait disparu à 00:38:27, réécrite avec une table de tournois vide par le
`pytest` que j'avais lancé entre les deux.

`SAVE_FILE` lit `FIFA14_CLUB_SAVE` et `tests/conftest.py` le détourne avant tout
import. Vérifié : le vrai fichier ne bouge plus d'un octet.

## Ce qui reste ouvert

- **Le gel en reprenant une coupe.** Mitigé : une coupe entrée mais jamais jouée
  n'est plus proposée en reprise. La cause reste inconnue — le second gel s'est
  produit sur une réponse identique octet pour octet à ce que le client avait
  lui-même envoyé, donc le document n'est pas en cause. Le rapport de match qui
  tuait la connexion Blaze est un suspect sérieux que je n'ai pas pu tester.
- **Les défis TOTW.** La structure existe (`squadChallenge`) et l'équipe est
  correcte maintenant, mais aucune route de défi n'est jamais apparue dans les
  journaux. Il faut ouvrir l'écran une fois pour que le client dise ce qu'il
  veut.
- **Les saisons.** Servies vides. Trois formes essayées, trois échecs, dont un
  gel — voir `docs/SEASONS.md`. Je n'y ai pas retouché cette nuit.
- **La réponse de `/match/end`.** Toujours les trois membres d'origine. Le
  règlement est côté serveur ; les coins tombent dans le solde. Les scalaires de
  l'écran de récompense attendent de voir un match aller au bout avec le décodeur
  Blaze corrigé.
- **La numérotation des styles de chimie**, à vérifier à l'œil.

## Ce qu'il me faut de toi

1. Rallumer la console.
2. Le périphérique de stockage par défaut, une fois.
3. Jouer **un match de coupe jusqu'au bout**. C'est ce qui exerce d'un coup le
   décodeur Blaze corrigé, l'écriture de la forme, la progression de coupe et le
   crédit des coins.
4. Appliquer **un style de chimie** et me dire si le nom affiché correspond.
