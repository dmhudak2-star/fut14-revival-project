# Handoff ChatGPT — FIFA 14 FUT Xbox 360 offline revival

Date : 6 août 2026  
Matériel : Xbox 360 RGH/JTAG + XBDM/JRPC2  
Xbox : `192.168.1.25`  
Mac / serveur local : `192.168.1.36`  

## Objectif non négociable

Faire fonctionner le vrai premier parcours FUT 14 hors ligne sur Xbox 360 :

1. loader FUT ;
2. popup `Chargement…` ;
3. téléchargement des dernières mises à jour ;
4. écran noir ;
5. vidéo d'introduction FUT ;
6. sélection des quatre capitaines ;
7. création réelle du club.

Ne pas considérer comme un succès un écran chargé artificiellement, un événement frontend forcé ou un saut direct vers `FutCreateClub`. La progression doit provenir des opérations natives CardsDLL et des réponses du serveur local.

## Dépôt de travail

```text
/Users/imranesallak/Documents/Codex/2026-07-27/capture-analys-e-elle-fonctionne-correctement/publish/fifa14-fut-offline-revival
```

Référence PC clonée localement :

```text
/Users/imranesallak/Documents/Codex/2026-07-27/capture-analys-e-elle-fonctionne-correctement/references/loopizzle-fifa14
```

Références publiques :

- <https://github.com/Loopizzle/FIFA-14-Ultimate-Team-Personal-Revival-Project>
- <https://github.com/ZamboniDevelopment/Zamboni3>
- <https://github.com/Aim4kill/Bug_OldProtoSSL>

Le worktree est volontairement sale et contient de nombreux outils/essais non commités. Ne supprimer ni réinitialiser les changements existants.

## Dernier jalon réellement confirmé

La connexion globale du titre au serveur local fonctionne désormais de bout en bout :

- redirector Xbox reçu sur le Mac ;
- connexion Blaze locale sur `10041` ;
- `Authentication2` locale réussie ;
- `GET /connect/auth` local reçu ;
- `GET /futBoot.xml` local reçu ;
- pings Blaze toutes les vingt secondes.

Journal de preuve :

```text
runtime/live-blaze-auth-v26.jsonl
```

Dans ce journal, la séquence commence vers `2026-08-06T13:41:40+0200` et contient `authentication2_login`, puis `fut_boot_served`.

## Cause exacte du premier échec Cards Authentication

Le constructeur JSON Xbox de `Cards Authentication` récupérait deux pointeurs nuls :

- `EASW-Session` ;
- `EASW-Token`.

Dans `powdllzf.xex.dll` :

- objet Auth vtable : `0x89707078` ;
- méthode Authentication : `0x897381E8` ;
- constructeur JSON : `0x8974D0E8` ;
- gate session : `0x8974D2C8` ;
- gate token : `0x8974D31C`.

Le nouvel outil suivant fournit deux valeurs locales uniquement au constructeur natif :

```text
tools/fifa14_cards_auth_credentials_patch.py
```

Valeurs :

```text
LOCAL-FIFA14-EASW-SESSION
LOCAL-FIFA14-EASW-TOKEN
```

Résultat prouvé : avant le correctif, `0x897381E8` retournait `0`; après le correctif, la même méthode native a retourné `1`. Le traceur `fifa14_pow_auth_trace.py` a confirmé une invocation sur l'objet `0xBF68C250` pendant la dernière session valide.

Donc le constructeur JSON n'est plus le blocage.

## Nouveau blocage exact découvert à la fin

Après la réussite du constructeur, aucune requête HTTP n'atteignait encore le Mac. La lecture directe de l'objet Auth a donné :

```text
Auth+0x600 = http://pal.gt.easfc.ea.com:8094
Auth+0x700 = http://pal.gt.easfc.ea.com:8094
```

La méthode `0x897381E8` construit précisément :

```text
%s/%s
base = Auth+0x600
route = pow/auth
```

Elle essaie donc encore :

```text
http://pal.gt.easfc.ea.com:8094/pow/auth
```

Ce n'est pas le `/ut/auth` du client PC Loopizzle. C'est la variante Xbox du même parser.

Deux changements ont été préparés :

1. `server/fifa14_blaze_server.py` normalise maintenant `/pow/auth` vers `/ut/auth` et `/game/fifa14/...` vers `/ut/game/fifa14/...` ;
2. `tools/fifa14_cards_auth_endpoint_patch.py` remplace les deux slots URL dans un objet Auth vivant par `http://192.168.1.36:18080`.

Les tests du serveur, dont l'alias `/pow/auth`, passent.

Le patch d'endpoint n'a pas encore pu être validé en direct : quand il a été lancé, la popup d'erreur avait déjà détruit l'objet Auth (`Cards root + 0x3A08 = 0`).

## Prochaine correction recommandée

Éviter la course manuelle « attendre l'objet puis patcher ». Le bon correctif est d'intégrer la substitution de l'URL avant l'envoi natif, de préférence par l'une de ces méthodes :

1. trouver le constructeur de l'objet Auth qui remplit `+0x600/+0x700` et remplacer la source URL par l'adresse locale ; ou
2. étendre le hook réversible placé à l'entrée de `0x897381E8` pour écrire les deux slots URL dans l'objet `r3` avant d'exécuter l'instruction déplacée et de continuer la méthode originale.

La deuxième solution est probablement la plus rapide et déterministe. Elle ne manipule pas le frontend : elle ne fait que rediriger le transport de la vraie opération Authentication.

Après ce hook, la preuve attendue est une vraie ligne :

```text
fut_ut_auth_request  POST /pow/auth
```

dans le journal du serveur, suivie de l'acceptation du header :

```text
X-UT-SID: LOCAL-XBOX360-FIFA14-SID
```

## Réponses serveur déjà implémentées

Le serveur local sait déjà répondre à :

- `/ut/auth` et `/pow/auth` ;
- `/ut/game/fifa14/user/accountinfo` avec `returningUser=0` et `userClubList=[]` ;
- `/ut/game/fifa14/phishing/trusteddevice` ;
- `/ut/game/fifa14/phishing` et `/phishing/question` ;
- `/ut/game/fifa14/phishing/validate` ;
- `/ut/game/fifa14/user/action` ;
- mise à jour d'une action utilisateur ;
- `/fut/packs/icebreaker/icebreakerpacklist.json` avec quatre capitaines ;
- locstrings Xbox 360 leaderboard/icebreaker.

Le SID de test est :

```text
LOCAL-XBOX360-FIFA14-SID
```

## Patches helperFunctions Loopizzle

Le TU3 Xbox contient bien le même `helperFunctions.apt`. Les trois continuations Loopizzle ont été reproduites :

- `checkForFUTRosters -> futSquadLoadSuccess` ;
- continuation LiveDB ;
- `proceedEnterFUT -> enterFutCallback`.

Outil runtime :

```text
tools/fifa14_tu3_helperfunctions_runtime_patch.py
```

L'adresse heap change selon le lancement. Sur les deux dernières exécutions elle était `0xBDD78B00`, mais il faut toujours valider la signature et le hash. L'outil le fait. Ne jamais réutiliser aveuglément l'adresse.

## Procédure de reprise propre

### 1. Lancer le serveur dans une session persistante

Ne pas lancer avec un simple `&` dans un shell éphémère : c'est ce qui a tué silencieusement v25 et produit une fausse erreur réseau.

```bash
cd '/Users/imranesallak/Documents/Codex/2026-07-27/capture-analys-e-elle-fonctionne-correctement/publish/fifa14-fut-offline-revival'

python3 server/fifa14_blaze_server.py \
  --listen 0.0.0.0 \
  --advertise 192.168.1.36 \
  --ports 10041,42124,42126,42127 \
  --journal runtime/live-blaze-auth-next.jsonl \
  --account-state runtime/local-account.json
```

Le garder au premier plan dans un PTY/session persistante.

### 2. Armer le démarrage avant de lancer FIFA

```bash
python3 tools/fifa14_early_local_server.py \
  192.168.1.25 \
  --local-ip 192.168.1.36 \
  --timeout 600 \
  --redirect-fut-resource \
  --trace-ion-unload \
  --trace-fut-launcher-transition \
  --trace-nav-transition-dispatch
```

### 3. Une fois `powdllzf.xex.dll` chargé

```bash
python3 tools/fifa14_cards_auth_credentials_patch.py \
  192.168.1.25 apply
```

Puis localiser/appliquer `helperFunctions` :

```bash
python3 tools/fifa14_tu3_helperfunctions_runtime_patch.py \
  192.168.1.25 \
  --timeout 300 \
  --chunk-size 0x800000
```

### 4. Avant le clic FUT

Armer :

```bash
python3 tools/fifa14_fut_auth_completion_trace.py 192.168.1.25 apply
python3 tools/fifa14_pow_auth_trace.py 192.168.1.25 apply
```

Mais surtout, intégrer d'abord le nouveau hook d'endpoint déterministe décrit plus haut. L'outil actuel `fifa14_cards_auth_endpoint_patch.py` ne fonctionne que si l'objet Auth est déjà vivant et risque donc de perdre la course.

## État de la console au handoff

- L'injecteur de manette `XamInputGetState` a été restauré à son trampoline original.
- Le dernier objet Auth a été détruit après la popup d'erreur ; il faudra relancer une session FUT propre.
- Le serveur v27 a été relancé dans une session persistante juste avant ce handoff, mais ne pas supposer qu'il survivra à la fermeture de la tâche Codex : vérifier les ports `10041` et `18080`.
- La console et XBDM étaient joignables sur `192.168.1.25:730`.

## Ce qui ne doit plus être refait

- ne plus tracer au hasard le texte des popups ;
- ne plus forcer `EnterFUT2`, `login-success`, `GameSceneEnable`, `FutCreateClub` ou un écran ION pour prétendre progresser ;
- ne plus conclure que Blaze est cassé : le login local Blaze et `futBoot` sont prouvés ;
- ne plus conclure que le JSON builder échoue : avec les deux credentials locaux, `Authentication` retourne maintenant `1` ;
- ne plus redémarrer au dashboard entre chaque lecture si le module et les objets nécessaires sont encore vivants ;
- ne pas lancer le serveur en arrière-plan depuis un shell qui se termine.

## Critère du prochain succès

Le prochain jalon n'est pas visuel. Il faut obtenir dans l'ordre :

1. invocation de `0x897381E8` ;
2. vrai `POST /pow/auth` reçu sur `192.168.1.36:18080` ;
3. callback natif de succès avec SID ;
4. première opération `/game/fifa14/...` reçue ;
5. seulement ensuite observer la progression du loader vers la sécurité et le premier-use flow.


## Reprise automatisée ajoutée après le handoff

Le script `tools/run_fifa14_xexmenu_session.command` suit désormais la reprise
propre depuis XeXMenu sans course manuelle :

1. démarre ou réutilise le serveur local ;
2. attend `default.xex` lancé depuis XeXMenu et applique le redirecteur précoce ;
3. arme le traceur natif FUT auth/config avant la reprise du titre ;
4. attend le module `powdllzf.xex.dll` ;
5. applique les credentials locaux puis le hook d'endpoint déterministe ;
6. localise et patche le `helperFunctions.apt` TU3 validé ;
7. affiche `READY_FOR_FUT_CLICK` seulement lorsque tout est armé.

Le nouveau helper est :

```text
tools/fifa14_cards_auth_runtime_setup.py
```

Il ne force aucune opération, aucun callback, aucun événement frontend et
aucune navigation. Le hook d'endpoint remplace le traceur passif
`fifa14_pow_auth_trace.py`, car les deux possèdent l'entrée `0x897381E8`.

Validation hors console dans la session ChatGPT normale : suite complète
`77 passed`. La validation live reste à effectuer depuis le Mac du LAN, car
l'environnement ChatGPT n'a pas accès à `192.168.1.25` ni `192.168.1.36`.
