# Distribuer le revival

Aujourd'hui il faut un Mac allumé sur le même LAN, avec XBDM, pour jouer. Une
release doit supprimer cette machine du chemin de l'utilisateur. Ce document dit
comment, et dans quel ordre.

Le Mac fait **deux** choses sans rapport l'une avec l'autre, et elles se
suppriment séparément :

1. il **écrit des octets en mémoire** dans la console, par XBDM ;
2. il **répond aux requêtes** du jeu, en Blaze et en HTTP.

## Ce qui n'est pas un problème

Les cartes et leurs images ne viennent pas d'ici. Le *native FUT-resource
redirect* les fait lire sur le disque de la console, dans les fichiers du jeu de
l'utilisateur. Le serveur n'envoie que du JSON : `fifa14_cards.json`,
`fifa14_consumables.json`, `icebreakerpacklist.json`, 4 Mo au total. Aucun
asset EA ne transite, aucun n'a besoin d'être distribué.

## 1. Les patches — un plugin Dashlaunch

XBDM est doublement disqualifiant : il exige un PC allumé, et la plupart des RGH
ne l'activent pas.

La forme standard sur 360 est un **plugin Dashlaunch** : un `.xex` résident,
déclaré par une ligne de `launch.ini`, qui accroche le lancement de titre,
reconnaît FIFA 14 et écrit lui-même ce que le Mac écrit aujourd'hui.
L'utilisateur copie un fichier et ajoute une ligne. Il n'installe pas XBDM et
n'allume aucun ordinateur.

### Ce que le plugin doit reproduire

Rien à redécouvrir : les adresses et les octets sont déjà dans le dépôt. Le
plugin est une transcription en C, pas une recherche.

**Identification du titre** — `default.xex` timestamp `0x534C8977`, base
`0x82000000`, taille d'image `0x023EC400` (`README.md`, « Supported build »).
Toutes les adresses ci-dessous valent pour ce build et pour lui seul ; le
plugin doit refuser de patcher tout autre.

**Étage 1, au chargement du titre, avant que le code du jeu tourne**
— source `tools/fifa14_early_local_server.py`, qui s'accroche aujourd'hui à la
notification `modload` de XBDM. Un plugin prend le même instant en accrochant le
chargeur de titre.

| quoi | adresse | rôle |
| --- | --- | --- |
| ticket Xbox | `0x82F3ED00`, cave `0x83C8DB80` | substitue un jeton local au ticket nul |
| config auth2 | `0x82F401B8`, cave `0x83C8DA00` | idem côté configuration |
| hook connect | `CONNECT_CALLSITE` (`fifa14_connect_bypass`) | redirige les seuls ports Blaze |
| profil redirector | `PROFILE_POINTER` (`fifa14_redirector_profile_patch`) | `standardInsecure_v3` ou TLS retail |
| XNet nosecure | `XNET_BYPASS_BRANCH`, `NOSECURE_MODE_BRANCH` | sockets locales |

Les hôtes EA d'origine sont **conservés** : le résolveur du titre suit son chemin
normal, c'est le connect qui est détourné. Ne pas « corriger » ça.

**Étage 2, une fois `powdllzf` mappé** — source
`tools/fifa14_easfc_endpoint_patch.py`. Deux chaînes à réécrire dans le module
chargé à `0x89700000` :

```
0x897061B0   content.lt.easfc.ea.com:8080    le catalogue
0x89706250   pal.gt.easfc.ea.com:8094        la session
```

Le patcheur actuel *sonde* jusqu'à ce que le module soit mappé. Un plugin
accroche son chargement — et corrige au passage le défaut visible dans le
journal du 14 août, où l'écriture à `0x89706250` a trouvé
`b'http://\x00artAssets/matchd'` et n'a rien écrit : le module n'était pas dans
l'état attendu au moment du sondage.

**Étage 3, avant l'entrée dans Ultimate Team** — le patch `helperFunctions`
(trois branches de continuation TU3), source
`tools/fifa14_tu3_helperfunctions_runtime_patch.py`.

C'est là que le plugin fait **mieux** que nous, pas seulement pareil.
`docs/AUTOMATIC_PATCH.md` explique qu'on ne peut pas patcher une fois : le titre
recharge l'APT, et un patch vérifié au lancement se relit `original` une minute
plus tard. D'où un surveillant qui sonde le tas toutes les cinq secondes. Un
plugin résident accroche le chargeur de ressource : il patche à chaque
chargement, sans sonde, sans course, sans balayage de tas — et le balayage
complet est justement ce qui a gelé cette console une fois
(`docs/AUTOMATIC_PATCH.md`, « What actually made it unsafe »).

### La configuration est sortie du code — 14 août 2026

L'adresse du serveur était un argument (`--local-ip`) que `tools/fut.sh`
calculait et compilait dans la mémoire du titre au lancement. Elle est
maintenant dans `fifa14revival.ini`, lu par `tools/revival_config.py` :

```ini
[server]
host = auto            ; ou 192.168.1.40, ou revival.example.net
core_port = 10041      ; Blaze
identity_port = 18080  ; HTTP

[console]
address = 192.168.1.25
title = Hdd:\Games\FIFA 14
```

C'est **ce fichier-là** que le plugin lira sur le disque de la console. Le
format est donc réglé ici, où il est exercé à chaque lancement, plutôt
qu'inventé plus tard sur le papier. La section `[console]` ne sert qu'au
lanceur de développement : un plugin qui tourne sur la console sait déjà sur
laquelle il est.

Trois choses à savoir :

- **Aucun fichier n'est nécessaire.** Chaque clé retombe sur ce que le script
  codait en dur. Ajouter la configuration n'est pas un jour de bascule.
- **L'environnement gagne toujours** : `MAC=... XBOX=... tools/fut.sh` est ce
  que dit chaque note de ce dépôt, et ça continue de marcher.
- **`host = auto`** résout l'adresse LAN de la machine à chaque lancement. Ce
  n'est pas du confort : le DHCP a déjà déplacé cette adresse une fois, et une
  valeur périmée est silencieuse — le titre démarre normalement puis échoue au
  premier connect Blaze, sans rien dans le journal, puisque rien n'a atteint le
  serveur. La détection est passée de zsh à Python parce qu'un serveur
  auto-hébergé sous Linux a besoin de la même réponse, et parce qu'interroger
  la table de routage bat une liste de noms d'interfaces : ici l'adresse est
  sur en1, pas sur en0.

Le port HTTP était par ailleurs codé en dur à un endroit — l'URL `futBoot.xml`
armée par le patch de lancement. Il vient de la configuration maintenant.

### Pourquoi pas le patch statique

`docs/TU3_STATIC_PATCH.md` documente la voie « fichiers pré-patchés » et son
blocage : notre encodeur LZX sort 18 843 octets là où l'emplacement de
l'enregistrement 2218 en fait 17 984 — 5 % de trop. C'est réel, mais c'est un
problème de build **une seule fois**, pas par utilisateur.

Le vrai argument contre n'est pas là. Le patch statique oblige à distribuer un
`default.xex` et un TU3 patchés, donc du code EA — ce que `NOTICE.md` interdit
explicitement. Le plugin ne distribue que du code à nous. C'est la raison de
l'ordre, et elle ne dépend pas de l'encodeur.

## 2. Le serveur — multi-locataire d'abord

Le serveur ne peut pas descendre sur la console : 180 000 lignes de Python. Il
tourne quelque part. Deux formes, à livrer ensemble :

- **hébergé publiquement**, adresse par défaut dans le `.ini` — la seule qui
  tienne la promesse « sans PC » ;
- **auto-hébergé**, en changeant une ligne du `.ini`, pour le LAN et le hors
  ligne.

### Le préalable : un club par persona

Le serveur est **mono-locataire**. Un `runtime/club-save.json`, un
`runtime/local-account.json`, un club. Deux consoles sur le même serveur
jouent le même club et s'écrasent l'une l'autre.

La clé de routage existe déjà et n'a pas à être inventée — mais ce n'est pas
celle qu'on croit. L'en-tête nucleus est le candidat évident et c'est le
mauvais : sur une session complète jusqu'à Saison Joueur Solo, il apparaît sur
**une** requête sur quarante-neuf. `X-UT-SID` apparaît sur quarante-six.

```
X-UT-SID: LOCAL-XBOX360-FIFA14-SID
Host: 192.168.1.40:18080
User-Agent: ProtoHttp 1.3/DS 13.3.0.5.0 (Xbox360)
```

C'est l'identifiant de session FUT, et il était **une constante servie à tout
le monde** : chaque requête après l'authentification était donc anonyme. Le
frapper par persona à `/ut/auth` transforme l'écho du client lui-même en clé de
routage, sans table de sessions à tenir.

Dérivé plutôt que stocké, délibérément : `tools/fut.sh` redémarre ce serveur à
chaque lancement, et une table en mémoire laisserait en rade un client qui
tient encore l'identifiant d'il y a une minute.

Trois sources, de la plus précise à la moins : `X-UT-SID`, puis l'en-tête
`Easw-Session-Data-Nucleus-Id` (que porte `accountinfo`), puis le `nuc` du
corps de `/ut/auth` — la seule requête qui ne peut porter ni l'un ni l'autre,
puisque c'est elle qui **établit** la session.

Couverture vérifiée sur le journal du 14 août : les seules requêtes FUT sans
rien pour les router étaient des `curl` de diagnostic depuis le Mac. Tout ce
que la console a réellement émis porte de quoi choisir un club.

Le nucleus id identifie le **profil**, pas la console — ce qui est le bon
grain : un club FUT appartient à un gamertag, un gamertag peut changer de
console, une console peut avoir plusieurs profils. `deviceId` et `macAddress`
sont aussi dans `/ut/auth` et identifient la console ; ils ne servent pas de
clé, mais ils sont utiles au diagnostic.

### Fait — 14 août 2026

L'état était en variables de module : `CLUB_INVENTORY`, `WALLET`,
`CARD_ACTIONS`, `MANAGER_TASKS`, `CLUB_SAVE`, `PACK_SHOP`, `CONSUMABLE_RACK`
dans `fifa14_blaze_server.py`, plus `CLUB_IDENTITY`, `TOURNAMENT_PROGRESS`,
`SEASON_PROGRESS` et `PERSONA` dans `fut_inventory.py` — environ deux cents
références.

Elles sont maintenant des **vues** (`TenantView`) sur le club de la requête en
cours, et l'état lui-même vit dans un `Tenant` que `TENANTS` indexe par
persona. Aucun site d'appel n'a changé : un site d'appel de ce dépôt est
presque toujours un comportement que quelqu'un a dû arracher à la console, et
une erreur y ressemble à un bug de jeu, pas à un refactor.

Ce qui reste vrai et qu'il faut garder en tête :

- La liaison est **par thread** et rendue en sortie de requête. Le serveur est
  un `ThreadingHTTPServer`, donc un thread par connexion ; mais la portée est
  explicite, parce qu'un thread réutilisé hériterait sinon du club précédent.
  La suite de tests, qui fait tourner un fichier entier sur un thread, l'a
  attrapé immédiatement.
- Le côté Blaze ne touche qu'une seule chose, `PERSONA`, et **ne lie rien** :
  il nomme son club (`TENANTS.get(state.xuid).persona.adopt(...)`). Lier le
  thread là était la première version et c'était faux — `Fifa14Protocol.handle`
  s'appelle sans connexion autour.
- Les 14 019 cartes du catalogue sont lues **une fois et partagées**. Chaque
  club garde son propre `CardCatalogue` — `served` et `sold` sont les siens,
  une carte achetée par un joueur ne doit pas disparaître du marché d'un autre
  — mais analyser 3,7 Mo de JSON par club est la différence entre un serveur
  qui en tient vingt et un qui n'en tient pas.
- Un club sans sauvegarde à lui **lit** l'ancienne sauvegarde unique, et
  n'y réécrit jamais. C'est ce qui fait suivre le club qui existe déjà sur
  cette console — 963 millions de crédits, une saison en cours — vers son
  propriétaire, et ça laisse l'original intact comme sauvegarde.

### Ce qui doit encore être tranché

- **Rétention** : un club par persona qui n'est jamais purgé, sur un serveur
  public, croît sans limite. 175 Ko par club ici.
- ~~**Confiance**~~ — réglé le 14 août, voir ci-dessous.
- **Concurrence** : deux requêtes du même club en parallèle écrivent la même
  sauvegarde. Un verrou par locataire suffit.

## Ce qui est livré, et ce qui ne l'est jamais

**Livré** : le plugin `.xex`, le `fifa14revival.ini`, le serveur, ses trois
JSON, la documentation d'installation.

**Jamais livré** : `default.xex`, TU3 patché ou non, `patch.big`, archives du
jeu, clés console, KV, profils, captures de session. `NOTICE.md` et
`SECURITY.md` disent déjà tout ça ; une release ne les assouplit pas.

## Ordre des travaux

1. ~~**Router l'état par persona**~~ — fait le 14 août, voir ci-dessus.
2. ~~**Externaliser la configuration**~~ — fait le 14 août, `fifa14revival.ini`.
3. **Le plugin Dashlaunch** — le seul vrai obstacle technique restant.
4. Plus tard, si tu veux supprimer aussi le plugin : le patch statique, qui
   demande de gagner 5 % sur l'encodeur LZX *et* de résoudre la question de la
   distribution de fichiers EA.


## L'authentification, pour une bêta ouverte — 14 août 2026

L'identifiant de session était `LOCAL-XBOX360-FIFA14-SID-<xuid>`, **dérivé** du
persona qu'il nommait. Sur un LAN c'est sans conséquence : personne d'autre
n'atteint le serveur. Publiquement, ça veut dire que **le justificatif *est*
l'identifiant d'utilisateur** — et un XUID Xbox n'est pas un secret. Qui en
connaît un prend le club correspondant, vend ses cartes et vide son
portefeuille.

Une bêta ouverte ne peut pas partir avec ça.

### Ce qui a changé

`SessionStore` émet un jeton **aléatoire** (`secrets.token_urlsafe(24)`) à
`/ut/auth` et retient la correspondance jeton → persona. Le jeton n'a plus
aucun rapport avec le XUID.

La table est **écrite sur disque**, à côté des sauvegardes de club. C'est ce
qui rend le changement viable : `tools/fut.sh` redémarre le serveur à chaque
lancement, et une table en mémoire déconnecterait toutes les consoles à chaque
fois — l'objection même qui rendait l'identifiant dérivé séduisant.

Un jeton par persona : un nouveau `/ut/auth` remplace le précédent.

### Ce que le serveur accepte comme preuve

| source | accepté | pourquoi |
|---|---|---|
| `X-UT-SID` connu du magasin | oui | la seule vraie preuve |
| corps de `/ut/auth` (`nuc`) | amorçage | c'est la requête qui *établit* la session |
| en-tête nucleus sur `accountinfo` | amorçage | demandé ~90 s **avant** `/ut/auth`, et en lecture seule |
| en-tête nucleus ailleurs | **non** | c'est l'identifiant d'utilisateur à ciel ouvert |

Une requête qui ne prouve rien tombe sur le club par défaut. Sur un serveur
public ce club ne contient les cartes de personne, donc l'échec d'une requête
non prouvée est de ne rien voir, pas de voir le club d'un autre.

### Ce qui reste ouvert

Le trafic est en clair — pas de TLS sur ce profil. Le jeton passe donc en clair
sur le réseau de l'utilisateur. Contre un attaquant sur le même réseau que la
victime, ça ne protège pas ; contre le reste d'Internet, si.
