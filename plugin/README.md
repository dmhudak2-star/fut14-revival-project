# Le plugin : squelette et chaîne de génération

Ce dossier contient le **squelette** du plugin Dashlaunch et l'outillage qui le
nourrit en données. `docs/PLUGIN.md` est la spécification ; ceci est le code de
départ et comment le construire.

## Avertissement, à lire en premier

`plugin.c` a été écrit **sans chaîne PowerPC et sans jamais être compilé ni
exécuté**. Tout le reste du dépôt a été vérifié sur la console ou dans les
tests ; ce fichier, non. C'est un point de départ structuré pour quelqu'un qui
a l'environnement de build Xbox 360, pas un plugin qui marche. Chaque endroit
qui a besoin de la vraie API kernel / Dashlaunch porte un `TODO(sdk)`.

Deux limites supplémentaires, importantes :

1. **La table de correctifs de lancement (étage 1) est le cœur fonctionnel,
   pas l'ensemble complet.** Le vrai lanceur
   (`tools/fifa14_early_local_server.py`, ~lignes 195-360) installe aussi des
   stubs de trace/journal dont on n'a pas séparé le nécessaire du
   diagnostique. Le manifeste le marque : `"complete": false`. Un plugin
   construit à partir de ça seul doit être validé contre un lancement patché
   complet avant d'être cru.
2. **`patches.h` est généré pour une IP fixe.** La résolution de nom au
   démarrage (`resolve_and_rewrite` dans `plugin.c`) est un TODO ; tant qu'elle
   n'existe pas, le plugin ne parle qu'à l'adresse pour laquelle le header a
   été généré.

Ce qui est solide : la **forme**. Trois accroches de chargement dans l'ordre,
des écritures gardées qui vérifient les octets d'origine, un APT localisé par
signature. Et les octets/adresses viennent de `patches.h`, généré depuis les
outils qui patchent la vraie console — donc ils ne sont pas inventés ici, même
si ce fichier n'est pas testé.

## La chaîne de génération

Aucun octet n'est tapé à la main, du Python jusqu'au C :

```
outils de patch Python
   │  tools/extract_patch_manifest.py --ip <serveur> --core-port … --identity-port …
   ▼
patches.json          (table complète, paramétrée par l'adresse)
   │  tools/gen_plugin_header.py patches.json --output plugin/patches.h
   ▼
plugin/patches.h      (tableaux d'octets en C)
   │  #include
   ▼
plugin/plugin.c       (la forme ; consomme le header)
```

Le jour où un patcheur bouge une adresse, on régénère et la différence se voit
à la compilation, pas comme un plugin cassé en silence.

```sh
# régénérer pour un serveur donné
tools/extract_patch_manifest.py --ip 203.0.113.10 \
    --core-port 10041 --identity-port 18080 > plugin/patches.json
tools/gen_plugin_header.py plugin/patches.json --output plugin/patches.h
```

`plugin/patches.sample.json` est un exemple généré pour `203.0.113.10`, et
`plugin/patches.h` en découle. Les deux sont versionnés pour que le squelette
compile en l'état (une fois la chaîne SDK en place), mais un déploiement réel
les régénère pour sa propre adresse.

## Ce qu'il reste à faire, par ordre

1. **Remplir les `TODO(sdk)` de `plugin.c`** : les vraies en-têtes (XDK
   `<xtl.h>` ou libxenon), la lecture du timestamp XEX, les notifications de
   chargement de module, le flush de cache instruction.
2. **Compléter l'étage 1** : instrumenter un lancement patché réel, comparer
   avec `patches.json`, et décider quels stubs de trace sont nécessaires. Tant
   que `"complete": false`, cette étape n'est pas faite.
3. **La résolution de nom** (`resolve_and_rewrite`) : lire `fifa14revival.ini`,
   résoudre l'hôte, réécrire l'IP dans le cave `connect_stub` (offsets connus
   du manifeste) et dans les deux chaînes EAS FC.
4. **Compiler, charger via `launch.ini`, vérifier** sur une RGH — la seule
   étape qui a besoin du matériel, et la seule qui dira si tout ça tient.

## Chaîne de build

Deux options, voir `docs/PLUGIN.md` pour le détail ARM/x86 :

- **XDK + Visual Studio 2010** (x86 Windows) — officiel, lourd.
- **xenon-gcc / devkitxenon** (cross-compilateur GCC) — plus léger, plus
  portable, sans debugger. Recommandé pour un plugin chargé par `launch.ini`.

Sur un Mac Apple Silicon, le plus fiable est un x86 Linux (le VPS qui héberge
le serveur convient) plutôt qu'une VM émulée.
