# Piloter la console sans personne devant

`tools/fut_drive.py` est ce qui permet d'essayer quelque chose dans FUT et de
voir ce que ça donne, sans attendre qu'un humain appuie sur un bouton.

Trois sens, et il a fallu les trouver dans cet ordre.

## 1. Le journal est un oracle d'écran

Chaque écran de FUT a une signature de requêtes qui n'appartient qu'à lui : le
hub demande `clientdata/totw` puis `clubUser`, la boutique demande
`storepackdescriptions`, une coupe demande `tournament/teams` puis
`tournament/user/<id>`. Regarder ce qui arrive dit où le titre se trouve, sans
image — et le dit dans les mots du client, ce qui est exactement ce qu'il faut
pour répondre à une question sur un écran pas encore implémenté.

`Console.wait_for()`, `settled()` et `where()` sont là-dessus.

## 2. La capture d'écran marche

`docs/AUTOMATIC_PATCH.md` note que la capture XBDM « n'est pas fiable sur cette
console », et le navigateur d'écran qui en dépendait a été retiré du chemin de
lancement pour cette raison.

**Elle a répondu à chaque appel le 12 août 2026** — 847×480, format
0x18280186, une trentaine de captures d'affilée. Ce qui n'allait pas avant ne va
plus mal maintenant. C'est ce qui a transformé un pilote aveugle en pilote qui
voit, et c'est ce qui a permis de comprendre, en une image, pourquoi rien ne
répondait.

Le journal reste le repli, et reste la seule chose qui puisse répondre à « qu'a
demandé le client ».

## 3. La manette doit rester *branchée*

L'action `press` de `xbox360_virtual_input.py` arme la boîte aux lettres pour
quelques images puis la remet à zéro. Une boîte à zéro repasse la main à la
vraie manette — et à quatre heures du matin il n'y en a aucune d'allumée. Le
titre voyait donc une manette apparaître un tiers de seconde en tenant un
bouton, puis disparaître. Il n'en tenait aucun compte.

`Console.press()` maintient zéro bouton entre les impulsions. C'est ce qui a
fait passer la première pression. Le compteur d'images le prouve : il descend de
60 par seconde, donc le titre interroge bien le point d'accroche.

## Les boîtes système : plus hors de portée (13 août 2026)

Ce qui suit était vrai et ne l'est plus. Le sélecteur de périphérique de
stockage **se referme sous la manette virtuelle** : un seul A, `frames=30`,
et l'écran suivant est l'avertissement de sauvegarde automatique, qu'un
deuxième A referme aussi. La séquence complète depuis un lancement à froid,
sans personne devant :

    A       (drapeau) choix de la langue
    START   écran-titre « APPUYEZ SUR START »
    A       « Choisir périph. » -> Disque dur, déjà surligné
    A       avertissement de sauvegarde automatique
            -> menu principal FIFA, ULTIMATE TEAM surligné

Ce qui a changé entre les deux constats n'est pas établi. Ce qui l'est, c'est
que `Console.press()` maintient zéro bouton entre les impulsions depuis, et
que c'est exactement la correction qui avait fait passer la première pression
dans un menu du jeu. La conclusion « XAM lit la manette par un autre chemin »
reposait sur des pressions qui ne tenaient pas — donc sur rien.

Le paragraphe d'origine est gardé ci-dessous : le conseil console-side reste
le meilleur si le sélecteur redevenait un obstacle.

## ~~Ce qui reste hors de portée : les boîtes de dialogue système~~

À chaque démarrage à froid, FIFA affiche le sélecteur de périphérique de
stockage du 360 — « Choisir périph. », disque dur ou clé USB. Tant qu'on n'a
pas répondu, le jeu est derrière un modal et **rien n'avance**.

La manette virtuelle ne peut pas le refermer. Vérifié : le compteur d'images
descend, donc le point d'accroche est bien appelé, mais deux DOWN ne déplacent
pas la sélection et A ne valide pas. Cette boîte est dessinée par XAM et lit la
manette par un autre chemin que `XamInputGetState`.

C'est aussi un vrai obstacle pour l'objectif « ça doit marcher tout seul » : ce
n'est pas un menu du jeu, c'est un dialogue système, et il apparaît à chaque
lancement à froid.

**La solution est côté console, une fois pour toutes** : Paramètres → Système →
Stockage, sélectionner le disque dur et le définir comme périphérique par
défaut. Le sélecteur ne s'affiche que parce qu'il y a deux périphériques et
aucun par défaut. Une manette réelle suffit, une seule fois.

## Utilisation

```python
import sys; sys.path.insert(0, "tools")
from fut_drive import Console

with Console() as console:
    console.take_pad()
    console.press("A")
    console.shot("apres-a")             # work/apres-a.png
    print(console.where(console.settled()))
```

En ligne de commande :

```
tools/fut_drive.py watch --seconds 20     # ce qui a été demandé, et par quel écran
tools/fut_drive.py where                  # juste l'écran
tools/fut_drive.py press A
tools/fut_drive.py recover                # gelé ou mort -> titre relancé et patché
```

`recover()` distingue les deux états qui se ressemblent vus d'ici : une façade
gelée, où XBDM répond encore, d'un titre mort, où il ne répond plus. Dans le
premier cas il redémarre par `magicboot`, attend le dashboard et relance
`tools/fut.sh`. Deux gels dans une soirée ont chacun coûté un aller-retour avec
un humain ; c'est ce que ça remplace.

## `magicboot` : une seule forme est utilisable

Trois formes ont été essayées le 12 août.

| forme | résultat |
|---|---|
| `magicboot` | la bonne : reboot vers le dashboard |
| `magicboot title="…FIFA 14\\default.xex"` | reboote **et relance FIFA**, à l'intro, sans aucun correctif de lancement. Le titre ne peut plus jamais atteindre le serveur, et `fut.sh` refuse de relancer par-dessus. |
| `magicboot cold` | **sort la console du réseau.** Vingt minutes plus tard le port 730 refusait toujours et l'entrée ARP était incomplète : il a fallu le bouton d'alimentation. |

`magicboot cold` ne doit jamais partir d'ici : rien de ce côté ne peut le
rattraper. Le commentaire de `tools/fut.sh` — « un magicboot par-dessus un titre
qui tourne reboote vers le dashboard » — décrit la première forme, pas la
deuxième.
