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
| B1 | P0 | Le mode fidèle `merged` recharge et redécode tous les nœuds du nouveau cut ; la seule ressource résidente est `__merged__`, donc les nœuds signalés comme réutilisés ne le sont pas côté CPU/GPU. | Pause proportionnelle au cut complet, même pour une petite variation de caméra. | Confirmé, phase arène GPU à faire. |
| B2 | P0 | SHA, décodage Q96 et création de ressources s'exécutent sur le thread UI. Le `setTimeout(0)` permet l'annulation entre packs mais chaque pack reste une longue tâche synchrone. | Jank d'entrée et frames manquées pendant le raffinement. | Partiellement réduit : pipeline columnar direct 1,90× et 2,28 Go transitoires évités ; Worker/WASM à faire. |
| B3 | P0 | L'opacité directionnelle dépend de la caméra dans un `workBufferModifier`, mais les mises à jour de caméra appellent `#updateOpacityCameraUniform(false)`. Le work buffer n'est réécrit qu'après le debounce LOD. | Opacité temporairement périmée pendant la navigation, puis réupload global coûteux. | Confirmé ; déplacer l'évaluation au shader par frame ou vers un pass GPU ciblé. |
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

État : **démarré** par la suppression des allocations/copies décodées
intermédiaires. `GSplatContainer` et les intervalles du monde unifié ont été
audités ; le profil réel montre 6,6–6,7 s de packing de ressource à remplacer.
Le conteneur GPU PlayCanvas reste à prototyper derrière
`GaussianRenderBackend`.

### 2. Pipeline Worker/WASM pour SHA + Q96 + préparation de streams — impact très élevé

Déplacer validation et décodage hors du thread UI. Le Worker doit posséder le
cache brut ou recevoir des buffers transférables sans détacher le cache du
main thread. La sortie doit être directement column-major dans le format
d'upload PlayCanvas afin d'éviter la seconde décolumnarisation de 58 canaux.
SIMD WASM est à comparer au TypeScript JIT ; il ne sera retenu que mesuré.

Acceptance : aucune tâche UI > 50 ms pendant un parcours froid/chaud, hash et
coefficients identiques, annulation générationnelle, mémoire de file bornée.

État : **décodage column-major final livré pour `merged`** ; Worker non
implémenté.

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

État : bug confirmé, design à prototyper dans le patch PlayCanvas.

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

Une rotation de caméra a mis en évidence la limite suivante : le nouveau cut
de 7 487 153 splats dispose d'assez d'espace libre total, mais pas d'un trou
contigu assez grand. Le plan actuel compacte alors toute l'arène : 0 splat
réutilisé, 12,912 s total, 5,022 s load et 7,876 s commit. L'optimisation
prioritaire suivante est donc l'allocation d'un nœud sur plusieurs spans, ou
une compaction GPU avec scratch borné, afin de conserver les ~6,6 M splats
communs sans nouveau décodage/packing.

La validation automatisée confirme une façade complète et un world state de
7 435 345 splats sans erreur renderer. La comparaison visuelle humaine avec le
PLY original reste la gate avant merge.

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

1. Ajouter télémétrie fetch/SHA/decode/columnar/upload/sort/raster et long-task.
2. Répéter les essais appariés Chrome sur un corpus de caméras figé et publier
   médiane, p95, long tasks, mémoire CPU/VRAM et différences d'image.
3. Prototyper un Worker propriétaire du cache brut, puis comparer TS et WASM.
4. Fractionner les nœuds de l'arène persistante sur plusieurs spans libres,
   avec invariants d'absence de recouvrement et tests de fragmentation.
5. Construire un prototype external-sort Morton sur une copie immuable du PLY.
6. Geler avec l'équipe le corpus de caméras et les seuils scientifiques avant
   toute optimisation de proxy.
