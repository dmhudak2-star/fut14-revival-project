# L'opération 0xDF, désassemblée

`docs/STATUS.md` s'arrête, au 6 août, sur cette phrase : « l'opération `0xDF`
soumise par l'emplacement `+0x4C` de l'objet `0x8908CA10` revient. Rien ne suit,
et aucune route HTTP ni trame Blaze correspondante n'atteint le serveur local. »

Elle est suivie jusqu'au bout ici. **Elle n'est plus la frontière.**

## La chaîne

`FirstTimeInit`, emplacement `+0x08` de la vtable du service FUT, dans CardsDLL :

```
0x8908D3D0  mflr    r12
0x8908D3E0  bl      0x89185500       ; le gestionnaire
0x8908D3EC  bl      0x8908CA10       ; obtenir l'objet requête -> r1+0x50
0x8908D3F0  li      r4, 0xDF         ; opération 223
0x8908D3FC  lwz     r11, 0(r31)      ; vtable de la requête
0x8908D400  lwz     r11, 0x4C(r11)
0x8908D408  bctrl                    ; submit(r3 = requête, r4 = 0xDF)
0x8908D414  lwz     r11, 4(r11)      ; release
```

`+0x4C` de la vtable `0x8218A330` vaut `0x83593B28`, dans `default.xex` :

```
0x83593B28  mr      r31, r4          ; garder l'identifiant d'opération
0x83593B3C  bl      0x82762398       ; r3 = *(0x83D922B8)   -- un singleton
0x83593B40  bl      0x82521208       ; r3 = singleton->[0x34]
0x83593B44  mr      r4, r31
0x83593B48  bl      0x8279ED50
```

`0x82762398` et `0x82521208` font chacune deux instructions : un chargement
global et un déréférencement de champ. `0x8279ED50` en fait quatre :

```
0x8279ED5C  addi    r5, r1, 0x50     ; un paramètre de sortie
0x8279ED60  lwz     r3, 0(r3)
0x8279ED64  bl      0x8278F228
```

Et `0x8278F228` est le répartiteur :

```
0x8278F228  cmpwi   r4, 0xA8         ; deux opérations traitées à part
0x8278F23C  cmpwi   r4, 0x46
...
0x8278F274  lwz     r11, 0x10(r3)    ; l'index du gestionnaire
0x8278F278  cmpwi   r11, -1
0x8278F27C  beqlr                    ; -1 -> retour immédiat, rien n'est fait
0x8278F280  slwi    r11, r11, 2
0x8278F284  lwzx    r10, r11, r3
0x8278F288  cmplwi  r10, 0
0x8278F28C  beqlr                    ; pas de gestionnaire -> pareil
0x8278F294  lwz     r11, 0(r3)
0x8278F298  lwz     r11, 4(r11)
0x8278F2A0  bctr                     ; sinon, on transmet
```

`0xDF` n'est pas un cas particulier : il prend le chemin générique. Et ce chemin
a **deux sorties silencieuses** — un index à -1, ou un gestionnaire nul. Une
opération qui « revient sans que rien ne suive » ressemble exactement à ça.

## Ce que dit la console, en vrai

L'adresse est calculable, donc la vérification l'est aussi. Console dans FUT,
12 août :

```
*(0x83D922B8)   = 0xB5D2A230     le singleton
        +0x34   = 0xB5C3C500
     *(+0x34)   = 0xB5C3EBD0     le r3 que voit 0x8278F228
        +0x10   = 1              l'index du gestionnaire
        vtable  = 0xB62352B0
```

**L'index vaut 1, pas -1.** Aucune des deux sorties silencieuses n'est prise :
la requête est transmise à un gestionnaire vivant.

Ce qui est cohérent avec tout le reste depuis : FUT se connecte, le club
s'affiche, l'équipe se dessine. La note du 6 août décrit un état que le serveur
a depuis dépassé. `0xDF` n'est plus un mur ; elle passe.

## Ce que ça corrige

L'idée que le silence de l'Équipe de la semaine était « probablement le même
appel » que `0xDF` est **fausse**, et elle était de moi. `0xDF` appartient à
`FirstTimeInit`, qui réussit maintenant. Le silence de l'écran TOTW est un autre
appel, qu'il reste à trouver — voir `docs/TOTW_CHALLENGE.md`, où la mesure qui
compte est que l'écran ne redemande rien quand on l'ouvre.

## L'outil

`tools/ppc_disasm.py` désassemble un vidage mémoire : PowerPC, gros-boutien,
32 bits. Jusqu'ici chaque instruction lue dans ce dépôt l'avait été à la main
dans un vidage hexadécimal, ce qui va pour un branchement de six octets et pas
du tout pour suivre une chaîne d'appels sur cinq niveaux.

```
tools/xbdm_dump_range.py IP 0x83593A00 0x600 work/submit.bin
tools/ppc_disasm.py work/submit.bin 0x83593A00 --from 0x83593B28 --count 20
```
