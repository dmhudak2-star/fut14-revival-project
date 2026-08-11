# Patcher le TU3 sur le disque plutôt qu'en mémoire

Le patch `helperFunctions` est aujourd'hui appliqué à chaud, et il doit être
*surveillé* : le titre recharge l'APT plus d'une fois, donc un patch qui se
vérifie au lancement se relit `original` une minute plus tard. Le mettre dans
le fichier supprimerait la surveillance, la course, et « le patch n'est pas
passé ».

L'outillage existe : `tools/fifa14_xbox_tu3_helperfunctions_patch.py` patche
l'enregistrement 2218 de `patch.big`, et `tools/stfs_inject_rehash.py` le
réinjecte dans le paquet STFS en reconstruisant l'arbre SHA-1. La signature RSA
de Microsoft est conservée telle quelle — une RGH ne la vérifie pas.

## Ce qui a été établi le 12 août 2026

Les deux paquets ont été tirés de la console et ouverts :

```
Hdd:\Content\0000000000000000\454109C3\000B0000\
    tu00000003_00000000                  157 052 928 o
    tu00000003_00000000.codex-patched    157 052 928 o
```

`patch.big` fait 0x7AA273E = 128 591 678 octets dans les deux, et l'original se
vérifie intégralement contre les empreintes que le patcheur exige :

```
patch.bh   sha256 08eb5207bc124e82db73…   ✔ ORIGINAL_BH_SHA256
patch.big  sha256 d0486e06d03ecaef3916…   ✔ ORIGINAL_BIG_SHA256
décodé     sha256 d6ffa69d851211a2bcb1…   ✔ ORIGINAL_DECODED_SHA256
APT        « Apt Data: », 0x6E0C octets, les trois branches à l'état d'origine
```

### Le TU déjà patché sur la console est illisible

L'index du `.codex-patched` annonce bien un enregistrement réécrit — 17 728
octets au lieu de 17 942 — donc le patcheur a bien tourné un jour. Mais son
contenu **ne se décode pas** :

```
Unsupported LZX block type 0
```

La cause est dans notre propre encodeur. Une trame LZX transporte au plus 32 Kio
de sortie ; `encode_container` ne contrôlait que le champ de longueur sur 16
bits, et a donc écrit les 54 048 octets de la ressource en **une seule trame
déclarant 54 048 octets**. Le décodeur sort de la trame et lit les octets
suivants comme un en-tête de bloc. Le décodeur du jeu ferait exactement pareil.

Ce fichier était posé à côté de l'original, prêt à être installé. Il ne doit
pas l'être.

`encode_container` refuse désormais toute charge dépassant une trame, avec un
test qui le fige.

### Ce qui bloque la régénération

Le patcheur relancé aujourd'hui, avec le décodeur et l'encodeur du dépôt :

```
Error: patched payload 18843 exceeds slot 17984
```

L'emplacement fait 0x4640 = 17 984 octets. EA compresse ces 54 048 octets en
17 942 ; notre encodeur en produit 18 843, soit **5 % de trop**. Et ce chiffre
est obtenu avec la trame unique erronée : un découpage correct en deux trames
n'améliorera pas le taux.

Il manque donc deux choses, dans cet ordre :

1. **l'écriture multi-trames** — découper à 32 Kio en conservant l'état du
   dictionnaire d'une trame à l'autre, ce que le décodeur sait déjà lire
   (`LzxStream` porte son état entre les trames d'un même bloc)
2. **environ 5 % de taux en plus** — recherche de correspondances plus longue,
   choix paresseux ; l'original utilise des blocs à décalage aligné, ce que
   l'encodeur émet déjà

Tant que le deuxième point n'est pas réglé, l'enregistrement patché n'entre pas
dans son emplacement et le patch statique est hors de portée. La surveillance à
chaud reste la voie qui marche, et elle marche : 27 secondes, sans intervention.

Déplacer l'enregistrement ailleurs dans `patch.big` pour lui donner plus de
place n'est pas une porte de sortie : `stfs_inject_rehash` exige un fichier de
taille identique, donc il faudrait de l'espace libre déjà présent dans
l'archive, et rien ne dit qu'il y en ait.

## Où sont les fichiers

Tirés sur le Mac, hors dépôt (`work/` est ignoré) :

```
work/tu3/tu3-original.stfs           le paquet retail, intact
work/tu3/tu3-codex-patched.stfs      le paquet illisible, conservé comme preuve
work/tu3/original/patch.big          extrait, vérifié
work/tu3/original-patch.bh/patch.bh  extrait, vérifié
```

C'est la première sauvegarde de l'original ailleurs que sur la console.
