# Réglage de l'opacité des bords — 2026-09-05

Les deux viewers exposent un multiplicateur de 0,25 à 2 (25–200 %), neutre à 1.
Le noyau est `alpha = alpha_centre * exp(-r_sigma² / (2 * multiplicateur))`,
avec support fixe à trois sigmas et seuil de rejet inchangé de 1/255.
Le centre, les SH et l'exposition sont indépendants. Ce réglage modifie la
décroissance radiale, pas l'opacité globale ni la quantité de géométrie.
Il élargit ou rétrécit la partie perceptible des splats dans leur support.

Direct3D transmet le paramètre au pixel shader et au picking. WebGPU adapte
aussi le découpage du quad au seuil alpha ; sans cette correction les bords
plus opaques seraient coupés au rayon correspondant au réglage précédent.
Le setter WebGPU réveille le rendu à la demande même sans mouvement de caméra.

## Qualification sur le GPU réel

RTX 4070 Laptop, 1440 × 900, même caméra `lod-carving-camera.json`, bundle
Saint-Étienne `gstile-streams`, budget 2 M, coupe fixe de 1 982 126 splats,
SH couleur et opacité de degré 3. Trois captures natives 25/100/200 %, puis
quatre changements WebGPU successifs 25/100/200/100 % sans déplacement.

- Native 100 % : pixels exactement identiques à `lod-carving-corrected.bmp`.
- Variation moyenne des pixels par rapport à 100 % : 27,04 / 10,05 niveaux
  RGB dans le natif à 25 / 200 %, 27,40 / 10,08 dans WebGPU.
- Écart moyen entre les deux moteurs : 2,016 / 2,001 / 1,928 niveaux RGB
  à 25 / 100 / 200 %. Ce ne sont pas deux moteurs pixel-identiques.
- Retour WebGPU à 100 % : écart moyen de 0,0011 niveau RGB entre captures,
  donc pratiquement identique mais pas bit-identique (le tri/raster reste
  celui de PlayCanvas). Tolérance du test : 0,01 niveau RGB.
- Population inchangée ; aucune erreur WebGPU. Valeurs hors intervalle et
  non finies rejetées. Les trois rapports natifs valident également le tri,
  les 20 combinaisons SH et les contrats de cache sur GPU.
- Compilation MSVC/FXC et CTest réussis ; 344 tests GSTile, TypeScript,
  ESLint et compilation Next de production réussis.

Les mesures portent sur cette caméra ; aucun gain de performance n'est revendiqué.
Les captures de diagnostic `*-binding-debug.*` sont les essais natifs AVANT
correction du binding pixel shader ; elles sont conservées, pas qualifiées.
Le rapport `*-report-size-debug.json` est un échec du transport du rapport
de test, remplacé par le rapport final. Le serveur de benchmark accepte
désormais 64 MiB pour les quatre captures (limite auparavant 16 MiB).

## Reproduction et preuves

Dans le dossier portable, `evidence/native-edge-{25,100,200}.json/.bmp`,
`evidence/webgpu-edge-opacity.json`, `evidence/webgpu-edge-*.png` et
`evidence/edge-opacity-pixels.json` contiennent les résultats.

Générer chaque rapport natif avec le bundle complet et :
`--benchmark --no-prefetch --budget 2000000 --frames 20 --camera lod-carving-camera.json --edge-opacity 1 --output native-edge-100.json --screenshot native-edge-100.bmp`.
Adapter le multiplicateur et les noms pour 25/200 %.
Générer le banc WebGPU avec `node native-viewer/benchmarks/prepare-edge-opacity.mjs <dossier>` ;
le servir avec `serve-streaming.mjs <dossier> <bundle> <native-edge-100.json> <webgpu-edge-opacity.json>`
et ouvrir `http://127.0.0.1:8770/?dev=1`.
Comparer avec `python native-viewer/benchmarks/verify-edge-opacity.py <dossier-evidence>`
(Pillow et NumPy). Le test suppose cette caméra et l'ancienne référence native.
