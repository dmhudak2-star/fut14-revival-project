# Héberger le serveur du revival sur un VPS

Le serveur est ce qui répond aux requêtes du jeu. Il tourne sur **Python 3.10+
nu, sans aucune dépendance à installer** — tout ce qu'il utilise est dans la
bibliothèque standard. `capstone` dans `requirements.txt` ne sert qu'aux outils
de désassemblage, pas au serveur.

Ce guide couvre le serveur. Les correctifs côté console sont un chantier séparé
(`docs/PLUGIN.md`) ; tant que le plugin n'existe pas, chaque joueur a encore
besoin d'un PC avec XBDM pour patcher. Le serveur public est nécessaire à la
release, il n'est pas suffisant à lui seul.

## Ce qu'il faut

- un VPS avec une **IPv4 publique statique** ;
- Python 3.10 ou plus ;
- les ports **10041**, **18080** et **42124/42126/42127** ouverts en TCP entrant.

L'IPv4 statique n'est pas négociable : l'adresse est écrite dans la mémoire du
titre au lancement (par le plugin, à terme), et le budget des chaînes EAS FC
n'accepte qu'une IP, pas un nom. Une IP qui change casse toutes les
installations qui la portent — d'où l'intérêt de faire résoudre un nom par le
plugin plus tard, mais côté serveur c'est bien une IP fixe qu'on héberge.

Les ressources sont dérisoires : le catalogue de 14 019 cartes est partagé
entre tous les clubs, chaque club pèse ~175 Ko, une réponse d'écran quelques
dizaines de Ko. Un VPS à 4 €/mois tient largement.

## Installation

```sh
# sur le VPS, en root
useradd --system --home /opt/fifa14-revival --shell /usr/sbin/nologin fifa14
# déposer le paquet (voir « Fabriquer le paquet » plus bas) dans /opt/fifa14-revival
tar xzf fifa14-revival-server.tgz -C /opt/fifa14-revival --strip-components=1
chown -R fifa14:fifa14 /opt/fifa14-revival

# la configuration
cd /opt/fifa14-revival
cp fifa14revival.example.ini fifa14revival.ini
# éditer fifa14revival.ini : server.host = <IP publique du VPS>

# le service
cp deploy/fifa14-revival.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fifa14-revival
journalctl -u fifa14-revival -f
```

`server.host` doit être l'**IP publique du VPS** — celle que la console
joindra. Pas `auto` : `auto` résout l'adresse LAN locale de la machine, juste
sur un poste de bureau, faux sur un VPS.

## Vérifier

```sh
curl -s http://<IP>:18080/ut/game/fifa14/season/user
# -> {"seasonId":10,"divisionId":9,"round":1}   (le club par défaut, sans jeton)
```

Une réponse JSON = le serveur écoute et répond. Le `season/user` renvoyé ici
est celui du club par défaut, ce qui est normal sans en-tête `X-UT-SID`.

## L'état, et sa croissance

Tout l'état vit dans `runtime/`, inscriptible par l'utilisateur `fifa14` :

- `runtime/clubs/<persona>.json` — un club par joueur (~175 Ko chacun) ;
- `runtime/sessions.json` — la table jeton→persona de l'authentification ;
- `runtime/blaze-server.jsonl` — le journal ;
- `runtime/local-account.json` — l'état de compte de la connexion en cours.

**Décision à prendre avant d'ouvrir la bêta** : `runtime/clubs/` croît sans
limite, un fichier par persona qui se connecte, jamais purgé. À 175 Ko le club,
c'est lent, mais sur un serveur ouvert ça finit par compter. Options, non
tranchées : ne jamais purger et surveiller la taille ; purger les clubs
inactifs depuis N jours ; plafonner le nombre de clubs. Rien n'est implémenté —
c'est un choix produit, documenté aussi dans `docs/RELEASE.md`.

## Ce que les joueurs appellent, en plus du jeu

`POST /revival/reset` remet l'état de compte à ce qu'un serveur fraîchement
démarré porte. Ce n'est pas un confort : le titre réécrit cet état depuis sa
session en mémoire en quelques secondes, donc rentrer dans FUT sans relancer ne
peut pas marcher. Sur cette machine-ci, `tools/fut.sh` l'obtenait en vidant
`runtime/local-account.json` et en redémarrant le serveur ; à travers le
réseau, ni l'un ni l'autre n'est disponible, et `tools/revival_client.py`
appelle donc cette route juste avant de lancer le titre.

Cet état est **par joueur** depuis le 20 août, comme les clubs :
`runtime/accounts/<persona>.json`. La persona 0 — une console qui ne s'est
jamais nommée — garde l'ancien fichier `runtime/local-account.json`, donc une
installation à un seul joueur ne change pas.

Ça n'a pas été corrigé par précaution : le jour même, un second joueur s'est
connecté au serveur public et `local-account.json` est revenu en portant **son**
gamertag. Les clubs étaient déjà séparés, donc rien de visible n'a cassé — le
club est ce qui porte les cartes et les crédits — mais l'identité et le
`FirstTimeFlag` étaient partagés, et `/revival/reset`, que chaque lancement
envoie, remettait l'autre joueur à son premier démarrage en pleine partie.

## Sécurité, sans enjoliver

- **Le trafic est en clair.** Pas de TLS sur ce profil. Le jeton de session
  passe en clair sur le réseau du joueur ; contre quelqu'un déjà sur ce réseau,
  il ne protège pas. Contre le reste d'Internet, si (voir `docs/RELEASE.md`,
  section authentification).
- **N'exposer que les ports nécessaires.** 10041, 18080, 42124/42126/42127.
  Rien d'autre.
- Le serveur répond à des URL qui ressemblent à celles d'EA. C'est un service
  de préservation pour des consoles que leurs propriétaires possèdent ; ça
  n'héberge aucun contenu EA (voir `NOTICE.md`).

## Fabriquer le paquet

Depuis le dépôt, sur n'importe quelle machine avec Python :

```sh
tools/package_server.py --output fifa14-revival-server.tgz
```

Ça rassemble exactement les fichiers du runtime — les trois modules serveur,
les quatre JSON de données, `revival_config.py`, le `.ini` d'exemple et les
artefacts de `deploy/` — et rien d'autre. Aucun fichier de jeu, aucune donnée
de club, aucun outil de désassemblage.
