# Handoff — FIFA 14 FUT Xbox 360 offline revival

Date : 6 août 2026 (session du soir, suite de `HANDOFF_CHATGPT_2026-08-06.md`)
Xbox : `192.168.1.25` — Mac/serveur : `192.168.1.36`
Dépôt de travail : `~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival`

L'objectif non négociable et la liste « ce qui ne doit plus être refait » du
handoff précédent restent valables. Ce document ne remplace que la partie
« dernier jalon » et « prochaine correction ».

## Résumé

Le blocage du handoff précédent est levé et validé en direct. Le blocage actuel
est identifié nommément : le premier parcours FUT échoue à l'étape
**sécurité/phishing**, sur un code d'erreur FCC non mappé.

## Ce qui est prouvé en direct cette session

Journaux : `runtime/live-blaze-auth-v29.jsonl` (premier `/pow/auth`),
`v31.jsonl` (identité adoptée), `v32.jsonl` (identité cohérente Blaze + FUT).

Les quatre critères du handoff précédent sont atteints :

1. `0x897381E8` invoqué — `invocation_count = 1`, objet `0xBF68EEE0` ;
2. vrai `POST /pow/auth` reçu sur `192.168.1.36:18080` ;
3. le client accepte la réponse et poursuit avec son
   `Easw-Session-Data-Nucleus-Id` ;
4. première opération `/ut/game/fifa14/user/accountinfo` reçue.

Le hook d'endpoint déterministe fonctionne : `REST URL slots = local, local`.

### Contrat exact envoyé par la Xbox à `/pow/auth`

```json
{
    "isReadOnly": false,
    "sku": "FFA14XBX",
    "clientVersion": 1,
    "nuc": 2535469248587161,
    "nucleusPersonaId": 0,
    "nucleusPersonaDisplayName": "Imskobogota6z",
    "locale": "fr-FR",
    "method": "cas",
    "priorityLevel": 5,
    "identification": {
        "EASW-Session": "LOCAL-FIFA14-EASW-SESSION",
        "EASW-Token": "LOCAL-FIFA14-EASW-TOKEN"
    }
}
```

Le schéma de réponse (`sid`, `serverTime`, `lastOnlineTime`) et celui de
`accountinfo` sont identiques à la référence PC Loopizzle. Vérifié dans
`references/loopizzle-fifa14/server/probe.py` et `server/local_identity.py`.
**Ce n'est donc pas un problème de schéma JSON.**

## Régression corrigée : le transport du redirecteur

`tools/run_fifa14_xexmenu_session.command` lançait le redirecteur en TLS. La
console négocie OldProtoSSL, que l'OpenSSL de Python refuse au niveau record :

```text
{"event": "tls_handshake_error", "local_port": 42124,
 "error": "SSLError: [SSL: WRONG_VERSION_NUMBER] wrong version number"}
```

Résultat : popup « Les serveurs EA ne sont pas disponibles » avant même le menu.
Le chemin prouvé de bout en bout est **plaintext** (`standardInsecure_v3`,
XNet `global-nosecure`), comme le jalon v26.

Le script utilise désormais plaintext par défaut.
`FIFA14_REDIRECTOR_TRANSPORT=tls` reste disponible pour retravailler le
handshake OldProtoSSL.

## Blocage actuel, nommé

Au clic FUT : popup « une erreur s'est produite lors de la connexion à FIFA 14
Ultimate Team », **sans aucune requête serveur**.

`tools/fifa14_localization_key_trace.py` donne la séquence exacte de clés
résolues juste avant le popup :

```text
0x8970E780  TXT_EASFC_SERVER_ERROR        <- string interne powdllzf.xex.dll
0xBE0AF6F4  FUT_SECURITY_TITLE
0xBE08835C  FUT_SECURITY_TIP
0xBE0E6B74  FUT_SECURITY_CHOOSE_QUESTION
0xBE0EA9A4  Unknown_FCC_Error
0xBDF39684  Okay_abbr2
```

Lecture : le client **entre bien dans le premier parcours FUT** et construit
l'écran de sécurité first-use (choix de la question secrète), puis échoue sur un
code d'erreur FCC pour lequel il n'a pas de libellé — d'où `Unknown_FCC_Error`.

C'est un blocage bien plus avancé que « le loader tourne » : l'étape sécurité
est la suite immédiate de `accountinfo` dans le contrat Loopizzle (stage 8).

### État natif corrélé

- `powdllzf.xex.dll` chargé à `0x89700000`, patches crédentials + endpoint armés ;
- helperFunctions TU3 patché (adresse heap re-validée à chaque lancement) ;
- FUT loader : `state = 1` (in-flight, jamais terminé), `available = 1` ;
- `IONUnloadViewEnqueue` et `IONActionDispatch28` : 8 invocations chacun ;
- `ViewManagerEnterFlow` = **0**, `ScreenFlowConstructor` = **0** ;
- aucune frame Blaze **composant 2148 (CardHouse)** n'est jamais émise ;
- `fifa14_fut_auth_completion_trace.py` (14 sondes) : aucune sonde touchée.

## Hypothèses écartées cette session (avec preuve)

- **Schéma JSON de `/ut/auth` ou de `accountinfo`** — identiques à la référence
  PC qui, elle, franchit cette étape.
- **Nom de persona incohérent.** Le serveur renvoyait `OfflineFUT` alors que la
  console présente `Imskobogota6z`. Corrigé (voir plus bas) et vérifié en direct
  jusqu'à `Blaze DSNM = Imskobogota6z` : **l'erreur persiste à l'identique**.
- **Route HTTP manquante.** Le serveur journalise maintenant toute route non
  gérée (`identity_http_unhandled`) : aucune n'apparaît. Le client n'émet
  simplement plus rien après `accountinfo`.

## Le popup est un timeout, pas un login rejeté

Le désassemblage de `CardsDLLzf.xex.dll` (mappé à `0x89000000` dès l'entrée FUT)
identifie exactement l'origine du popup.

La routine qui l'affiche est `0x8909F448`. Elle lit la propriété `0xA8` de son
objet : si elle vaut 2 elle prend une autre branche, sinon elle affiche
`ServerFatalError` / `Unknown_FCC_Error` (chaînes à `0x8900B158` et
`0x8900B16C`), une seule fois grâce au drapeau `this+0x4C`.

Deux appelants seulement :

1. `0x8911B0D4`, dans le dispatcher de messages `0x8911A998(this, message, param)`.
   Son `switch` envoie le message `0x65` — CardHouse `Login`, soit le
   `2148:101` du composant Blaze — directement sur la branche d'erreur, après
   un enregistrement de télémétrie `FUTT`/`DBUG`/`R4ER` code `0xA9`.
2. `0x8909FAF4`, dans le tick `0x8909FA50`. Il décrémente `this+0x48` à chaque
   passage et déclenche le popup quand ce compteur atteint exactement zéro.

Mesure en direct, traceur armé sur la notification `modload` de
`CardsDLLzf.xex.dll` pour capturer la toute première tentative
(`tools/fifa14_cards_message_dispatch_trace.py ... arm-on-load`) :

```text
handler invocations = 0
```

Le dispatcher **n'est jamais atteint**. Aucun message `0x65` n'arrive, ni en
succès ni en échec.

### Ce qui reste à vérifier sur ce chemin

Il serait tentant d'en conclure que le popup vient forcément du second appelant,
donc du compteur qui expire. **Cette conclusion n'est pas établie** et deux
éléments la contredisent :

- les deux constructeurs initialisent `this+0x48` à `-1`, et le tick traite
  toute valeur négative par un saut qui n'affiche rien et ne décrémente pas ;
  il faudrait donc qu'un tiers arme le compte à rebours, et ce tiers n'a pas
  été trouvé ;
- surtout, le traceur de localisation résout `Unknown_FCC_Error` depuis une
  adresse de **heap** (`0xBE0EA9A4`), pas depuis la chaîne rdata de CardsDLL
  (`0x8900B16C`). Le dialogue affiché peut donc provenir du front-end FUT
  résolvant la même clé, sans passer par `0x8909F448`.

L'outil `tools/fifa14_cards_message_dispatch_trace.py` trace désormais aussi
`0x8909F448` et rapporte `ServerFatalError popup calls`. Un compteur à zéro
lors d'une reproduction prouvera que le chemin C++ CardsDLL n'est pas celui
emprunté, et renverra l'enquête vers le front-end.

### Ce qui est solidement établi

Le dispatcher de résultats n'est pas atteint, aucune frame du composant 2148
n'est émise, aucune requête HTTP ne suit `accountinfo`, aucune route non gérée
n'est demandée, et le hook `connect` ne compte aucune connexion supplémentaire.

En revanche — et cela corrige une affirmation antérieure de ce document — **le
login est bien émis**. Voir la section suivante.

## La table d'API native FUT, et le login effectivement appelé

L'initialiseur `0x89107480` construit la surface FUT de CardsDLL sous forme
d'enregistrements de 12 octets `{?, handler, nom}`. Reconstruite statiquement
depuis l'image du module, elle donne **75 opérations nommées**, dont toute la
route vers un premier match :

| Opération | Handler |
| --- | --- |
| `LoginToFUT` | `0x89105D18` |
| `FirstTimeInit` | `0x89105D50` |
| `GetIdentityData` | `0x89105EA0` |
| `CardsDownloaded` | `0x89105E68` |
| `CreateClub` | `0x891061E0` |
| `CreateMatch` | `0x89106218` |
| `ServiceQuickMatch` | `0x89106130` |
| `MatchReady` | `0x89226270` |

`tools/fifa14_fut_api_trace.py` trace n'importe laquelle de ces opérations par
son nom, avec un mode `arm-on-load` qui s'arme sur la notification `modload` de
CardsDLL pour ne pas rater le premier appel.

Mesure en direct :

```text
LoginToFUT invocations = 1
     0  r3=0xBD9DE7B0 r4=0x00000000 r5=0xBD9DE744 lr=0x824112FC
FUT service object = 0xB5AA3018
```

Le front-end appelle donc bien `LoginToFUT`, une fois, depuis `0x824112FC` dans
`default.xex`. Le service vivant est `0xB5AA3018`, de vtable `0x89008E90`, et
`LoginToFUT` en invoque le slot `+0x04`, soit `0x8908D350`.

Le désassemblage de `0x8908D350` montre une méthode **synchrone** : elle monte
l'état local via `0x8909EA30`, `0x8909DD90` et `0x8909EBD8` — les mêmes routines
que celles qui entourent le constructeur du manager FUT — puis émet un
enregistrement de télémétrie `_global` / `LoginToFUT`. Elle n'attend aucune
réponse serveur.

Autrement dit, `LoginToFUT` est une initialisation locale, et l'opération en
ligne qui devrait suivre est ailleurs dans cette même table.

Corrélé côté réseau, sur toute la session : une seule connexion Blaze, aucune
frame du composant 2148, aucune requête HTTP après `accountinfo`, aucune route
non gérée. Le client n'émet rien et attend.

## Vérifications supplémentaires, toutes négatives

Trois pistes plausibles ont été fermées par la mesure, pour éviter qu'une
prochaine session les rouvre :

- **Le contrat de réponse `/pow/auth`.** La table de chaînes du parser Xbox dans
  `powdllzf.xex.dll`, à `0x897107BC`, contient exactement et seulement
  `lastOnlineTime`, `serverTime`, `sid`, à côté de `text/json` et de l'en-tête
  `Accept: application/json`. C'est précisément ce que le serveur renvoie.
- **Une connexion CardHouse séparée.** Le journal du hook `connect` compte cinq
  appels sur toute la session, le dernier vers `192.168.1.36:18080`. CardsDLL
  n'ouvre aucune connexion propre : il réutiliserait la connexion Blaze
  existante, sur laquelle il n'envoie jamais rien.
- **Une clé de configuration manquante.** Les seules clés de config que
  `CardsDLLzf.xex.dll` sait lire sont `CARDS/DIRECTED_BLAZEENV`,
  `FUT/MODULE_BASEURL_%s`, `FUT/SINGLE_BASEURL_%s`, `FUT/IS_RETURNING_USER`,
  `FUT/FORCE_TUTORIALS`, `FUT/DISABLE_TUTORIALS`,
  `FUT/ALWAYS_SHOW_SMART_TUTORIALS`, `FUT/ALWAYS_SHOW_QUESTS_PANEL`,
  `FUT/FUT_STAT_TUNING` et `FUT/LOG_RPUPS`. Le serveur fournit déjà toutes
  celles qui ne sont pas de simples réglages d'affichage.

À noter pour la suite : `/pow/auth` et `accountinfo` partent pendant le boot du
titre, avant tout clic FUT, et le menu affiche malgré tout « EAS FC non
connecté ». Le sous-système EASFC de `powdllzf` et le bootstrap FUT de
`CardsDLLzf` sont donc deux clients distincts du même serveur local.

## Le blocage exact : la requête `0xDF` de `FirstTimeInit`

En armant toutes les opérations tracées sur la notification `modload` de
CardsDLL puis en sélectionnant FUT, la séquence complète tient en un run :

```text
LoginToFUT             1 call(s)   lr=0x824112FC
FirstTimeInit          1 call(s)   lr=0x824112FC
GetIdentityData        never called
CardsDownloaded        never called
CreateClub             never called
CreateMatch            never called
...                    never called
FUT service object = 0xB5AA7018
```

Le front-end appelle exactement **deux** opérations, puis s'arrête.
`GetIdentityData`, étape suivante du parcours first-use retail, n'est jamais
atteinte, et le dialogue d'erreur suit.

`FirstTimeInit` est le slot vtable `+0x08`, soit `0x8908D3D0`. Contrairement à
`LoginToFUT` qui est purement local, il **émet une requête** :

```text
bl 0x89185500          ; manager
bl 0x8908CA10          ; objet de requête
li r4, 0xDF            ; identifiant d'opération 223
vtable+0x4C(r31, 0xDF) ; soumission
vtable+0x04(r31)       ; libération
```

Le blocage tient donc en une seule requête nommée : l'opération `0xDF` soumise
via le slot `+0x4C`. Rien ne lui succède, et aucune route HTTP ni frame Blaze
correspondante n'atteint le serveur local.

À noter : la table de noms d'opérations à `0x890A6980` ne couvre que les ids
0..81, donc `0xDF` appartient à une autre énumération et reste à nommer.

## Prochaine correction recommandée

La table d'API rend la suite mécanique plutôt qu'exploratoire. `LoginToFUT`
passe ; il faut maintenant savoir quelle opération suivante est appelée, et
laquelle ne revient pas.

1. Tracer dans l'ordre, avec `arm-on-load`, les opérations qui suivent le login :
   `FirstTimeInit`, `GetIdentityData`, `CardsDownloaded`. La première qui n'est
   jamais appelée, ou qui est appelée sans jamais rendre la main, désigne le
   blocage exactement. C'est une mesure par opération, sans rétro-ingénierie
   supplémentaire.
2. Le service FUT vivant est `0xB5AA3018`, vtable `0x89008E90`. Ses seize
   premiers slots sont relevés dans `docs/STATUS.md` ; le slot `+0x04` est le
   login. Les slots voisins couvrent le reste du cycle et se tracent de la même
   façon si besoin.
3. Le compteur `this+0x48` ne doit pas être neutralisé pour faire disparaître le
   popup : il ne ferait que masquer l'attente sans la résoudre.

Ne pas forcer l'écran de sécurité, ne pas sauter vers `FutCreateClub`, ne pas
remettre le loader `+0x114` à zéro : les interdits du handoff précédent
s'appliquent toujours.

## Adresses CardsDLL confirmées cette session

`CardsDLLzf.xex.dll` est mappé à `0x89000000`, taille `0x2B0000`, et n'est
chargé qu'à l'entrée FUT — d'où le mode `arm-on-load` de l'outil de trace.

| Rôle | Adresse |
| --- | --- |
| Popup `ServerFatalError` | `0x8909F448` |
| Chaîne `ServerFatalError` | `0x8900B158` |
| Chaîne `Unknown_FCC_Error` | `0x8900B16C` |
| Drapeau « popup déjà montré » | `this+0x4C` |
| Tick FUT / watchdog | `0x8909FA50` |
| Compteur watchdog | `this+0x48` |
| Décrément du compteur | `0x8909FB00` |
| Constructeurs initialisant le compteur | `0x8909E0B8`, `0x8909EAE8` |
| Dispatcher de messages | `0x8911A998` |
| Branche fatale du message `0x65` | `0x8911B098` |
| Télémétrie `FUTT`/`DBUG`/`R4ER` code `0xA9` | `0x8911B0CC` |

Dans `powdllzf.xex.dll`, la bannière « EAS FC non connecté » est produite par
`0x8978C920(this, kind)` avec la table `0` = `TXT_EASFC_SERVER_ERROR`,
`1` = `TXT_EASFC_PLEASE_SIGN_IN`, `2` = `TXT_EASFC_RECONNECTING`. Elle est
indépendante du popup FUT et n'est pas la piste à suivre.

## Modifications livrées (88 -> 91 tests, tous verts)

`server/fifa14_blaze_server.py`
- `request_body_preview()` : le corps des requêtes est journalisé, borné et
  sûr. C'est ce qui a révélé le contrat `/pow/auth` ci-dessus.
- `identity_http_unhandled` : toute route non modélisée est journalisée avant le
  404, avec sa méthode, son chemin normalisé et son corps.
- `auth_request_identity()` : le serveur adopte le persona que la requête d'auth
  présente réellement, au lieu d'un placeholder.
- `authentication2_login` : ne réécrase plus le persona adopté et réutilise le
  nom stocké, puisque Authentication2 ne transporte aucun `GTAG`.

`tools/fifa14_early_local_server.py`
- `--launch-title DIRECTORY` : lance le titre par XBDM **après** avoir armé
  l'écoute de la notification `modload`. Supprime l'étape manuelle XeXMenu et la
  course associée.

`tools/run_fifa14_xexmenu_session.command`
- plaintext par défaut, TLS opt-in via `FIFA14_REDIRECTOR_TRANSPORT` ;
- lancement automatique via `FIFA14_TITLE_DIRECTORY`.

Tests ajoutés : corps de requête et route non gérée, adoption d'identité et ses
cas de rejet, cohérence du persona Blaze, construction de la commande de
lancement.

## Procédure de reprise (une commande)

```bash
cd ~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival
FIFA14_TITLE_DIRECTORY='Hdd:\Games\FIFA 14' tools/run_fifa14_xexmenu_session.command
```

Le script démarre le serveur en plaintext, lance le titre, arme le redirecteur
précoce, les crédentials et le hook d'endpoint, puis le patch helperFunctions,
et affiche `READY_FOR_FUT_CLICK`.

Navigation sans manette physique :

```bash
python3 tools/xbox360_virtual_input.py 192.168.1.25 apply
python3 tools/xbox360_virtual_input.py 192.168.1.25 press START --frames 8
python3 tools/xbox360_virtual_input.py 192.168.1.25 press A --frames 8
python3 tools/xbdm_screenshot.py 192.168.1.25 runtime/current.png
```

Au démarrage le titre boucle ses vidéos d'attract : un `START` saute la vidéo et
révèle l'écran titre, un second `START` quelques secondes plus tard entre. Un
sélecteur de périphérique de stockage et un avertissement de sauvegarde
automatique demandent chacun un `A`.

L'environnement de test local :

```bash
python3 -m venv .venv && .venv/bin/pip install pytest capstone
.venv/bin/python -m pytest -q          # 91 passed
```
