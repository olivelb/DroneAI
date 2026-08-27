# Vérification de l’audit externe : tiler et renderer

27 août 2026. Code inspecté : `fe7c069fc3821693e92bf27d42531771538439ac`,
sur les PR de qualification #277–279, pas seulement le `main` cité dans
l’audit. L’audit fourni par l’utilisateur est une proposition à vérifier ;
ses recommandations ne constituent pas des résultats expérimentaux.

## Conclusion

Les deux axes principaux sont pertinents : réduire les E/S de construction
et les mouvements de données du viewer. Mais **l’agrégation directe existe
déjà**, les proxies se construisent déjà après leurs enfants, et plusieurs
chiffres précèdent les optimisations récentes. Aucune accélération annoncée
ci-dessous n’est acquise sans benchmark. La conformité au PLY reste requise,
y compris quand on change le pipeline de rendu.

Le travail livré maintenant est le
[profil RAM 768/1 536 Mio et son comparatif manuel](../benchmarks/gstile-memory-profile-qualification.md).
L’ordre proposé ci-dessous privilégie ensuite les déplacements interactifs
observés ; pour un objectif producteur 100 M, le tri externe remonte en tête.

## Les douze propositions confrontées au code

| Audit | Verdict et preuve locale | Décision / qualification indispensable |
| --- | --- | --- |
| 1. Tri externe Morton + bottom-up | **Partiellement confirmé.** `tiler.py::_split_work_file` relit/réécrit les populations sur l’axe le plus long ; `_visit_branch` construit déjà les proxies après ses enfants. `_morton_codes` existe mais calcule ses bornes sur la population reçue. | Grand potentiel pour 50–100 M, non mesuré. Fournir des bornes globales stables, départager les collisions par source ID. Un tri externe écrit aussi ses runs et passes de fusion : « une seule réorganisation physique » est trop absolu. |
| 2. Cache/Q96 possédés par Worker | **Confirmé comme cible.** `decode-worker-pool.ts` copie encore chaque entrée avec `slice` ou `Uint8Array.set`, puis transfère la copie. Le recyclage réduit les allocations, pas les 96 octets/splat copiés. | Priorité viewer après RAM. Co-localiser cache et décodage ou transférer avec un protocole d’emprunt/retour. Un Worker de cache qui retransfère vers quatre Workers de décodage ne supprime pas automatiquement les copies. |
| 3. Sweep leaf_size | **Hypothèse justifiée**, optimum inconnu. `GsTileBuildOptions.leaf_size` vaut 65 536. Les feuilles plus fines peuvent réduire le travail ajouté, mais augmenter métadonnées, proxies et requêtes. | Tester 65 536 / 49 152 / 32 768 / 16 384. Garder les autres paramètres compatibles et déclarés ; ne pas réduire silencieusement `lod_proxy_size`, qui doit rester ≤ leaf_size. Requalifier les images : nouvelles populations et bornes de quantification. |
| 4. Q96 GPU decode/scatter | **Projet distinct**, non réfuté par l’essai négatif `writeTexture`. La sortie native actuelle vaut environ 172 octets/splat, l’entrée Q96 96. | Potentiel élevé, risque élevé. Le rapport 96/172 n’est ni un speedup garanti ni une mesure exacte des octets GPU actuels. Vérifier formats storage, limites de bindings, arrondis/half/quaternions et synchronisation des slots. |
| 5. Transport transposé + Zstd | **À mesurer.** Q96 est AoS ; grouper les colonnes peut aider la compression. Le ratio de 13,1 % cité provient d’un ancien lot de prefetch, pas du corpus entier ni du dernier parcours. | Commencer par une transformation réversible hors ligne, sans changer le contrat canonique. Des entiers Q96 transposés ne sont pas directement les textures natives PlayCanvas : conversion et packing restent nécessaires. |
| 6. Source IDs en sidecar | **Arithmétique confirmée** : 8/96 = 8,33 % bruts. `decode.ts` conserve un décodeur scientifique avec IDs, mais les streams renderer les omettent. | C’est un nouveau contrat, pas un champ à supprimer du Q96 v1. Préserver identité, intégrité, reconstruction et usages d’interaction. Les IDs ne sont pas nécessairement monotones après tri spatial ; mesurer delta/varint au lieu de promettre un sidecar minuscule. |
| 7. Encode/V4/Zstd parallèles | **Non implémenté comme pool borné** dans `gaussian_tiles`. `_visit_branch` parcourt gauche/droite séquentiellement ; `format.py` compresse chaque pack avec Zstd niveau 1. | Expérience 1/2/4 workers, plafond en octets et tâches, un writer déterministe. Commencer par encode/compression indépendants, puis V4 après profilage ; ne pas supposer que toutes les boucles Python libèrent le GIL. |
| 8. Agrégation directe 2 Mio | **Déjà implémentée.** `pack_tile` regroupe par `(kind, depth)` lors du parcours spatial, `pack_target_bytes` est exposé par le CLI ; `_enforce_aggregate_memory_bound` limite les payloads en attente à 256 Mio. Tests de déterminisme, payloads, Zstd et séparation des profondeurs. | Ne pas refaire cette fonctionnalité. Conserver le choix de profil ; l’observation historique 10,257 s contre 12,354 s ne démontre pas un bénéfice universel. Repack reste utile aux anciens bundles immuables. |
| 9. Concurrence HTTP adaptative | **Possible, priorité conditionnelle.** Six accès réseau et deux lectures IndexedDB sont déjà indépendants ; la demande passe avant le prefetch. | Ce n’est pas « faible risque » sans garde de mémoire/backpressure. Les relectures chaudes actuelles n’ont pas de réseau critique ; augmenter à 12–16 n’y résout pas le décodage. D’abord un sweep froid 4/6/8/12 sur un transport HTTP/2 réellement vérifié. |
| 10. Précision du prefetch | **Bonne direction, constat chiffré ancien.** L’expiration à 1 500 ms est corrigée ; le plancher spéculatif passe de 96 à 64 Mio, sur replay à utilité 8–12 %. | Replay : −18,73 % de payload spéculatif, pas une mesure de latence. Avant un score probabiliste, compléter l’attribution de l’utilité aux hits IndexedDB après éviction RAM ; elle est encore partielle. |
| 11. Cache décodé / cooldown GPU | **Pas équivalent au cache RAM brut livré.** 256 Mio / 172 ≈ 1,56 M splats théoriques, moins avec overhead. | Tester après la propriété des buffers. Le cache doit survivre aux transferts sans dupliquer systématiquement les sorties. Un cooldown GPU augmente la VRAM : autre budget, autres risques. |
| 12. Culling avant tri | **À nuancer.** PlayCanvas embarqué possède déjà `GSplatFrustumCuller`, culling/compaction d’intervalles. Cela ne signifie pas un rejet fin conservatif de chaque splat hors écran. | Mesurer les passes et le nombre d’éléments réellement triés. Ajouter seulement un filtre conservatif utile, tenant compte du support projeté et des bords/near-plane ; pas un simple test du centre. |

Chemins de code : `app1-colmap/gaussian_tiles/{tiler,format,repack}.py`,
`app1-colmap/gaussian_tiles/tests/test_gstile.py`,
`app4-dashboard/frontend/app/lib/gstile/{decode-worker-pool,decode,range-source,lod-prefetch}.ts`,
et le PlayCanvas 2.21.4 patché dans `node_modules/playcanvas/build/playcanvas/src/scene/gsplat-unified/`.

## Lecture correcte des mesures historiques

- 12,912 → 1,764 s décrit une observation de transition avec réutilisation
  de l’arène, pas un facteur garanti sur tout chargement.
- [Copie Worker](WORKER_DECODE_QUALIFICATION_20260827.md) : les ~1,95 Go et
  ~759 ms sont des octets logiques et temps de service cumulés d’une ancienne
  observation. Ne pas les soustraire de la latence actuelle.
- [Assemblage Worker](WORKER_ASSEMBLY_QUALIFICATION_20260827.md) : ~98 % porte
  sur le travail d’assemblage explicitement chronométré sur le thread principal,
  pas sur le lag total, les FPS ou tous les octets copiés.
- [Recyclage d’entrée](INPUT_RECYCLE_QUALIFICATION_20260827.md) : déjà présent,
  aucune suppression de la copie défensive des entrées.
- [GPU direct upload](GPU_DIRECT_UPLOAD_EVALUATION_20260827.md) : +61–72 % de
  temps hôte dans la forme essayée ; résultat négatif conservé, pas rejet de
  toute architecture compute/scatter.
- [Préchargement récent](../benchmarks/gstile-prefetch-budget-qualification.md) :
  réseau de demande nul au retour/revisit dans les traces, mais lectures disque
  et décodage encore importants. Distinguer temps cumulé Workers et temps mur.
- [Agrégation](../benchmarks/gstile-pack-aggregation-qualification.md) : 99,41 %
  d’efficacité brute concerne un cut donné ; 572 s concerne un repack particulier.

## Ordre d’implémentation proposé, avec portes de décision

1. **Terminer le pilote RAM** : latence après geste, hits RAM/IndexedDB,
   long tasks et pics mémoire. Ne généraliser 1,5 Gio qu’après qualification
   de mémoire totale et d’autres appareils. Aucun gain encore revendiqué.
2. **Prototype de propriété cache/décodage Worker** : commencer sur des packs
   et un trace replay figés. Protocole explicite pour lecture, pinning, annulation,
   éviction, retour de buffers et panne Worker. Garder un budget global, pas
   quatre caches de 1,5 Gio. Comparer copie d’entrée, allocations, latence et
   fidélité byte-for-byte. Rejeter un Worker unique qui élimine une copie mais
   sérialise assez de décodage pour dégrader le temps mur.
3. **Microbenchmark tiler encode/Zstd 1/2/4** : changement plus local que
   Morton, qui peut conserver le bundle canonique identique. Writer dans
   l’ordre, backpressure par octets, cancellation et propagation d’erreur,
   aucun relâchement de publication atomique/fsync. Mesurer CPU/E/S/RSS avant
   de modifier V4 ou de lancer des sous-arbres en parallèle.
4. **Prototype tri externe Morton** (priorité producteur 100 M) : runs bornés,
   fusion multi-voies, tie-break source ID, feuilles contiguës et métadonnées
   bottom-up. Tester centres confondus, distributions très déséquilibrées,
   gros IDs et annulations/disque plein. Les bornes de quantification et les
   proxies peuvent changer : conservation de tous les IDs exacts et contrôle
   image figé obligatoires. Ne pas exiger un bundle identique à l’ancien
   partitionnement, mais un bundle répétable pour le nouveau profil.
5. **Granularité puis transport réversible**, en expériences séparées :
   coût par geste, nombre de packs, bytes utiles/téléchargés, decode, commit,
   p50/p95 sur assez de répétitions. Bench de compression par type de champ
   avant tout contrat sidecar. Pour Morton, relancer la granularité car la
   distribution des feuilles aura changé.
6. **GPU decode/scatter**, puis culling fin si les passes le justifient :
   fixtures 1/3/7,5 M, hashes des streams et images, queue-complete et temps
   hôte, VRAM ; plusieurs adapters/navigateurs. Conserver le chemin actuel
   comme contrôle et repli. Pas de remplacement de PlayCanvas à ce stade.

La concurrence réseau adaptative remonte dans cet ordre seulement si un
parcours froid montre des Workers sous-alimentés et de l’attente réseau
critique. Le cache décodé remonte si le pilote RAM élimine le disque mais
laisse le décodage dominant. Les gains supposés doivent suivre ces mesures,
pas des pourcentages d’effort arbitraires.

Le préflight disque `N × 96 × maximum_depth` est toujours présent. Le remplacer
demande un estimateur conservateur pour le nouveau tree, une marge et un contrôle
d’espace pendant les écritures ; ne pas simplement abaisser la réserve.

## Sources primaires et portée

La construction de hiérarchie à partir d’un ordre Morton est étayée par
[Karras, HPG 2012](https://research.nvidia.com/publication/2012-06_maximizing-parallelism-construction-bvhs-octrees-and-k-d-trees)
et son [exposé NVIDIA](https://developer.nvidia.com/blog/thinking-parallel-part-iii-tree-construction-gpu/).
Ces travaux donnent un algorithme de hiérarchie, pas une preuve de gain E/S
pour notre tiler Python/out-of-core ni de qualité identique des proxies.

Le transfert d’un `ArrayBuffer` détache le buffer côté émetteur :
[documentation Mozilla](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Transferable_objects).
D’où l’obligation de résoudre la propriété/réutilisation du cache, pas
seulement de déplacer `fetch` dans un Worker.

Les écritures compute exigent des formats/usages storage compatibles :
[WebGPU](https://gpuweb.github.io/gpuweb/#texture-format-caps),
[table WGSL](https://gpuweb.github.io/gpuweb/wgsl/#texel-formats).
La capacité réelle de l’adapter et les formats créés par PlayCanvas restent
à vérifier ; la spécification ne prouve pas la parité des calculs.

Zstd dispose de travail natif hors GIL, mais ses petits blocs n’utilisent
pas nécessairement plusieurs threads :
[python-zstandard](https://python-zstandard.readthedocs.io/en/latest/multithreaded.html).
Un pool de packs indépendants doit garder un contexte par worker et éviter
la sursouscription ; le temps V4 Python reste à profiler séparément.
