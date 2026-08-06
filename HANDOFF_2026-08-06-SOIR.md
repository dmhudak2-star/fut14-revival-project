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
succès ni en échec. Le popup vient donc du second appelant : le compteur expire.

Ce compteur est un **watchdog fixe**. Il est initialisé une seule fois dans les
constructeurs CardsDLL (`0x8909E0B8` et `0x8909EAE8`, avec le reste de l'objet),
décrémenté en `0x8909FB00`, et jamais réarmé. Le délai observé entre
`accountinfo` et le popup est d'environ 65 secondes.

Conclusion : le bootstrap FUT ne reçoit **jamais** son résultat de login
CardHouse. Ce n'est pas un login refusé, c'est un login qui n'aboutit pas.

Corrélé côté réseau, sur toute la session : une seule connexion Blaze, aucune
frame du composant 2148, aucune requête HTTP après `accountinfo`, aucune route
non gérée. Le client n'émet rien et attend.

## Prochaine correction recommandée

La question n'est plus « pourquoi le login échoue » mais « pourquoi le login
n'est jamais émis ». Le point de mesure suivant est donc l'émetteur, pas le
récepteur.

1. Trouver qui **devrait** poster le message `0x65` au dispatcher `0x8911A998`.
   Le dispatcher est un consommateur de résultats ; son producteur est
   l'opération CardHouse `Login`. Repérer la méthode qui construit cette
   opération dans CardsDLL et y poser un hook passif du même type que
   `tools/fifa14_cards_message_dispatch_trace.py`, pour savoir si elle est
   appelée, et si oui où elle s'arrête avant d'atteindre le transport Blaze.
2. Vérifier comment CardsDLL obtient son point de connexion CardHouse. Le
   composant 2148 est bien annoncé dans notre `CIDS`, mais aucune frame ne part
   et aucune seconde connexion Blaze n'est ouverte. Si CardsDLL attend une
   valeur de configuration (`fetch_config`) ou une adresse que le serveur local
   ne fournit pas, l'opération peut ne jamais être construite.
   `ZamboniUltimateTeam` implémente ce composant pour NHL/HUT et reste la
   référence la plus proche du protocole attendu.
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
