# Le plugin Dashlaunch : spécification

Ce document dit comment construire le `.xex` résident qui remplace le Mac et
XBDM. Il ne contient pas le code compilé — cet environnement n'a pas de chaîne
PowerPC — mais il contient tout ce qu'il faut pour l'écrire sans rien
redécouvrir : les trois points d'accroche, la source unique des octets à
écrire, et les deux pièges qui coûtent une console gelée.

`docs/RELEASE.md` donne le pourquoi et l'ordre général. Ceci est le comment.

## La source des correctifs n'est pas ce document

Les adresses et les octets **ne sont pas recopiés ici**. Ils vivent dans les
outils Python qui patchent la vraie console aujourd'hui, et
`tools/extract_patch_manifest.py` les en extrait :

```
tools/extract_patch_manifest.py --ip 203.0.113.10 \
    --core-port 10041 --identity-port 18080 > plugin/patches.json
```

Le plugin se construit **à partir de ce JSON**, jamais à partir de constantes
transcrites à la main. Si un patcheur bouge une adresse, le manifeste bouge
avec, et la différence se voit au moment de la génération plutôt que comme un
plugin silencieusement cassé. C'est ce qui rend vraie la phrase de RELEASE.md :
« le plugin est une transcription, pas une redécouverte ».

Le manifeste est paramétré par l'adresse du serveur, parce que deux endroits
l'embarquent : le stub de redirection connect et les chaînes EAS FC. Tout le
reste est fixe pour le build supporté (`default.xex` timestamp `0x534C8977`,
base `0x82000000`).

## Identification du titre, et refus de tout le reste

Le plugin doit reconnaître FIFA 14 et ne toucher à rien d'autre. Le critère est
le timestamp de `default.xex` : `0x534C8977`. Tout autre titre, tout autre
timestamp — le plugin ne fait rien. Chaque écriture ci-dessous porte en plus
les octets d'origine attendus (`expect` dans le manifeste) ; le plugin les
vérifie avant d'écrire et abandonne si ça ne correspond pas, plutôt que de
corrompre un build qu'il ne connaît pas.

## Trois accroches, dans cet ordre

### 1. Au chargement de `default.xex`, avant le code du jeu

C'est l'instant que le Mac prend sur la notification `modload` de XBDM. Un
plugin l'obtient en accrochant le chargeur de titre (le point d'entrée que
Dashlaunch expose déjà pour les plugins de titre).

Écrire d'abord les **caves** (`stage1_launch.caves`), puis les **hooks**
(`stage1_launch.sites`) : les hooks branchent vers les caves, donc les caves
doivent exister avant. Enfin le **pointeur de profil**
(`stage1_launch.pointer`) — un simple mot à écrire.

Sept caves, cinq hooks, un pointeur. Le manifeste les nomme :

- `connect_stub` / `connect_hook` — détourne les seuls connects Blaze
- `ticket_stub` / `ticket_hook` — jeton hors-ligne
- `auth2_config_stub` / `auth2_config_hook` — configuration auth2
- `connect_redirect_stub` — **porte l'IP du serveur** ; à régénérer si l'adresse change
- `connect_result_stub`, `socket_security_stub` — sockets locales
- `xnet_nosecure` / `xnet_bypass` — deux branches à neutraliser (`60000000`, un `nop`)
- `redirector_profile` — pointe le redirector sur le profil en clair

Les hôtes EA d'origine sont **conservés**. Le résolveur du titre suit son chemin
normal ; c'est le connect qui est détourné. Ne pas toucher aux hôtes.

### 2. Au chargement de `powdllzf`

Deux chaînes réécrites **sur place** (`stage2_easfc.strings`) :

```
0x897061B0   content.lt.easfc.ea.com:8080   ->  http://<ip>:<identity_port>
0x89706250   pal.gt.easfc.ea.com:8094       ->  <ip>:<core_port>
```

Réécriture sur place, donc plus court ou égal à l'original obligatoire. Le
manifeste calcule si ça rentre (`fits`) : une IPv4 rentre toujours (voir
RELEASE.md), un nom d'hôte non — d'où la résolution côté plugin.

Le Mac *sonde* jusqu'à ce que le module soit mappé, et rate parfois l'écriture
(journal du 14 août : `unexpected content` à `0x89706250`). Le plugin accroche
le **chargement** du module et écrit à l'instant où il est mappé, ce qui
supprime la course.

### 3. Au chargement de `helperFunctions` (l'APT du TU3)

Trois branches (`stage3_tu3.branches`), aux offsets `APT+0x2C86`, `+0x2D92`,
`+0x2FEA`, chacune gardée par un contexte avant/après de 16 octets qui doit
correspondre avant l'écriture.

L'APT **n'est pas à une adresse fixe** : le manifeste donne une `signature` de
48 octets et le plugin localise l'APT en la cherchant. Mais c'est là que le
plugin fait mieux que le Mac : au lieu de sonder le tas toutes les cinq
secondes — et de risquer le balayage complet qui a gelé la console une fois — il
accroche le chargeur de ressource et applique les trois branches **à chaque
fois que l'APT est chargé**. Le titre le recharge plusieurs fois
(`docs/AUTOMATIC_PATCH.md`) ; un patch posé une seule fois se fait écraser. Une
accroche au chargement, elle, ne peut pas être en retard.

## La configuration vient du disque de la console

L'adresse du serveur ne doit pas être compilée dans le plugin. Elle vient de
`fifa14revival.ini`, posé à côté du plugin, au format déjà exercé par
`tools/revival_config.py` :

```ini
[server]
host = revival.example.net   ; ou une IP
core_port = 10041
identity_port = 18080
```

**`host` peut être un nom, et c'est même recommandé.** Une IP de VPS gravée dans
des milliers d'installations devient un point de rupture le jour où elle change.
Le plugin résout le nom **au démarrage**, une fois, et écrit l'IP résolue dans
le stub de redirection connect et dans les deux chaînes EAS FC — exactement là
où le manifeste marque l'adresse comme variable. La console a un résolveur ; ce
travail est du ressort du plugin, pas de la configuration.

Si la résolution échoue (pas de réseau, nom mort), le plugin n'applique pas les
correctifs réseau : le titre démarre normalement et dit seulement « connectez-
vous aux serveurs EA », ce qui est un échec lisible plutôt qu'un plantage.

## Ce que le plugin ne distribue jamais

Le plugin, c'est du code à nous. Il ne contient aucun octet de `default.xex`,
de TU3 ou de `powdllzf` — il les **modifie en mémoire** sur la console de
l'utilisateur, à partir de ses propres fichiers de jeu. C'est ce qui distingue
cette voie du patch statique (`docs/TU3_STATIC_PATCH.md`), qui exigerait de
distribuer des fichiers EA patchés et que `NOTICE.md` interdit.

## Ce qui reste à faire, concrètement

1. Un lecteur de `fifa14revival.ini` côté console, plus la résolution de nom.
2. Le squelette de plugin Dashlaunch : point d'entrée, trois accroches de
   chargement de module, application depuis `patches.json` (embarqué ou lu du
   disque).
3. La régénération des parties dépendantes de l'adresse après résolution.
4. La chaîne de génération : `extract_patch_manifest.py` → `patches.json` →
   plugin. Aucune étape ne recopie une adresse à la main.

Rien de tout cela n'a besoin de la console pour être écrit — seule la
vérification finale la demande. Le désassemblage et les octets sont figés dans
le manifeste, qui est testable ici.
