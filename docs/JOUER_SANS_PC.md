# Donner accès à quelqu'un d'autre

La question posée : *comment lui donner accès facilement et qu'il fasse tourner
solo ?* Réponse honnête, en deux morceaux, parce que le Mac fait deux choses
sans rapport l'une avec l'autre et qu'elles se suppriment séparément.

| ce que fait le Mac aujourd'hui | ce qui le remplace | état |
| --- | --- | --- |
| **répondre au jeu** (Blaze + HTTP) | un VPS, partagé par tout le monde | **fait** |
| **écrire en mémoire** dans la console (XBDM) | un plugin Dashlaunch | **squelette non compilé** |

Donc : « zéro machine du tout » n'existe pas encore. Ce qui existe déjà, et qui
suffit pour un joueur qui n'a pas de PC, c'est **un VPS + son téléphone**.

## 1. Le serveur : une fois, chez toi, pour tout le monde

Il tourne sur Python 3.10 nu, sans une seule dépendance à installer. Un VPS à
4 €/mois tient largement — le catalogue de 14 019 cartes est partagé entre tous
les clubs, chaque club pèse ~175 Ko.

```sh
tools/package_server.py --output fifa14-revival-server.tgz
# puis docs/DEPLOY.md, qui donne le service systemd et les ports à ouvrir
```

Le serveur est **multi-locataire** depuis le 14 août : un club par persona,
routé par le jeton de session que le client réémet lui-même. Deux consoles
dessus ne s'écrasent plus. Ton ami ne configure rien de ce côté : il reçoit une
adresse.

Ce qui transite, c'est du JSON — 4 Mo de cartes, consommables et packs. Les
images et les cartes sont lues sur **le disque de sa console**, par le *native
FUT-resource redirect*. Aucun fichier EA n'est distribué, et `NOTICE.md` reste
respecté.

## 2. La console : un téléphone Android suffit

C'est le point qui n'était pas évident et qui change la réponse.

**Toute la chaîne de patch est en bibliothèque standard pure.** Pas de
capstone, pas de pip, pas de virtualenv — `capstone` dans `requirements.txt`
appartient aux outils de désassemblage, et rien sur ce chemin ne les importe.
Vérifié : les cinq scripts du chemin critique n'importent que `argparse`,
`socket`, `select`, `struct`, `json`, `time`.

Donc ça tourne sous **Termux**, sur le téléphone Android qu'il a déjà. Pas de
Winlator, pas de Wine, pas de PC.

```sh
# dans Termux, une fois
pkg install python git
git clone <le dépôt>
cd fifa14-fut-offline-revival

# à chaque partie, console sur le dashboard, téléphone sur le même Wi-Fi
python3 tools/revival_client.py --console 192.168.1.25 --server <IP du VPS>
```

`tools/revival_client.py` est `tools/fut.sh` moins le serveur : il lance le
titre, applique les trois étages de correctifs, puis **garde le troisième
appliqué** — le titre recharge `helperFunctions` plusieurs fois et un patch posé
une seule fois se fait écraser. Il affiche `PRÊT`, et il faut laisser la fenêtre
ouverte pendant qu'on entre dans Ultimate Team.

Deux différences délibérées avec `fut.sh`, et ce sont des différences Termux :

* **Pas de zsh, pas de tâche de fond.** Le surveillant tourne au premier plan.
  Un `nohup` qu'Android tue en silence ressemblerait exactement à un
  surveillant qui n'a rien à faire.
* **La remise à zéro est une requête, pas un fichier.** `fut.sh` vide
  `runtime/local-account.json` et redémarre le serveur ; à travers le réseau,
  ni l'un ni l'autre n'est disponible. `POST /revival/reset` dit la même chose
  à distance. Un serveur trop ancien répond 404 : ça coûte un `FirstTimeFlag`
  périmé, pas une partie.

### Ce qu'il lui faut sur la console

- une RGH/JTAG (il en a une), et **XBDM activé** dans Dashlaunch — c'est le seul
  prérequis console, et c'est une case à cocher ;
- FIFA 14, build `default.xex` timestamp `0x534C8977`, base `0x82000000` ;
- la console et le téléphone sur le même réseau local.

## 3. Ce qui supprimerait aussi le téléphone

Le plugin Dashlaunch : un `.xex` résident, une ligne dans `launch.ini`, et plus
rien à lancer. `docs/PLUGIN.md` est la spécification complète et `plugin/` le
squelette — écrit sans chaîne PowerPC, jamais compilé ni exécuté. Ce qui manque
est nommé et fini :

1. remplir les `TODO(sdk)` de `plugin.c` (en-têtes XDK ou libxenon,
   notifications de chargement de module, flush du cache d'instructions) ;
2. compléter l'étage 1 — le manifeste le marque `"complete": false`, parce que
   le lanceur installe aussi des stubs de trace dont on n'a pas séparé le
   nécessaire du diagnostique ;
3. la résolution de nom au démarrage, pour qu'une IP de VPS gravée dans mille
   installations ne devienne pas un point de rupture ;
4. compiler et vérifier sur une vraie RGH.

Rien là-dedans n'est de la recherche : les adresses et les octets sortent de
`tools/extract_patch_manifest.py`, généré depuis les patcheurs qui tournent
réellement sur cette console. C'est une transcription, et il lui faut un
x86 Linux — le VPS qui héberge le serveur fait très bien l'affaire.

## Ce qu'il ne faut pas promettre

- **Pas de release « fichier à déposer dans le dossier du jeu »** aujourd'hui.
  Le patch statique demanderait de distribuer un `default.xex` et un TU3
  patchés, donc du code EA, ce que `NOTICE.md` interdit — et l'encodeur LZX est
  5 % au-dessus de l'emplacement disponible (`docs/TU3_STATIC_PATCH.md`).
- **Le trafic est en clair.** Pas de TLS sur ce profil : le jeton de session
  passe en clair sur le réseau du joueur. Contre quelqu'un sur son propre Wi-Fi,
  ça ne protège pas ; contre le reste d'Internet, si.
- **`runtime/clubs/` grossit sans limite** sur un serveur ouvert, et l'état de
  compte (`/revival/reset`) est encore **global** et non par locataire — deux
  joueurs qui relancent en même temps se marchent dessus sur ce seul point.
  À trancher avant une bêta ouverte, pas avant de dépanner un ami.
