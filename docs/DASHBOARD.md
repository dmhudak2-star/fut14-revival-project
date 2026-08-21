# Le tableau de bord

Une page qui montre ce que le serveur est en train de vivre : qui joue, ce
qu'ils ouvrent, ce que le titre demande et n'obtient pas. Reprend la direction
artistique des menus de FIFA 14, parce que c'est la même chose qu'on regarde.

    http://<serveur>:8099/

## Ce qu'il faut savoir en premier

**C'est un second processus, et c'est le point.** Redémarrer
`fifa14-revival.service` éjecte qui est en session FUT à cet instant — le titre
retombe au menu principal de FIFA et la demi-heure non sauvegardée part avec.
Le dashboard, lui, est la chose qu'on redémarre vingt fois dans un après-midi
pendant qu'on travaille sa mise en page. Les deux ne peuvent donc pas partager
un processus. `fifa14-dashboard.service` s'arrête, se relance et se met à jour
pendant qu'une partie tourne, sans rien lui faire.

**Il ne peut rien écrire.** Le prix de cette séparation est qu'il voit les
clubs tels qu'ils sont *sur le disque*, pas les objets vivants dans la mémoire
du serveur — soit tout sauf les quelques secondes entre un changement et la
sauvegarde qui suit. Et il n'écrit pas : un fichier de club qu'il modifierait
serait écrasé sans bruit par la sauvegarde suivante du serveur de jeu. Toutes
les routes sont des GET. `tests/test_dashboard.py` sert chaque route puis
vérifie que `runtime/` est octet pour octet ce qu'il était.

## Installation

Le dashboard fait partie du dépôt ; il n'a aucune dépendance au-delà de
Python 3.10.

```sh
sudo cp deploy/fifa14-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fifa14-dashboard
journalctl -u fifa14-dashboard -n 5      # le code d'accès est sur la 2e ligne
```

En local, sans systemd :

```sh
python3 server/dashboard.py --root . --port 8099
```

### Le code d'accès

Généré une fois au premier démarrage et gardé dans
`runtime/dashboard-token.txt`, pour qu'un redémarrage ne mette pas le
propriétaire à la porte de son propre tableau de bord. Il se donne dans l'URL
une fois (`?k=…`, la page le range dans le navigateur et le retire de la barre
d'adresse) ou se tape sur l'écran d'entrée.

`--token ''` enlève la garde : juste sur un Mac derrière une box, faux sur un
VPS avec une adresse publique.

### Ouvrir le port

Chez IONOS, le pare-feu du panneau Cloud est séparé de celui de la machine :
`ufw` peut être inactif et le port rester injoignable. Il faut ajouter TCP 8099
dans **Réseau → Politiques de pare-feu**. Sans ça, un tunnel SSH suffit pour
regarder depuis le poste qui a la clé :

```sh
ssh -N -i ~/.ssh/fifa14_revival_deploy -L 8099:localhost:8099 root@<serveur>
```

## Ce que la page montre

| Onglet | Ce qu'on y lit |
| --- | --- |
| **Accueil** | Joueurs, masse monétaire, packs, matchs, disponibilité, flux récent, activité par heure sur 48 h |
| **Joueurs** | Un panneau par club : coins, note de l'équipe, cartes, packs, matchs, dernière trace, adresse |
| **Joueur** | Le onze de départ en cartes FUT, le banc, les meilleures cartes, l'inventaire par type, les saisons et coupes, et tout ce que ce joueur a fait |
| **Activité** | Le journal, filtrable par catégorie, avec le bruit masqué par défaut |
| **Économie** | Raretés et notes tirées, meilleurs tirages du serveur, ventes rapides |
| **Serveur** | Ports, composants annoncés, journal brut, et les deux tables ci-dessous |

### « Ce que le jeu demande et n'obtient pas »

La table la plus utile de la page, et pas seulement pour surveiller.

Chaque ligne de la partie Blaze est un composant et une commande sans
gestionnaire — le serveur écrit `unknown_route` chaque fois que le titre essaie
quelque chose qu'il ne sait pas faire. C'est une liste de choses à écrire que
le jeu tient lui-même. `GameManager` (composant 4) y figurant, c'est le mode en
ligne qui demande à exister.

La partie HTTP est la même chose pour les routes FUT répondues 404. Les scans
venus d'Internet en sont écartés : le port d'identité est ouvert sur le VPS et
les scanners le trouvent en quelques heures, avec une forme nouvelle toutes les
heures. La liste des chemins que FIFA 14 demande, elle, est courte et ne change
pas — donc c'est elle qui sert de filtre, dans ce sens-là.

## Comment il sait qui a fait quoi

La plupart des lignes du journal ne portent que `peer`, l'adresse du client.
Seules quelques-unes portent l'identifiant nucleus — l'adoption d'identité, la
lecture du compte — mais elles arrivent tôt dans une session. Parcourir le
journal dans l'ordre en retenant la dernière persona vue à chaque adresse nomme
donc presque toutes les lignes qui suivent. Les trames Blaze portent un numéro
de connexion à la place, que `connected` rattache à une adresse.

Ça se trompe si deux joueurs partagent une adresse publique — même box, même
réseau mobile. C'est le seul cas, et il se voit : deux clubs, une adresse.

## Détails qui ont coûté du temps

- Un `[hidden]` perd contre un `display: grid`. L'écran d'entrée restait
  affiché sous le tableau de bord qu'il venait de laisser passer.
- `listening` s'écrit *avant* `ready`, pas après. Collecter les ports après le
  dernier `ready` n'en trouvait aucun.
- Une barre de répartition dans un `<span>` sans `display` n'a pas de hauteur :
  toutes se dessinaient en filet, pleine largeur.
- La base de cartes porte les noms d'état civil — « C. Ronaldo dos Santos
  Aveiro », « Radamel Falcao García Zarate ». Une carte FUT montre le nom
  court, et c'est le **deuxième mot** dans presque tous les cas : Messi, Neuer,
  Ribéry, Iniesta, Falcao, Piqué, Ronaldo. Prendre les deux derniers mots
  donnait « SANTOS AVEIRO ».
- Les packs contiennent des maillots, des écussons et des consommables, écrits
  avec un `assetId` nul. Les compter comme des cartes de rareté inconnue
  dessinait une barre « ? » au-dessus de toutes les vraies.
