# Audit performance DroneGS GSTile — 24 août 2026

## Décision et périmètre

Cet audit couvre le producteur `gaussian_tiles` et le viewer GSTile PlayCanvas.
L’objectif est de réduire le temps de construction, le temps au premier rendu,
les pauses de transition LOD et le temps de frame sans supprimer de Gaussians,
réduire les degrés SH, diminuer la résolution ou modifier silencieusement le
profil scientifique.

Les optimisations sont séparées en deux catégories :

- **plateforme exacte** : I/O, copies, scheduling, cache, upload, tri et
  instrumentation, avec identité de bundle ou de coefficients exigée ;
- **scientifique** : proxy, compression avec pertes, pruning ou changement de
  rasterisation, avec profil nommé, corpus figé et seuils PSNR/SSIM/LPIPS.

## Baseline reproductible

- commit de référence : `68a251d1faef151ae803e6de1cadc68eb15ada03` ;
- branche de travail : `codex/audit-tiler-renderer-performance` ;
- machine : Intel Core i9-13900H, 20 CPU logiques, RTX 4070 Laptop 8 188 MiB,
  pilote 610.62, WSL2 Linux 5.15 ;
- Python 3.12.3, NumPy 2.4.6, Node 24.14.0, Vitest 4.1.10 ;
- fixture tiler : 500 000 splats, 148 001 902 octets,
  SHA-256 `785aa696b3f5ddca9af4e42059f041defbdb963ecb06adcccc1cd5b67d7d6a68` ;
- artefacts conservés : `/tmp/droneai-gstile-perf-68a251d-phase1`.

Le benchmark tiler exécute trois paires croisées base/optimisé. Les six sorties
ont le même bundle ID
`sha256:1323eac62eb30e2596a90670046cee528f1fbebe6bbdc24dece4a591f7524339`,
8 feuilles et 48 000 256 octets de packs.

| Mesure | Base médiane | Optimisé médian | Variation |
|---|---:|---:|---:|
| Temps mur tiler | 2,312 s | 1,631 s | **-29,4 %** |
| CPU utilisateur | 3,250 s | 2,628 s | **-19,1 %** |

Le benchmark Q96 renderer porte sur un pack de 65 536 splats :

| Chemin | Moyenne | Débit | Variation |
|---|---:|---:|---:|
| allouer, décoder, recopier | 13,602 ms | 73,52/s | référence |
| décoder directement dans le cut final | 9,334 ms | 107,14/s | **1,46×** |

Le second chemin évite aussi environ 19 MiB d'allocation transitoire par pack.

### Qualification navigateur disponible

La gate Playwright WebGPU a chargé le bundle V4 synthétique de 500 000 splats
via 8 requêtes range et a atteint un cut complet de 8 nœuds sous SwiftShader,
sans erreur de contrat, SHA, décodage ou upload. Elle échoue ensuite sur
l'assertion que le pan doit modifier l'image : le fixture synthétique est
quasi unidimensionnel et la capture du canvas reste vide. Verdict :
**intégration partielle passée, acceptation visuelle non concluante**. La
capture et le contexte d'erreur sont conservés sous
`app4-dashboard/frontend/test-results/`; cette exécution ne remplace pas la
qualification RTX 4070 sur le bundle Saint-Étienne représentatif.

### Qualification réelle Saint-Étienne sur RTX 4070

Les bundles sont lus directement depuis `I:` sur BIGZEN et servis avec Range
HTTP/CORS via un tunnel localhost vers Chrome Windows. Aucun pack n'est copié
sur la machine de rendu.

| Bundle | Splats source | Filtrés | Nœuds/packs | Packs | Disque |
|---|---:|---:|---:|---:|---:|
| v4b Cesium r1 | 49 408 067 | 0 | 2 699 | 6 865 054 336 o | 6 872 155 349 o |
| v4c filtered r1 | 49 392 943 | 15 124 | 2 679 | 6 847 873 152 o | 6 854 920 514 o |

Le v4c complet a validé WebGPU, SHA, Q96, upload et rendu visible de la façade.
La contrainte produit est désormais explicite : **seul le mode monolithique
`merged` reproduit la qualité du PLY original**. Le mode `incremental` a réduit
le temps d'un raffinement complet de 25,081 s à 21,942 s dans un essai, mais a
produit de gros splats flous après un changement de cut ; il est rejeté comme
chemin de production.

Sur un cut `merged` comparable de 7,4 M splats et 356 nœuds, l'optimisation de
décolumnarisation réduit le commit observé de 10 892 à 10 234 ms (-6,0 %). Le
temps total reste dans le bruit de mesure, 25 081 contre 25 021 ms, car le
chargement/décodage représente encore environ 14,1 s. Le benchmark croisé de
conversion de 65 536 splats mesure 9,796 ms avant et 6,876 ms après, soit
**1,42×**, avec les mêmes tableaux de propriétés vérifiés par tests. La mesure
bout en bout est un essai apparié et doit être répétée avant d'être utilisée
comme engagement de performance.

La phase suivante supprime entièrement le cut row-major transitoire pour
`merged` : chaque record Q96 est décodé directement dans les 59 colonnes PLY
et les quatre streams RGBA32F d'opacité finaux. Les tests comparent chaque
`float32`, l'ordre et le nom des propriétés ainsi que les 16 coefficients
d'opacité au chemin de référence. Le stockage CPU passe de 604 octets/splat au
pic (304 row-major + 300 finaux) à 300 octets/splat, soit environ **2,28 Go
évités à 7,5 M de splats**. Sur 65 536 splats, le pipeline CPU complet passe de
19,144 à 10,076 ms, soit **1,90×**.

Sur le v4c réel, trois rechargements Chrome RTX 4070 du même cut donnent :

| Passage | Total LOD | Load/décodage | Commit GPU |
|---|---:|---:|---:|
| 1 | 21 956 ms | 14 510 ms | 7 389 ms |
| 2 | 19 715 ms | 12 788 ms | 6 830 ms |
| 3 | 20 176 ms | 12 688 ms | 7 421 ms |
| **Médiane** | **20 176 ms** | **12 788 ms** | **7 389 ms** |

Face au dernier passage `merged` à 25 021 ms / commit 10 234 ms, la médiane
est indicativement meilleure de 19,4 % au total et 27,8 % au commit. La base
n'ayant qu'un essai apparié, ce delta reste une qualification d'ingénierie et
non un intervalle statistique. Le chemin `merged` de référence avait été
confirmé visuellement conforme au PLY original. Cette variante conserve les
mêmes coefficients bit-à-bit et sa comparaison visuelle humaine dans Chrome a
également été déclarée conforme au PLY original. La gate perceptuelle est donc
passée ; le mode reste strictement `merged`.

Deux passages instrumentés supplémentaires décomposent le chemin critique :

| Passage | Load mur | Fetch service Σ | SHA-256 Σ | Q96 CPU Σ | Commit | Ressource PlayCanvas | Streams | Scène |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| froid instrumenté | 12 034 ms | 1 910 781 ms | 595 ms | 3 250 ms | 6 874 ms | 6 572 ms | 301 ms | < 1 ms |
| chaud instrumenté | 14 087 ms | 2 188 850 ms | 582 ms | 3 382 ms | 7 068 ms | 6 715 ms | 352 ms | < 1 ms |

`Fetch service Σ` additionne les durées de 356 requêtes concurrentes et ne
doit pas être comparé au temps mur. Cette mesure localise néanmoins le commit :
**95 % environ est consacré à reconstruire et repacker la ressource GSplat**,
pas à l'attacher à la scène. L'arène persistante doit donc conserver les
textures packées des nœuds réutilisés ; une optimisation préalable du seul
work-buffer ciblerait le mauvais segment.

## Bugs et défauts confirmés

| ID | Priorité | Constat et preuve | Impact | État |
|---|---|---|---|---|
| B1 | P0 | Le mode fidèle `merged` rechargeait et redécodait tous les nœuds du nouveau cut ; la seule ressource résidente était `__merged__`, donc les nœuds signalés comme réutilisés ne l'étaient pas côté CPU/GPU. | Pause proportionnelle au cut complet, même pour une petite variation de caméra. | **Corrigé** : arène GPU persistante, offsets stables et allocation multi-spans des seuls nouveaux nœuds. |
| B2 | P0 | SHA, décodage Q96 et création de ressources s'exécutent sur le thread UI. Le `setTimeout(0)` permet l'annulation entre packs mais chaque pack reste une longue tâche synchrone. | Jank d'entrée et frames manquées pendant le raffinement. | Partiellement réduit : pipeline columnar direct 1,90× et 2,28 Go transitoires évités ; Worker/WASM à faire. |
| B3 | P0 | L'opacité directionnelle dépend de la caméra dans un `workBufferModifier`. L'audit initial supposait que `#updateOpacityCameraUniform(false)` laissait le work-buffer périmé jusqu'au debounce. | Risque supposé d'opacité périmée et de réupload global coûteux. | **Diagnostic corrigé** : `colorUpdateAngle = 0` déclenche déjà le pass GPU color-only exact à chaque translation. Deux invalidations globales redondantes au debounce et au commit ont été supprimées. |
| B4 | P1 | Le debounce LOD fixe ajoutait 650 ms après le dernier événement d'interaction. | Impression de lag avant tout fetch/décodage. | **Corrigé : 120 ms configurable**, validation stricte 0–5 000 ms. |
| B5 | P1 | Le préflight LOD réserve `N × 96 × maximum_depth`, soit une borne adversariale sans rapport avec l'arbre spatial habituel. | Refus prématuré de builds valides à grande échelle. | Confirmé ; modèle mesuré + garde dynamique à implémenter. |
| B6 | P1 | `_bounds` créait une matrice float64 `N×3` à chaque chunk et niveau ; chaque pack était relu pour SHA et rescanné pour CRC après encodage. | Allocation, pression cache et I/O `O(N log N)` amplifiées. | **Corrigé**, bundle bit-identique, tiler médian -29,4 %. |
| B7 | P1 | La partition midpoint relit et réécrit chaque splat à chaque profondeur. | Trafic temporaire `O(N log N)` et faible scalabilité 100M. | Confirmé ; external-sort Morton à faire. |
| B8 | P2 | Une erreur réseau transitoire termine le raffinement ; le scheduler déduplique, annule et met en cache, mais n'a pas de retry borné avec classification. | Cut conservé mais LOD en erreur jusqu'à une nouvelle interaction. | Confirmé ; retry 408/429/5xx et erreurs réseau à ajouter. |
| B9 | P2 | Les métriques agrégeaient `lodLoadMs` et `lodCommitMs` sans séparer fetch, SHA, decode, conversion et upload. | Les régressions se diagnostiquaient par capture manuelle. | **Partiellement corrigé** : fetch service, SHA, Q96, ressource, streams et scène exposés ; attente queue, tri, raster et long tasks restent à ajouter. |

## Pistes classées par efficacité supposée

L'ordre combine gain attendu, fréquence du chemin et capacité à préserver la
qualité. Les multiplicateurs issus de papiers ne sont pas des promesses pour
DroneAI ; ils servent à prioriser les expériences.

### 1. Arène GPU persistante et patch incrémental des cuts — impact très élevé

Remplacer la destruction/recréation de la ressource fusionnée par un conteneur
GPU préalloué : plages libres, table stable `nodeId → offset`, uploads des seuls
nœuds ajoutés, invalidation des seuls nœuds retirés, compactage différé. La
sélection reste REPLACE et branch-atomic. C'est la correction directe de B1 et
la plus forte réduction attendue des pauses de commit.

Acceptance : coefficients et ordre du cut identiques au chemin fusionné,
absence de stale allocation, p95 commit et octets uploadés rapportés, mémoire
VRAM bornée, aucune device loss sur la RTX 4070.

État : **livré et mesuré**. L'arène `GSplatContainer` persistante, les
intervalles non-octree et l'allocation multi-spans conservent les nœuds communs
sans les redécoder, les repacker ni les déplacer. Sur la transition
Saint-Étienne qui forçait une reconstruction totale, le temps passe de
12,912 s à 1,764 s (7,3×).

### 2. Pipeline Worker/WASM pour SHA + Q96 + préparation de streams — impact très élevé

Déplacer validation et décodage hors du thread UI. Le Worker doit posséder le
cache brut ou recevoir des buffers transférables sans détacher le cache du
main thread. La sortie doit être directement column-major dans le format
d'upload PlayCanvas afin d'éviter la seconde décolumnarisation de 58 canaux.
SIMD WASM est à comparer au TypeScript JIT ; il ne sera retenu que mesuré.

Acceptance : aucune tâche UI > 50 ms pendant un parcours froid/chaud, hash et
coefficients identiques, annulation générationnelle, mémoire de file bornée.

État : **Q96 et préparation des streams livrés et mesurés ; SHA/WASM et longues
tâches restent ouverts**. Le pool borné à quatre Workers décode maintenant les
96 octets Q96 directement vers centres, bornes et toutes les textures finales
PlayCanvas : transformations, couleur, SH3 et opacité directionnelle. Chaque
Worker reçoit 96 octets/splat et renvoie 172 octets/splat transférés ; aucune
copie d'entrée n'est créée pour les tâches en attente. Sur les quatre plus gros
nœuds Saint-Étienne, l'ensemble de travail actif nominal est borné à 66,9 Mio
(24,0 Mio d'entrées et 43,0 Mio de sorties), soit environ 34,0 Mio de plus que
le Worker transformations seul. SHA, le prototype SIMD WASM et la mesure
automatisée des longues tâches restent à déplacer, comparer ou instrumenter.

### 3. Tri/culling/raster WebGPU moderne — impact très élevé sur le frame time

Évaluer dans cet ordre : culling conservatif avant tri, quad sizing dépendant
de l'opacité, radix sort portable/wait-free, puis raster macro-tile/fine-tile.
WebSplatter rapporte 1,2× à 4,5× face aux viewers web testés ; HiGS rapporte
1,8–2,2× face aux rasterizers modernes et jusqu'à 15,8× face au 3DGS original,
avec compositing exact. L'intégration doit rester derrière l'adapter afin de ne
pas coupler le contrat GSTile à un fork moteur.

Acceptance : images exact-leaf comparées aux références, p50/p95 GPU à 1080p
et 4K, overdraw, splats triés, fragments, VRAM et compatibilité multi-GPU.

État : recherche confirmée, prototype non commencé.

### 4. Tiler external-sort Morton, feuilles contiguës, arbre bottom-up — impact très élevé à l'échelle

Émettre une paire `(morton63, source_id, record)` par splat, trier en externe
par runs bornés, fusionner les runs, écrire chaque feuille exacte une fois,
puis construire les nœuds/proxies bottom-up. Un flux trié Morton permet de
construire l'octree en un sweep ; la spécification Streamed SOG utilise aussi
des runs Morton pour la localité.

Acceptance : déterminisme sur deux builds, exact-pack fingerprint identique ou
migration de profil explicitée, RSS bornée, octets temporaires et temps par
phase, cancellation/reprise et test réel ≥ 49M.

État : design confirmé ; les micro-optimisations sans risque sont livrées en
attendant cette réécriture R3.

### 5. Proxies supervisés par rendu — impact élevé sur qualité par splat

Optimiser position, covariance, couleur SH et opacité SH des proxies contre les
enfants exacts depuis un corpus de caméras représentatives. Stocker une borne
d'erreur mesurée par nœud et utiliser cette erreur pour le SSE. H3DGS, LODGE et
A LoD of Gaussians confirment l'intérêt d'une hiérarchie entraînée/affinée et
du cache out-of-core.

Acceptance : seuils PSNR/SSIM/LPIPS gelés avant l'expérience, validation
held-out, conservation des détails façade et opacité directionnelle, profil
GSTile scientifique distinct et réversible vers les feuilles exactes.

État : protocole à définir avec le corpus DroneAI ; aucune approximation mise
en production par cet audit.

### 6. Transitions progressives sans couture — impact élevé sur le lag perçu

Ajouter skirts/clusters de frontière partagés ou cross-fade en épaisseur
optique entre parent et enfants complets. Cela autorise des commits de branches
prêtes au lieu d'attendre le cut entier, sans checkerboard grossier/exact.
LODGE emploie un blending d'opacité entre clusters.

Acceptance : aucun trou ni double assombrissement, capture vidéo de parcours,
diff temporelle et frame pacing, comportement exact après transition.

État : recherche confirmée ; dépend du profil proxy et de l'arène GPU.

### 7. Streaming prédictif et cache multi-étages — impact élevé sur cold/warm navigation

Précharger un cut à horizon court depuis vitesse caméra, coalescer les ranges
adjacentes lorsque plusieurs tiles partagent un pack, conserver un cooldown
cache borné, prioriser couverture avant finesse et exploiter HTTP/2/3. Ne pas
transférer plus de données que le budget décodable/uploadable.

Acceptance : time-to-first-complete-cut, hit ratio, octets inutiles après
annulation, p95 réseau/décodage, limites RAM, tests de caméra oscillante.

État : cache LRU brut, déduplication in-flight et grâce 300 ms déjà présents ;
préfetch/coalescence absents.

### 8. Agrégation de packs et durabilité batchée — impact moyen à élevé sur le tiler et HTTP

Regrouper plusieurs nœuds dans des packs alignés pour amortir open/fsync et
requêtes HTTP, tout en conservant ranges, SHA du pack et CRC des payloads.
Évaluer `fdatasync` par lot avant le rename final sans affaiblir le contrat de
publication atomique.

Acceptance : crash-injection, validation après interruption, nombre de fsync,
requêtes et débit disque ; aucune publication partielle visible.

État : non commencé. Le reread SHA et le second CRC inutiles sont supprimés.

### 9. Parallélisme tiler borné — impact moyen

Pipeline à files bornées : lecture/sort, encodage Q96, SHA et écriture de packs.
Les packs sont indépendants, mais l'ordre du manifest et les hashes doivent
rester déterministes. Pour les proxies adaptatifs, paralléliser par sous-arbre
après la construction des feuilles.

Acceptance : speedup par nombre de workers, RSS/disque bornés, mêmes hashes,
annulation propre et absence de sursouscription BLAS/NumPy.

État : non commencé.

### 10. Opacité directionnelle évaluée par frame sans réupload global — impact moyen à élevé

Déplacer la dépendance caméra du work-buffer vers le vertex/compute shader de
rendu, ou maintenir un stream d'alpha GPU ciblé. Cela corrige B3 et évite une
réécriture de millions de splats après chaque mouvement.

Acceptance : parité directionnelle sur directions figées, absence d'opacité
périmée pendant le geste, coût GPU isolé et frame p95 inférieur au chemin de
réupload.

État : **diagnostic corrigé et invalidations redondantes retirées**. Le seuil
`colorUpdateAngle = 0` de PlayCanvas déclenche déjà le pass color-only exact à
chaque translation ; aucune opacité périmée n'a été observée.

### 11. Budget adaptatif piloté par télémétrie, sans baisse de qualité stable — impact moyen

Utiliser le budget maximal compatible avec le frame target et la VRAM, avec
hystérésis. La qualité finale stable reste le cut le plus strict qui tient en
mémoire ; seule la vitesse de convergence peut varier. La réduction de DPR ou
de SH est explicitement exclue du profil exact.

État : dépend de la télémétrie GPU fiable.

### 12. Retry réseau borné et reprise de raffinement — fiabilité élevée, performance indirecte

Retry jitteré uniquement pour erreurs réseau, 408, 429 et 5xx, respect de
`Retry-After`, jamais pour corruption, 4xx permanents ou range invalide.
Conserver le cut complet précédent jusqu'au succès.

État : non commencé.

## Implémentations réalisées dans cette phase

1. `_bounds` réduit directement les trois champs sans matrice float64 `N×3`.
2. Les préfixes PLY/work sont copiés en une opération contiguë au lieu d'une
   boucle sur 74 propriétés.
3. SHA-256 est calculé sur les octets encodés déjà en mémoire et le CRC du
   header est réutilisé ; aucun reread de pack.
4. Le mode fusionné décode directement chaque pack dans sa plage du cut final.
5. Le debounce LOD est configurable et vaut 120 ms par défaut au lieu de 650.
6. Deux harness reproductibles sont ajoutés : tiler JSON et benchmark Vitest
   Q96.
7. La conversion row-major vers les 58 colonnes PlayCanvas parcourt chaque
   champ séquentiellement au lieu de 58 scans stridés ; benchmark 1,42× et
   commit réel `merged` observé -6,0 % sur 7,4 M splats.
8. Le chemin `merged` décode Q96 directement dans les 59 propriétés PLY et les
   quatre streams d'opacité finaux, sans `sourceId` inutilisé par le renderer :
   pipeline 1,90×, pic CPU -50,3 % et médiane réelle 20,176 s sur trois runs.
9. La télémétrie sépare fetch service, SHA-256, Q96, construction ressource,
   streams et scène dans le contrat, le HUD et le snapshot debug. Elle établit
   que le packing `GSplatResource` représente environ 95 % du commit actuel.
10. Une arène `GSplatContainer` de capacité fixe conserve les offsets des
    nœuds stables, copie les nouveaux streams par commandes GPU row-bounded et
    n'expose qu'une ressource/entité au renderer. Les AABB utilisent exactement
    la formule PlayCanvas `centre ± 2·exp(max(log_scale))`.
11. Le patch PlayCanvas 2.21.4 versionné ajoute des intervalles non-octree
    publics, leur allocation contiguë dans le work-buffer et leur réupload
    complet lorsque les IDs d'octree sont absents. Il est idempotent,
    fail-closed, couvert par cinq tests et appliqué aux builds prod/debug/profilé
    ainsi qu'aux déclarations TypeScript.
12. `GSplatContainer.configureMaterial` est remplacé localement par le chemin
    direct déjà validé de `GSplatResourceBase`. L'indirection de chunk du
    container produisait un pipeline WebGPU valide mais un work-buffer nul avec
    le format SH3 enrichi des streams d'opacité DroneAI.
13. Chaque commit d'arène réinitialise uniquement le manager unifié dérivé et
    publie immédiatement une frame. Cela empêche l'ancien index de lire les
    nouveaux texels lorsque `requestAnimationFrame` est ralenti et supprime une
    frame de latence de transition.
14. Un nœud peut occuper plusieurs spans libres déterministes. Les spans des
    nœuds résidents restent immuables, les nouveaux texels sont copiés
    séquentiellement dans les trous, et les spans adjacents sont coalescés avant
    de configurer le renderer. La fragmentation ne provoque plus de compaction
    ni de reconstruction du cut complet.
15. Les changements de caméra ne marquent plus explicitement tout le
    work-buffer sale au démarrage du debounce puis au commit. PlayCanvas assure
    déjà la mise à jour utile par son pass color-only avec seuil angulaire nul ;
    la formule SH3 et la cadence exacte pendant la translation sont inchangées.
16. Le décodeur Q96 fusionné normalise les quaternions bornés avec une somme
    quadratique directe, déroule le packing fixe des 15 coefficients d'opacité
    et traite les 45 coefficients SH couleur par groupes de trois. Un test
    différentiel compare bit à bit les 59 propriétés et quatre streams sur
    4 096 records pseudo-aléatoires.
17. Le staging fusionné surcharge uniquement `updateTransformData` pour lire
    directement les colonnes GSTile. Il produit les mêmes mots PlayCanvas
    `transformA` RGBA32U et `transformB` RGBA16F sans créer ni muter un `Vec3`,
    `Quat` et itérateur par splat. Les autres ressources et tous les shaders
    conservent le chemin moteur standard.
18. Le packing SH3 natif conserve la lecture colonne→tableau local de
    PlayCanvas, mais calcule le maximum pendant cette lecture et supprime les
    bornages et arrondis devenus redondants avant le packing binaire. Le clamp
    du premier coefficient rouge est volontairement conservé pour reproduire
    le cas asymétrique du moteur mot pour mot.
19. Le chemin `merged` fusionne maintenant déquantification Q96 et packing
    SH3. Les 45 propriétés PLY restent présentes sous forme de marqueurs vides
    pour annoncer les trois bandes à PlayCanvas, tandis que quatre streams
    RGBA32U finaux remplacent les 45 colonnes float32 temporaires.
20. Le même chemin fusionne la conversion des trois couleurs DC et de
    l'opacité logit vers le stream `splatColor` RGBA16F. Les quatre propriétés
    PLY restent présentes comme marqueurs de schéma, mais le constructeur de
    ressource ne fait plus qu'une copie linéaire du stream final.
21. Le chemin `merged` remplace les trois colonnes `x/y/z` par le tableau
    interleavé de centres exigé par le tri PlayCanvas. Le calcul de bornes par
    nœud déjà nécessaire à l'arène alimente aussi `GSplatData`, supprimant les
    scans `getCenters()` et `calcAabb()` redondants sans augmenter le handoff
    de 176 octets/splat.
22. Le packing des transformations utilise les écritures natives
    `Float16Array` lorsqu'elles sont disponibles, avec un fallback PlayCanvas
    exact. Cela remplace sept conversions JavaScript float32→float16 par splat
    sans allocation ni copie supplémentaire.
23. Les quatre streams SH3 déjà packés sont dimensionnés avec le padding exact
    des textures PlayCanvas puis adoptés comme sources initiales. Le chemin
    `merged` supprime ainsi leur copie CPU de 64 octets/splat et le second
    buffer d'environ 454 Mio sur Saint-Étienne.
24. Le stream couleur RGBA16F final suit le même contrat d'adoption atomique.
    Son padding est validé avant toute allocation et une source incohérente
    laisse la ressource intacte. Le chemin `merged` supprime ainsi la copie CPU
    de 8 octets/splat et environ 56,7 Mio de stockage dupliqué sur
    Saint-Étienne.
25. Les quatre streams d'opacité RGBA32F sont également paddés pendant le
    décodage puis installés directement comme `extraStreams`. Comme ils ne sont
    pas encore présents dans la map PlayCanvas, le handoff évite à la fois leur
    copie CPU de 64 octets/splat et l'allocation préalable de quatre textures
    vides.
26. La première ressource fusionnée est créée directement à la capacité de
    l'arène puis promue en conteneur mutable. Les sources CPU des textures sont
    libérées après la première frame soumise, supprimant le second conteneur et
    environ 1,118 Gio de rétention CPU sur Saint-Étienne.
27. Le packer de transformations reçoit un contrat explicite lorsque les
    rotations Q96 sont déjà normalisées. Le chemin générique sûr reste le
    défaut ; le chemin Q96 évite une racine carrée et quatre divisions par
    splat.
28. Un pool de quatre Workers prépare centres, bornes et textures de
    transformations directement depuis les records Q96. Les tâches en attente
    ne dupliquent pas les payloads ; l'admission, l'annulation et le fallback
    synchrone sont bornés et télémétrés.
29. Le même Worker possède désormais tout le décodage Q96 final : couleur
    RGBA16F, quatre streams SH3 RGBA32U et quatre streams d'opacité RGBA32F.
    Le thread UI ne fait plus que des copies contiguës vers le cut préalloué ;
    le fallback synchrone utilise exactement le même décodeur natif.

### Qualification arène GPU persistante — Saint-Étienne v4c

Trois runs Chrome propres, 7 435 345 splats, 356 nœuds, SH3 et opacité
directionnelle :

| run | total LOD | load | commit | état renderer |
|---|---:|---:|---:|---|
| arena16 | 18,906 s | 12,046 s | 6,851 s | complet, 1 ressource |
| arena17 | 19,881 s | 12,899 s | 6,847 s | complet, 1 ressource |
| arena18 | 20,275 s | 13,164 s | 7,016 s | complet, 1 ressource |

Médiane : 19,881 s, soit -1,46 % face à la médiane fusionnée précédente de
20,176 s. Le faible gain initial est cohérent : le passage du cut grossier au
cut final ne réutilise que 292 922 splats et le packing du staging
`GSplatResource` reste dominant (~6,3 s).

Une rotation de caméra a d'abord mis en évidence une fragmentation critique :
le nouveau cut de 7 487 153 splats disposait d'assez d'espace libre total, mais
pas d'un trou contigu assez grand. Le plan mono-span compactait alors toute
l'arène : 0 splat réutilisé, 12,912 s total, 5,022 s load et 7,876 s commit.

Le plan multi-spans conserve exactement les ~6,6 M splats communs :

| transition | total LOD | load | commit | réutilisés |
|---|---:|---:|---:|---:|
| rotation mono-span | 12,912 s | 5,022 s | 7,876 s | 0 |
| rotation multi-spans, premier passage | **1,764 s** | **0,781 s** | **0,966 s** | 6,6 M |
| retour | 0,461 s | 0,118 s | 0,340 s | 7,2 M |
| rotation répétée | 1,410 s | 0,487 s | 0,915 s | 6,6 M |

Le premier passage comparable est **7,3× plus rapide au total**, 6,4× sur le
load et 8,2× sur le commit. Le run initial reste stable à 19,799 s pour
7 435 345 splats : l'optimisation vise bien les transitions et ne dégrade pas
le cold load. Huit tests couvrent texture copies, bornes, recouvrement,
fragmentation et stabilité sur transitions répétées ; typecheck, lint et build
production passent. La capture Chrome automatisée montre la façade complète
dans les vues initiale et tournée, sans erreur renderer GSTile. La comparaison
visuelle humaine dans Chrome a ensuite été déclarée conforme au PLY original :
la gate perceptuelle de cette phase est passée.

### Expérience de packing direct rejetée

Un encodeur bit-compatible avec `GSplatResource` a été prototypé pour écrire
directement couleurs, transformations, SH3 et opacités dans les textures de
l'arène. Le test différentiel comparait tous les mots float16/uint32 et les
streams float32. Le premier parcours mesurait 10,591 s de packing ; une boucle
SH fixe RGB×15 l'a réduit à 8,190 s, mais PlayCanvas reste plus rapide sur ce
corpus, autour de 6,0–6,3 s. Le prototype a donc été retiré, conformément à la
gate de performance. La piste restante est de fusionner le packing dans le
décodage Q96 ou de l'exécuter en Worker/WASM : un second parcours JavaScript de
45 coefficients par splat ne peut pas gagner face au chemin moteur actuel.

### Qualification du cœur Q96 — Saint-Étienne v4c

Le benchmark Vitest de 65 536 splats passe de **10,769 ms à 7,598 ms**, soit
**1,42×**. Les essais intermédiaires ont été gardés ou rejetés séparément :

- normalisation `sqrt(w²+x²+y²+z²)` bornée : 9,208 ms ;
- packing d'opacité fixe sans division/modulo dans la boucle : 7,989 ms ;
- boucle SH couleur déroulée par trois : 7,598 ms ;
- vues typées à la place de `DataView` : rejetées, 11,260 ms ;
- déroulage des trois axes : rejeté, 7,684 ms et minimum dégradé.

Dans Chrome sur le cut froid de 7 435 345 splats, la somme Q96 passe de
**3,222 s à 2,616 s**, soit **−18,8 %**. Le total reste dominé par le fetch et
les 6,29 s de construction `GSplatResource`. Sur la transition chaude, Q96
mesure 371–387 ms contre environ 400 ms auparavant ; 6,6 M splats sont déjà
réutilisés, donc le packing PlayCanvas des nouveaux splats reste le premier
coût du commit. Les captures automatisées initiale et tournée sont complètes.

### Qualification du packing natif de transformations — Saint-Étienne v4c

Le packer PlayCanvas de référence matérialise un itérateur fermé sur onze
colonnes, lit trois objets mathématiques par splat, normalise un `Quat`, calcule
trois exponentielles puis convertit en float16. Le chemin GSTile effectue les
mêmes opérations scalaires directement sur les colonnes finales. Un test sur
65 536 quaternions Q96 normalisés compare mot pour mot les buffers RGBA32U et
RGBA16F de référence, padding texture compris.

Trois runs Chrome froids appariés donnent :

| variante | ressource, runs | médiane ressource | médiane commit |
|---|---|---:|---:|
| PlayCanvas standard | 6,382 / 6,278 / 5,951 s | 6,278 s | 6,964 s |
| packing GSTile direct | 5,227 / 5,175 / 5,192 s | **5,192 s** | **5,877 s** |

Le gain est de **−17,3 %** sur la construction de ressource et **−15,6 %** sur
le commit froid, soit environ 1,09 s gagnée. Une transition tournée mesure
755 ms de ressource et 849 ms de commit avec 6,6 M splats réutilisés ; la
capture automatisée est complète.

Deux variantes ont été rejetées séparément : désactiver la copie CPU des
centres économise environ 89 Mo mais dégrade la médiane ressource de 1,4 % ;
supprimer la seconde normalisation quaternion, pourtant float16-identique sur
le corpus, dégrade la médiane de 1,3 %. Le JIT réel prime donc sur la seule
réduction du nombre apparent d'opérations.

### Qualification du packing natif SH3 — Saint-Étienne v4c

La télémétrie de construction sépare désormais couleur, transformations et
SH. Elle confirme que le SH représente environ 73 % du temps de
`GSplatResource` sur le cut froid. Le premier prototype lisait deux fois les
45 colonnes et régressait de 33 % ; il a été rejeté. La version retenue ne lit
chaque colonne qu'une fois, calcule le maximum dans le même ordre que
PlayCanvas, puis s'appuie sur la conversion `ToInt32` déjà effectuée par les
décalages binaires. Un test différentiel compare les quatre textures RGBA32U,
padding compris, sur 16 384 splats pseudo-aléatoires et les cas nuls.

Trois runs Chrome froids par variante, dans la même session et sur le même
build instrumenté, donnent :

| variante | médiane SH | médiane ressource | médiane commit | médiane LOD |
|---|---:|---:|---:|---:|
| PlayCanvas standard | 4,024 s | 5,513 s | 6,257 s | 19,468 s |
| packing GSTile SH3 | **3,740 s** | **5,132 s** | **5,876 s** | **18,144 s** |

Le gain médian atteint **−7,1 % sur le SH**, **−6,9 % sur la ressource**,
**−6,1 % sur le commit** et **−6,8 % sur le LOD total**, sans modifier un seul
mot des textures SH produites. La suite frontend complète passe avec 26
fichiers et 146 tests, ainsi que typecheck, lint et build production.

### Qualification du pipeline Q96→SH3 fusionné — Saint-Étienne v4c

Le décodage fusionné arrondit chaque coefficient avec `Math.fround`, comme
l'écriture préalable dans une `Float32Array`, puis réutilise le même packer
SH3 record par record. Les quatre textures obtenues sont comparées mot pour
mot au chemin décode→45 colonnes→packing. L'allocation CPU du handoff passe de
300 à 184 octets par splat, soit environ **862 Mo économisés** sur le cut de
7,4 M splats.

Le microbenchmark de 65 536 splats mesure 11,849 ms contre 7,797 ms pour le
seul décodage colonne : le surcoût projeté d'environ 0,46 s est volontaire,
car il supprime le second parcours complet et ses 45 colonnes. Trois runs
Chrome froids donnent :

| variante | médiane SH | médiane ressource | médiane commit | médiane LOD |
|---|---:|---:|---:|---:|
| packing SH après décodage | 3,740 s | 5,132 s | 5,876 s | 18,144 s |
| Q96→SH3 fusionné | **0,324 s** | **1,728 s** | **2,493 s** | **15,227 s** |

Le gain médian atteint **−91,3 % sur la phase SH**, **−66,3 % sur la
ressource**, **−57,6 % sur le commit** et **−16,1 % sur le LOD total**. Une
transition chaude mesure 1,088 s au total et 376 ms de commit avec 6,6 M de
splats réutilisés. La suite complète passe avec 26 fichiers et 148 tests,
ainsi que typecheck, lint et build production. Le contrôle visuel humain dans
Chrome est déclaré conforme au PLY original.

### Qualification du pipeline Q96→RGBA16F fusionné — Saint-Étienne v4c

La conversion reproduit exactement les opérations PlayCanvas 2.21.4 :
`DC × 0,28209479177387814 + 0,5`, sigmoïde du logit d'opacité, puis conversion
float32→float16. Un test différentiel compare mot pour mot 65 536 jeux de
valeurs pseudo-aléatoires au moteur, et un second test compare le décodage Q96
fusionné au chemin de référence sur 4 096 records. Le handoff CPU passe de 184
à 176 octets par splat, soit environ **59 MiB économisés** sur 7,4 M splats.

Trois runs Chrome froids du même cut donnent :

| variante | médiane couleur | médiane ressource | médiane commit | médiane LOD |
|---|---:|---:|---:|---:|
| Q96→SH3 fusionné | 0,304 s | 1,728 s | 2,493 s | 15,227 s |
| Q96→SH3 + RGBA16F fusionnés | **0,036 s** | **1,408 s** | **2,074 s** | **14,095 s** |

Le gain médian atteint **−88,2 % sur la couleur**, **−18,5 % sur la
ressource**, **−16,8 % sur le commit** et **−7,4 % sur le LOD total**. Une
transition retour avec 7,2 M splats réutilisés mesure 310 ms au total et
168 ms de commit. La suite complète passe avec 27 fichiers et 150 tests,
ainsi que typecheck, lint et build production. Le contrôle visuel humain dans
Chrome est déclaré conforme au PLY original.

### Qualification des centres interleavés et bornes partagées — Saint-Étienne v4c

Le schéma PLY interne conserve ses marqueurs `x/y/z`, mais le chemin `merged`
écrit les centres décodés directement dans le tableau interleavé attendu par
PlayCanvas. Les bornes calculées pour chaque nœud lors de la préparation de
l'arène sont réutilisées pour le `GSplatData`. La première qualification a
échoué en mode fermé sur un second consommateur des colonnes de position dans
le calcul des bornes de l'arène ; ce consommateur a été converti vers le flux
interleavé et couvert par un test de non-régression avant toute mesure.

| variante | Q96 médian | ressource médiane | commit médian | LOD médian |
|---|---:|---:|---:|---:|
| Q96 → RGBA16F | 3,194 s | 1,408 s | 2,074 s | 14,095 s |
| centres interleavés + bornes partagées | 3,275 s | 1,128 s | 1,817 s | 14,070 s |

Sur trois chargements froids, la création de ressource baisse de **19,9 %** et
le commit de **12,4 %**. Le décodage Q96 augmente de 2,5 %, mais la somme
Q96 + ressource baisse de **4,3 %**. Le LOD médian reste stable dans le bruit
réseau (-0,18 %). À chaud, le commit mesuré est de 217 ms avec 6,6 M de
splats réutilisés, puis 183 ms au retour.

Un prototype exact fusionnant aussi les transformations dans Q96 a été rejeté :
malgré un commit en baisse de 29,4 %, il augmentait le LOD médian de 2,0 %
(14,379 s contre 14,095 s), principalement par déplacement du coût vers le
décodage Q96. Ce résultat négatif est conservé pour éviter de réintroduire
cette variante sans parallélisation ou écriture directe vers l'arène.

Les tests comparent bit à bit 4 096 centres décodés avec
`GSplatData.getCenters()`, les transformations interleavées et colonnaires sur
65 536 valeurs, ainsi que les bornes des deux représentations. La qualification
complète passe 27 fichiers et 151 tests, le typecheck, le lint et le build local
et BIGZEN. Le contrôle visuel humain est validé conforme au PLY original.

### Qualification du packing natif Float16Array — Saint-Étienne v4c

Le chemin rapide écrit directement les composantes quaternion et échelles dans
des vues `Float16Array` superposées aux textures `transformA` et `transformB`.
Les positions restent identiques bit à bit. L'arrondi IEEE natif peut différer
du convertisseur PlayCanvas, qui passe d'abord par float32, mais le test
différentiel sur 65 536 splats borne chaque composante à **un ULP float16
maximum**. Le fallback sans `Float16Array` reste identique mot à mot à
PlayCanvas.

Le microbenchmark A/B dans le même processus mesure 3,534 ms pour le fallback
et 2,786 ms pour le chemin natif par bloc de 65 536 splats, soit **1,27×** ou
**−21,2 %**.

Trois chargements Chrome froids du cut complet donnent :

| variante | transform médian | ressource médiane | commit médian | Q96 médian | LOD médian |
|---|---:|---:|---:|---:|---:|
| centres interleavés + bornes partagées | 0,769 s | 1,128 s | 1,817 s | 3,275 s | 14,070 s |
| packing natif `Float16Array` | **0,486 s** | **0,795 s** | **1,480 s** | **3,121 s** | **13,717 s** |

Le gain médian atteint **−36,8 % sur les transformations**, **−29,5 % sur la
ressource**, **−18,5 % sur le commit** et **−2,5 % sur le LOD total**. La suite
complète passe avec 27 fichiers et 151 tests, ainsi que typecheck, lint et build
production local et BIGZEN. Le contrôle visuel humain est validé conforme au
PLY original.

### Qualification de l'adoption zéro-copie SH3 — Saint-Étienne v4c

Les quatre tableaux RGBA32U produits pendant Q96 ont désormais exactement la
capacité `ceil(sqrt(N)) × ceil(N / width)` calculée par PlayCanvas. La ressource
crée d'abord les quatre textures de remplacement avec ces tableaux comme
sources initiales, puis bascule la table des streams seulement si toutes les
allocations ont réussi. Un échec partiel détruit les remplacements et conserve
intacts les anciens streams.

La première qualification a échoué en mode fermé parce que le contrat du
décodeur exigeait encore `N × 4` mots sans padding. Le contrat a été corrigé et
couvert avant toute mesure. Pour 7 435 345 splats, le padding n'ajoute que
75 776 octets et l'adoption supprime une copie de 475 937 856 octets, soit
environ **454 Mio** de pic mémoire CPU.

| variante | SH médian | ressource médiane | commit médian | Q96 médian | LOD médian |
|---|---:|---:|---:|---:|---:|
| packing natif `Float16Array` | 0,271 s | 0,795 s | 1,480 s | 3,121 s | 13,717 s |
| adoption zéro-copie SH3 | **0,195 s** | **0,738 s** | **1,447 s** | 3,441 s | **13,660 s** |

Le gain médian atteint **−28,0 % sur SH**, **−7,2 % sur la ressource** et
**−2,2 % sur le commit**. Le LOD reste stable dans le bruit réseau (−0,4 %).
La hausse Q96 n'est pas structurelle : la capacité supplémentaire ne représente
que 1 184 texels et aucune opération supplémentaire n'est exécutée par splat.
La qualification complète passe 28 fichiers et 154 tests, le typecheck, le
lint et les builds local et BIGZEN. Le contrôle visuel humain est validé
conforme au PLY original.

### Qualification de l'adoption zéro-copie couleur — Saint-Étienne v4c

Le tableau RGBA16F produit pendant Q96 est maintenant dimensionné à la capacité
exacte de la texture `splatColor`, puis adopté par le même basculement atomique
que les streams SH3. Le padding de 1 184 texels ajoute seulement 9 472 octets.
L'adoption supprime la copie de 59 482 760 octets et son stockage dupliqué net,
soit environ **56,7 Mio** pour les 7 435 345 splats du cut complet. Les valeurs
RGBA16F existantes sont transmises sans conversion ni réarrondi.

Après le redémarrage de BIGZEN, la qualification a été reconstruite depuis le
commit `main` `600ac5a`. Chaque variante reçoit un warm-up exclu, puis trois
chargements du même cut, dans le même onglet Chrome et avec la vue recommandée
du bundle. L'ordre candidat → baseline → candidat borne la dérive temporelle :

| variante | couleur médiane | ressource médiane | commit médian | LOD médian |
|---|---:|---:|---:|---:|
| candidat, premier passage | **0,024 s** | **0,692 s** | 1,385 s | 13,533 s |
| baseline `main` | 0,037 s | 0,710 s | 1,412 s | 13,987 s |
| candidat, retour | **0,023 s** | **0,703 s** | **1,365 s** | 14,083 s |

La médiane des six runs candidats est de 23,5 ms pour la couleur, contre 37 ms
pour le baseline, soit un gain isolé et reproduit de **−36,5 %**. Les médianes
agrégées candidat sont 0,699 s pour la ressource et 1,373 s pour le commit,
respectivement −1,6 % et −2,8 % face au baseline. Le LOD total reste soumis au
bruit du chargement réseau ; aucune accélération globale n'est revendiquée sur
ce seul échantillon. Le contrôle visuel humain est validé conforme au PLY
original.

### Qualification de l'adoption zéro-copie opacité — Saint-Étienne v4c

Les quatre tableaux RGBA32F produits pendant Q96 sont maintenant dimensionnés
à la capacité exacte des textures PlayCanvas et installés dans la map avant la
première synchronisation des `extraStreams`. Le padding de 1 184 texels ajoute
75 776 octets. Le chemin supprime une copie et une duplication nette de
475 862 080 octets, soit environ **453,8 Mio**, sans conversion : les mêmes
valeurs float32 alimentent directement les shaders d'opacité directionnelle.

La qualification utilise le même ordre candidat → baseline → candidat, un
warm-up exclu par build, le même onglet Chrome et le cut complet de 7 435 345
splats :

| variante | streams médian | ressource médiane | commit médian | LOD médian |
|---|---:|---:|---:|---:|
| candidat, premier passage | **0,182 s** | **0,711 s** | **1,268 s** | 14,564 s |
| baseline `main` `851d628` | 0,283 s | 0,718 s | 1,401 s | 13,158 s |
| candidat, retour | **0,193 s** | 0,722 s | **1,304 s** | **13,061 s** |

Sur les six runs candidats, la médiane `streams` est de 186,5 ms, soit
**−34,1 %** face au baseline. Les médianes agrégées sont 0,712 s pour la
ressource, 1,283 s pour le commit et 13,066 s pour le LOD : respectivement
−0,8 %, **−8,5 %** et −0,7 %. Le load médian est apparié à moins de 0,5 %, ce
qui rend le gain de commit interprétable. La suite complète passe 28 fichiers
et 157 tests, le lint, le typecheck et les builds local et BIGZEN. Le contrôle
visuel humain est validé conforme au PLY original.

### Qualification de la promotion directe de l'arène — Saint-Étienne v4c

Le premier cut crée désormais une `GSplatResource` directement à la capacité
GPU maximale, mais ne packe que son préfixe actif. Cette même ressource reçoit
ensuite l'interface mutable `maxSplats/update` de l'arène persistante. Le chemin
supprime donc la petite ressource de staging initiale, la seconde création
d'entité et toutes ses copies texture vers le conteneur. Les raffinements
suivants gardent les slots, copies GPU partielles et intervalles existants.

PlayCanvas conservant les niveaux `TypedArray` des textures de données après
upload, le candidat les libère immédiatement après le premier rendu soumis. À
la capacité de 7 500 000 splats (7 502 121 texels avec padding), cela libère
exactement **1 200 339 360 octets**, soit environ **1,118 Gio**, sans modifier
les textures GPU. Un test de contrat vérifie que seules les sources typées sont
libérées et que le compteur actif reste borné par la capacité.

Le protocole garde le même onglet Chrome, un warm-up exclu par build et l'ordre
candidat → baseline `befbebe` → candidat. Les trois derniers runs candidats
incluent la libération CPU post-upload :

| variante | streams médian | ressource médiane | commit médian | load médian | LOD médian |
|---|---:|---:|---:|---:|---:|
| candidat, premier passage | **0,078 s** | **0,550 s** | **1,080 s** | **11,067 s** | **12,086 s** |
| baseline `befbebe` | 0,191 s | 0,705 s | 1,287 s | 12,778 s | 14,077 s |
| candidat final, sources CPU libérées | **0,068 s** | **0,544 s** | **1,054 s** | **12,000 s** | **13,064 s** |

Face au baseline, le candidat final gagne **−64,4 % sur `streams`**, **−22,8 %
sur la ressource** et **−18,1 % sur le commit**. Le LOD total baisse de 7,2 %,
mais 6,1 points suivent le bruit du load ; seul le gain interne de commit est
attribué à cette phase. La suite complète passe 28 fichiers et 161 tests, le
lint, le typecheck et les builds de production local et BIGZEN. Le contrôle
visuel humain est validé conforme au PLY original.

### Qualification des rotations Q96 déjà normalisées — Saint-Étienne v4c

Le décodeur Q96 normalise chaque quaternion avant de l'écrire dans les colonnes
`Float32Array`. Le packer PlayCanvas peut donc recevoir explicitement ce contrat
et éviter une seconde racine carrée ainsi que quatre divisions par splat. La
canonicalisation du signe (`w >= 0`) et le chemin générique, qui continue à
normaliser les entrées non garanties, sont conservés.

Un test différentiel déterministe couvre 65 536 quaternions représentatifs du
codage Q96. Sur les 196 608 composantes quaternion stockées en float16, dix
seulement diffèrent du chemin qui renormalise, soit **0,0051 %**, et l'écart
maximum est exactement **un ULP float16**. Les positions et échelles restent
identiques. Le microbenchmark sur 65 536 splats mesure 2,824 ms avec le contrat
Q96 contre 2,989 ms avec la renormalisation redondante, soit **1,06×** ou
**−5,5 %** sur le packer isolé.

Le protocole Chrome conserve le même onglet, le même cut, un warm-up exclu par
build et l'ordre candidat → baseline `4920adf` → candidat :

| variante | transform médian | ressource médiane | commit médian | load médian | LOD médian |
|---|---:|---:|---:|---:|---:|
| candidat, premier passage | **0,448 s** | **0,524 s** | **0,999 s** | 12,135 s | 13,225 s |
| baseline `4920adf` | 0,464 s | 0,538 s | 1,007 s | **11,664 s** | **12,683 s** |
| candidat, retour | **0,418 s** | **0,482 s** | **0,927 s** | 12,245 s | 13,174 s |

Sur les six runs candidats, les médianes agrégées sont 0,4185 s pour les
transformations, 0,491 s pour la ressource et 0,9545 s pour le commit : gains
respectifs de **−9,8 %**, **−8,7 %** et **−5,2 %** face au baseline. Le LOD
agrégé augmente de 4,1 %, mais le load réseau augmente simultanément de 4,5 % ;
aucune régression du chemin interne n'est donc observée ni aucune accélération
du LOD total revendiquée. La suite complète passe 28 fichiers et 162 tests, le
lint, le typecheck et les builds de production local et BIGZEN. Le contrôle
visuel humain est validé conforme au PLY original.

### Qualification du Worker borné Q96→transformations — Saint-Étienne v4c

Le Worker ne matérialise ni colonnes `logScale/rotation`, ni ressource
intermédiaire. Il lit les 20 premiers octets du record Q96, écrit les centres
float32 et les textures PlayCanvas `transformA` RGBA32U / `transformB` RGBA16F,
puis calcule les bornes conservatrices du nœud. Pendant ce temps, le thread UI
continue le décodage couleur, SH et opacité. Les textures finales sont adoptées
par la ressource sans second packing au commit.

La file accepte au plus quatre tâches actives. Les tâches en attente conservent
une référence au pack brut déjà mis en cache ; la copie de 96 octets/splat
n'est créée et transférée qu'au moment où un Worker est libre. Chaque résultat
transféré occupe 36 octets/splat. Le handoff permanent passe en parallèle de
176 à **172 octets/splat**, soit 29 741 380 octets (environ **28,4 Mio**) évités
sur les 7 435 345 splats du cut.

Un test différentiel sur 16 384 records compare bit à bit centres,
`transformA`, `transformB` et bornes au décodeur/packer précédent. Deux tests
de pool vérifient qu'un seul payload est admis par Worker disponible et qu'une
tâche annulée en file n'est jamais envoyée. Le fallback synchrone reste fermé
sur les mêmes validations Q96 et son compteur est exposé dans le HUD.

Le protocole Chrome conserve le même bundle, un warm-up exclu par build et
l'ordre candidat → baseline `da454a8` → candidat :

| variante | Q96 médian | transform médian | ressource médiane | commit médian | load médian | LOD médian |
|---|---:|---:|---:|---:|---:|---:|
| candidat, premier passage | **3,179 s** | **0,025 s** | **0,103 s** | **0,334 s** | 12,120 s | 12,467 s |
| baseline `da454a8` | 3,350 s | 0,419 s | 0,503 s | 0,984 s | 11,627 s | 12,630 s |
| candidat, retour | **3,216 s** | **0,026 s** | **0,106 s** | **0,363 s** | **11,162 s** | **11,502 s** |

Sur les six runs candidats, les médianes agrégées sont 3,195 s pour Q96,
25 ms pour les transformations, 104,5 ms pour la ressource, 336 ms pour le
commit, 11,615 s pour le load et 11,977 s pour le LOD. Face au baseline, les
gains sont **−4,6 % sur Q96**, **−94,0 % sur les transformations**, **−79,2 %
sur la ressource**, **−65,9 % sur le commit** et **−5,2 % sur le LOD**. Le load
est apparié à −0,1 %, ce qui rend le gain global interprétable.

Après correction du bornage mémoire, trois runs supplémentaires donnent des
médianes de 3,191 s Q96, 26 ms transformations, 105 ms ressource et 329 ms
commit. La télémétrie rapporte 4,343 s de service Worker cumulé — somme des
tâches concurrentes, pas temps mur — et **zéro fallback**. La suite complète
passe 30 fichiers et 167 tests, le lint, le typecheck et les builds production
local et BIGZEN. Le contrôle visuel humain dans Chrome est validé conforme au
PLY original.

### Qualification du Worker borné Q96 complet — Saint-Étienne v4c

Le pool produit maintenant dans une seule passe toutes les représentations
finales consommées par PlayCanvas. Les buffers de sortie sont transférés sans
copie par `postMessage`, puis copiés par plages contiguës dans le cut global.
Il n'existe plus de boucle de déquantification/packing par splat sur le thread
UI. Le fallback reste synchrone, mais partage le même décodeur et ne s'active
qu'après une défaillance Worker.

L'oracle différentiel déterministe sur 16 384 records compare bit à bit les
centres, `transformA/B`, la couleur RGBA16F, les quatre streams SH3, les quatre
streams d'opacité et les bornes au pipeline précédent. Les tests de pool
conservent les garanties d'admission et d'annulation. La sortie permanente du
cut reste à 172 octets/splat ; seul l'ensemble de travail des quatre Workers
actifs augmente nominalement d'environ 34,0 Mio.

Le protocole Chrome conserve le même bundle, un warm-up exclu par build, le
même onglet et l'ordre candidat → baseline `dd9c585` → candidat :

| variante | Q96 UI médian | Worker Σ médian | ressource médiane | commit médian | load médian | LOD médian |
|---|---:|---:|---:|---:|---:|---:|
| candidat, premier passage | **0,431 s** | 6,895 s | **0,100 s** | 0,336 s | 12,140 s | 12,357 s |
| baseline `dd9c585` | 3,119 s | **4,262 s** | 0,104 s | **0,332 s** | **11,909 s** | **12,253 s** |
| candidat, retour | **0,406 s** | 6,518 s | 0,108 s | 0,383 s | 13,360 s | 13,762 s |

Sur les six runs candidats, la médiane Q96 du thread UI est de **424 ms**
contre 3,119 s, soit **−86,4 %**. Le service Worker cumulé augmente de 58,0 %
car il inclut désormais couleur, SH et opacité ; ces tâches s'exécutent en
parallèle. Les médianes candidat sont 103,5 ms pour la ressource, 369,5 ms pour
le commit, 12,287 s pour le load et 12,607 s pour le LOD. Face au baseline,
la ressource reste stable (−0,5 %), le commit ajoute 37,5 ms et le LOD +2,9 %,
alors que le load réseau augmente simultanément de 3,2 % : aucune régression
globale interne n'est attribuée au candidat sur cet échantillon. Les six runs
rapportent **zéro fallback**. La suite complète passe 30 fichiers et 167 tests,
le lint, le typecheck et les builds production local et BIGZEN. Le contrôle
visuel humain est validé conforme au PLY original.

## Sources primaires

- WebSplatter, papier et code officiel : https://arxiv.org/abs/2602.03207 et
  https://github.com/websplatter/WebSplatter
- HiGS, NVIDIA Spatial Intelligence Lab :
  https://research.nvidia.com/labs/sil/projects/higs/
- A LoD of Gaussians, code officiel SIGGRAPH 2026 :
  https://github.com/FelixWindisch/LoDOfGaussians
- Hierarchical 3D Gaussians, papier/code auteurs :
  https://arxiv.org/abs/2406.12080 et
  https://github.com/graphdeco-inria/hierarchical-3d-gaussians
- LODGE : https://arxiv.org/abs/2505.23158
- PlayCanvas Streamed SOG et performance :
  https://developer.playcanvas.com/user-manual/gaussian-splatting/formats/streamed-sog/
  et https://developer.playcanvas.com/user-manual/gaussian-splatting/building/performance/
- API officielle `GSplatContainer` PlayCanvas 2.21.4 :
  https://api.playcanvas.com/engine/classes/GSplatContainer.html
- Sources et notes de version PlayCanvas :
  https://github.com/playcanvas/engine et
  https://github.com/playcanvas/engine/releases
- Construction d'octree out-of-core :
  https://diglib.eg.org/items/62d4fdab-2dd4-4e8c-8bb6-b84d0d17a785
- WebGPU Recommendation : https://www.w3.org/TR/webgpu/

## Prochaines gates

1. Ajouter attente Worker, copies entrée/sortie, upload, sort/raster et
   longues tâches à la télémétrie structurée.
2. Répéter les essais appariés Chrome sur un corpus de caméras figé et publier
   médiane, p95, long tasks, mémoire CPU/VRAM et différences d'image.
3. Conserver le décodage Q96 complet dans le Worker borné et mesurer les tâches
   longues avant/après sur un parcours caméra figé. La variante synchrone sur
   le thread UI avait réduit le commit de 29,4 % mais augmenté le LOD de 2,0 % ;
   ne pas la réintroduire sans gate sur Q96 + load + ressource.
4. Instrumenter le nombre de spans, les octets réellement copiés et le temps
   de reconstruction du manager unifié, puis figer un parcours caméra répétable.
5. Construire un prototype external-sort Morton sur une copie immuable du PLY.
6. Geler avec l'équipe le corpus de caméras et les seuils scientifiques avant
   toute optimisation de proxy.
