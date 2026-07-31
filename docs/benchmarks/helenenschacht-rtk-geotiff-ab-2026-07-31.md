# Benchmark A/B RTK du GeoTIFF — Helenenschacht

- Date : 2026-07-31
- Révision de base : `c8d0464` (arbre de travail audité non commité)
- Dataset : `odm_data_helenenschacht`
- Images : 176/176, Autel XT705
- CRS : `EPSG:32633` (WGS 84 / UTM 33N)
- Contrôle indépendant : 5 GCP, 35 observations image

## Verdict

Le raffinement RTK **ne démontre pas d'amélioration planimétrique globale** du
GeoTIFF face aux cinq GCP indépendants. Il réduit l'erreur horizontale moyenne
de 1,73 mm et la médiane de 4,69 mm, mais la RMSE augmente de 0,53 mm, le
maximum augmente de 1,23 mm et seulement 2 GCP sur 5 s'améliorent. L'intervalle
bootstrap apparié à 95 % du gain moyen est `[-11,22 ; +6,04] mm` et traverse
zéro : le petit gain moyen observé n'est pas robuste sur cet échantillon.

En revanche, après correction de trois défauts du renderer de hauteur, le
raffinement RTK améliore nettement la verticale du DSM : MAE de 21,72 à
15,54 cm (-28,5 %), RMSE de 23,06 à 16,10 cm (-30,2 %) et maximum de 30,19 à
18,23 cm (-39,6 %). L'intervalle bootstrap à 95 % du gain moyen absolu est
`[2,56 ; 10,10] cm`. Ce résultat vertical est prometteur, mais reste limité à
cinq points d'un même site.

## Protocole contrôlé

Les deux variantes partent exactement du même sparse historique et de la même
base COLMAP :

- source : `/home/olivier/droneai-workspaces/helenenschacht-benchmark/05_quality_gps_sr_2400_ba2_retri` ;
- SHA-256 initial de `database.db` :
  `a740dea675df9b9ef5d0a6d7f60dbfb0c8621ed24987b24abc8897ff30349a73` ;
- A, témoin : alignement et undistortion sans raffinement des poses ;
- B, RTK : `pose_prior_mapper`, puis le même alignement et la même undistortion ;
- priors B : 176/176 issus des fichiers DJI MRK, écart-type horizontal médian
  de 1,88 cm et vertical médian de 2,62 cm ;
- les GCP ne participent à aucune optimisation : ils servent uniquement de
  checkpoints indépendants.

Les deux entraînements Gaussian utilisent la même recette : profil `balanced`
personnalisé, 30 000 itérations, plafond de 2 000 000 Gaussiennes, SH degré 3,
`data-factor=1`, largeur maximale 4096 px, tuilage 4, seed 42, GSD demandé de
5 mm et seuil canari SSIM diagnostique de 0,25.

Un essai exploratoire construit depuis un staging d'images différent a été
écarté. Il n'entre dans aucune métrique ci-dessous.

## Résultats

| Métrique | A sans raffinement | B avec RTK | Lecture |
|---|---:|---:|---|
| Points sparse | 27 535 | 27 683 | +148 |
| Reprojection sparse moyenne | 1,4258 px | 1,3624 px | RTK meilleur |
| Reprojection sparse médiane | 1,3899 px | 1,3906 px | quasi identique |
| Résidu GPS horizontal médian | 10,51 cm | 8,75 cm | RTK meilleur |
| Résidu GPS vertical médian | 4,02 cm | 3,52 cm | RTK meilleur |
| GCP horizontal moyen | 4,711 cm | 4,539 cm | -1,73 mm |
| GCP horizontal médian | 4,256 cm | 3,787 cm | -4,69 mm |
| GCP horizontal RMSE | 4,997 cm | 5,050 cm | +0,53 mm |
| GCP horizontal maximum | 7,579 cm | 7,703 cm | +1,23 mm |
| DSM vertical MAE corrigée | 21,72 cm | 15,54 cm | -28,5 % |
| DSM vertical RMSE corrigée | 23,06 cm | 16,10 cm | -30,2 % |
| DSM vertical maximum corrigé | 30,19 cm | 18,23 cm | -39,6 % |
| Canary PSNR | 19,4404 dB | 19,4669 dB | +0,0266 dB |
| Canary SSIM | 0,27620 | 0,27887 | +0,00266 |
| Durée Gaussian complète | 6371,7 s | 6304,5 s | comparable |

### Détail par checkpoint

Une différence négative est un gain de la variante RTK.

| GCP | Horizontal A | Horizontal B | Delta B-A | \|Z DSM\| A | \|Z DSM\| B | Delta B-A |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 3,21 cm | 1,26 cm | -1,95 cm | 7,1 cm | 7,1 cm | 0,0 cm |
| 2 | 3,07 cm | 3,79 cm | +0,71 cm | 30,2 cm | 17,3 cm | -12,9 cm |
| 3 | 5,43 cm | 6,16 cm | +0,73 cm | 24,7 cm | 18,2 cm | -6,5 cm |
| 4 | 7,58 cm | 7,70 cm | +0,12 cm | 24,3 cm | 18,0 cm | -6,3 cm |
| 5 | 4,26 cm | 3,78 cm | -0,48 cm | 22,3 cm | 17,0 cm | -5,3 cm |

La position planimétrique du GeoTIFF est celle de la reconstruction alignée.
Les superpositions visuelles confirment que le centre triangulé indépendant
tombe sur l'intersection des quatre cases des cibles dans les rasters. La
précision de lecture directe reste toutefois bornée par le lissage du rendu à
5 mm et par les erreurs de pointage/reprojection de plusieurs pixels.

## Défauts du DSM découverts et corrigés

Les anciens fichiers `orthomosaic.ultra-5mm.height.tif` sont invalides pour
l'altimétrie et ne doivent pas être utilisés. Trois erreurs se cumulaient :

1. la translation Z du Sim3, conservée en float64 pour la précision, était
   ajoutée à l'origine X/Y mais oubliée dans le raster de hauteur ;
2. le CUDA écrivait une profondeur prémultipliée par l'opacité, sans division
   par l'opacité accumulée, ce qui relevait artificiellement les pixels
   semi-transparents de plusieurs mètres ;
3. une profondeur nulle sans couverture devenait l'altitude de la caméra au
   lieu d'une valeur nodata.

Le renderer normalise désormais la profondeur, convertit les pixels non
couverts en `NaN`, écrit `nodata=NaN` et applique exactement une origine
verticale : `t.z` du Sim3, ou l'origine GPS/EXIF du chemin PCA. Il ne translate
plus la surface du sol vers l'altitude moyenne du drone.

Les DSM corrigés ont été rerendus depuis les checkpoints terminés, sans nouvel
entraînement. Les RGB corrigés ont le même CRS, la même transformation et les
mêmes dimensions que les originaux ; 2 000 pixels pseudo-aléatoires par
variante ont été comparés à l'identique.

## Artefacts

- témoin :
  `/home/olivier/droneai-workspaces/helenenschacht-rtk-geotiff-benchmark/baseline_from_sparse05/orthomosaic.ultra-5mm.tif` ;
- RTK :
  `/home/olivier/droneai-workspaces/helenenschacht-rtk-geotiff-benchmark/rtk_from_sparse05/orthomosaic.ultra-5mm.tif` ;
- DSM témoin corrigé :
  `/home/olivier/droneai-workspaces/helenenschacht-rtk-geotiff-benchmark/baseline_from_sparse05/orthomosaic.ultra-5mm.heightfix.height.tif` ;
- DSM RTK corrigé :
  `/home/olivier/droneai-workspaces/helenenschacht-rtk-geotiff-benchmark/rtk_from_sparse05/orthomosaic.ultra-5mm.heightfix.height.tif` ;
- métriques GCP : `gcp_checkpoints.json` dans chaque workspace ;
- contrôles visuels :
  `/home/olivier/droneai-workspaces/helenenschacht-rtk-geotiff-benchmark/inspection_ab`.

Emprises RGB :

- A : 28 077 × 31 229 px,
  `[610789.0552, 5277628.9108, 610929.4402, 5277785.0558]` ;
- B : 27 318 × 31 352 px,
  `[610792.1652, 5277629.2022, 610928.7552, 5277785.9622]`.

La différence d'emprise, dépendante des extrêmes du modèle filtré, est grande
devant le signal centimétrique recherché. Elle n'exclut aucun GCP mais constitue
une faiblesse supplémentaire du découpage automatique actuel.

Autre facteur de confusion : l'échelle Sim3 vaut environ 8,909 pour A et 0,999
pour B. DroneGS entraîne en coordonnées COLMAP locales avant d'appliquer le
Sim3 ; les pas d'optimisation spatiaux absolus ne représentent donc pas
strictement la même distance physique. Les trajectoires Gaussian et les
métriques held-out sont très proches dans cet essai, ce qui ne révèle pas de
biais majeur, mais une future comparaison causale stricte devra normaliser le
modèle en unités métriques avant l'entraînement ou mettre les hyperparamètres
spatiaux à l'échelle.

## Vérifications et limites

- 8 tests unitaires CPU du nouveau référencement vertical : réussis ;
- compilation Python des modules touchés : réussie ;
- tests GPU `test_render_small`, `test_depth_is_normalized_by_accumulated_opacity`
  et `test_write_with_height` : 3 réussis, y compris la compilation du kernel
  CUDA et la vérification numérique de la profondeur ;
- seulement 5 checkpoints, regroupés sur un seul site ;
- 35 annotations image, avec reprojection triangulée moyenne d'environ 4,9 px
  pour A et 4,7 px pour B ;
- le bootstrap mesure l'incertitude sur ces cinq points, pas la généralisation
  à d'autres vols, capteurs ou géométries.

## Recommandation

Activer le raffinement RTK quand la stabilité verticale/DSM est importante,
mais **ne pas annoncer de gain planimétrique RTK sur Helenenschacht** au vu de
ce bench. Conserver les GCP comme contrôle indépendant et répéter l'essai avec
plus de checkpoints, mieux répartis en plan et en altitude. Une emprise de
rendu imposée et identique aux deux variantes rendrait aussi les comparaisons
de bord plus strictes.

## Bench sparse de résolution et de densité de features

Le bench complémentaire a été exécuté dans des workspaces neufs. Pour isoler
l'effet RTK de la variabilité GPU de SIFT et du matching, chaque mesure RTK
causale repart exactement du sparse et de la base de sa variante sans RTK. Les
GCP restent exclusivement des checkpoints et ne participent ni au bundle
adjustment, ni à l'alignement.

| Profil | Temps sparse | Points | RMSE H | RMSE V | RMSE 3D |
|---|---:|---:|---:|---:|---:|
| 2400/4096, sans guided, sans RTK | 192,9 s | 24 981 | 5,085 cm | 26,683 cm | 27,163 cm |
| 2400/4096, sans guided, RTK causal | +≈3 s | — | 4,997 cm | 24,175 cm | 24,686 cm |
| 3200/8192, guided, sans RTK | 321,9 s | 56 965 | 5,503 cm | 23,019 cm | 23,667 cm |
| 3200/8192, guided, RTK causal, Cauchy 7,82 | +≈4 s | 58 442 | 6,430 cm | 16,125 cm | 17,360 cm |
| 3200/8192, guided, RTK causal, Cauchy 62,56 | +≈6 s CPU | — | 6,320 cm | **15,741 cm** | **16,962 cm** |
| 4096/8192, guided, sans RTK | 349,6 s | 66 552 | 6,214 cm | 19,775 cm | 20,728 cm |
| 4096/8192, guided, RTK causal | +≈3 s | — | 5,260 cm | 19,533 cm | 20,229 cm |

Le profil 3200+RTK donne la meilleure RMSE 3D observée (-31,3 % face au
2400+RTK à l'échelle robuste générale 7,82), essentiellement grâce à la
verticale, mais sa RMSE horizontale est plus mauvaise (+26,5 %). Il constitue
donc le meilleur compromis pour un
DSM/volume 3D, pas pour une livraison strictement planimétrique. Le profil
2400+RTK reste recommandé pour le XY et termine environ 40 % plus vite.

Deux ablations empêchent de tirer de mauvaises conclusions :

- à 4096 px, revenir aux seuils de retriangulation historiques 15 px/1° donne
  6,221 cm en H et 19,465 cm en V, soit aucune amélioration significative face
  aux seuils expérimentaux 4 px/1,5° ; les seuils stricts sont donc abandonnés ;
- activer le matching guidé à 2400 px produit un échec géométrique majeur
  (RMSE H 1,760 m, V 2,561 m). Il reste désactivé dans le profil planimétrique.

Un balayage causal de l'échelle Cauchy des priors RTK (3,91 ; 7,82 ; 15,64 ;
31,28 ; 62,56) montre une amélioration 3D monotone sur le profil 3200. Le
palier 62,56 améliore aussi la 3D des reconstructions 2400 et 4096, mais y
dégrade légèrement le XY. Ce réglage plus confiant n'est donc pas le nouveau
défaut général : il est exposé comme paramètre, inclus dans l'empreinte de
cache, et appliqué uniquement par le preset 3D.

La répétabilité exacte n'est pas garantie par le seul seed GLOMAP : deux
extractions/matchings SIFT CUDA frais ont donné des graphes légèrement
différents. Le verdict RTK repose donc sur les paires causales qui partagent le
même sparse initial ; la répétition RTK construite indépendamment n'est pas
utilisée pour attribuer un gain au raffinement.

Le preset `Précision 3D · RTK` est maintenant réglé à 3 200 px, 8 192 features,
première octave 0, matching guidé, seed 42, deux passes BA, seuils historiques
15 px/1° et `pose_prior_mapper` de 25 itérations avec les covariances RTK et
une échelle Cauchy 62,56. Les autres presets conservent 7,82. GLOMAP n'utilise
toujours pas directement les positions RTK dans son positionnement global.

Ces écarts reposent sur seulement cinq checkpoints d'un seul site. Ils
justifient un preset spécialisé, mais pas une promesse générale de précision.

## Validation GeoTIFF du profil 3D retenu

Le profil final 3200/8192 guided + RTK Cauchy 62,56 a ensuite été exécuté
jusqu'au GeoTIFF : 30 000 itérations DroneGS, plafond 2 millions, SH3,
`data-factor=1`, largeur 3200, seed 42 et GSD 5 mm. Le sparse RTK, le Sim3 et
les images undistordues appartiennent au même workspace causal ; les GCP n'ont
été lus qu'après l'écriture du raster.

| Mesure finale | Profil 3200+RTK-62,56 | Ancien RTK 2400 | Ancien témoin 2400 |
|---|---:|---:|---:|
| GCP horizontal RMSE sparse | 6,320 cm | 5,050 cm | 4,997 cm |
| DSM vertical MAE | **10,354 cm** | 15,54 cm | 21,72 cm |
| DSM vertical RMSE | **11,444 cm** | 16,10 cm | 23,06 cm |
| DSM vertical maximum | **16,246 cm** | 18,23 cm | 30,19 cm |
| Canary PSNR | 19,3654 dB | 19,4669 dB | 19,4404 dB |
| Canary SSIM | **0,29295** | 0,27887 | 0,27620 |

Face à l'ancien GeoTIFF RTK, le nouveau profil réduit la MAE DSM de 33,4 % et
la RMSE de 28,9 %. Face au témoin sans raffinement, la RMSE verticale baisse de
50,4 %. En contrepartie, la RMSE horizontale sparse est moins bonne de 1,27 cm
que l'ancien RTK : le profil 3200 reste un choix 3D/DSM, pas le meilleur choix
planimétrique.

Le modèle final contient 1 853 537 Gaussiennes avant filtrage et 1 812 454
après filtrage. L'entraînement a duré 6 161,4 s et la reprise contrôlée +
filtrage + rendu 228,1 s, soit environ 6 389,5 s (106,5 min). Le GeoTIFF mesure
27 534 × 29 932 px, à 0,005 m/px, en EPSG:32633, avec l'emprise
`[610794,1344 ; 5277632,8539 ; 610931,8044 ; 5277782,5139]`. Le DSM est en
float32 avec `nodata=NaN`.

Artefacts :

- RGB :
  `/home/olivier/droneai-workspaces/helenenschacht-precision-ab-2026-07-31/compromise-3200-rtk62-geotiff/orthomosaic.precision-3200-rtk62-5mm.tif` ;
- DSM :
  `/home/olivier/droneai-workspaces/helenenschacht-precision-ab-2026-07-31/compromise-3200-rtk62-geotiff/orthomosaic.precision-3200-rtk62-5mm.height.tif` ;
- contrôle DSM :
  `/home/olivier/droneai-workspaces/helenenschacht-precision-ab-2026-07-31/compromise-3200-rtk62-geotiff/dsm_checkpoints.json`.

Cette passe a aussi révélé un défaut de reprise : les seuils de canari étaient
traités comme des paramètres d'entraînement. Relâcher un seuil après un échec
déplaçait donc le résultat terminé et relançait inutilement 30 000 itérations.
Le dépôt réévalue désormais PSNR/SSIM depuis le manifeste existant, sans
retraining ; un échec de seuil compatible reste un échec rapide, et le gros
checkpoint optimiseur est supprimé après promotion réussie. Le seuil SSIM
production passe de 0,35 à 0,25, valeur atteinte par les trois GeoTIFF complets
de cette campagne.

## Validation croisée OUR_WORKFLOW / Metashape

Le 1er août 2026, le même jeu de cinq GCP a été mesuré dans l'orthomosaïque
Metashape `TEST_ORTHO.tif` puis dans le rendu DroneGS final. Les centres
Metashape sont les jonctions visibles des damiers. Les centres DroneGS, plus
flous, sont reconstruits sur une grille UTM commune à 5 mm par consensus de
corrélations multi-flou en luminance, chromaticité et gradients, puis contrôlés
sur les jonctions encore visibles.

| Produit | Erreur H moyenne | RMSE H | Maximum H |
|---|---:|---:|---:|
| Metashape orthomosaic, centres visibles | 14,21 cm | 14,88 cm | 21,66 cm |
| DroneGS orthomosaic, centres reconstruits | **5,82 cm** | **6,24 cm** | **10,20 cm** |
| COLMAP sparse, triangulation des 35 observations | 5,94 cm | 6,32 cm | 8,44 cm |

La concordance entre DroneGS et son sparse source montre que le rendu conserve
la précision planimétrique globale, malgré des déplacements locaux de centre
d'environ 2 à 4,4 cm dus au splatting, au flou et à l'interpolation. Les GCP 2
et 3 ont respectivement environ 3 cm et 2,5 cm d'incertitude de lecture dans
DroneGS à cause de la saturation ou de la perte du contraste central.

Le projet Metashape ne contenait aucun marqueur GCP : ses 176 références caméra
RTK étaient actives et les GCP restaient indépendants. Son sparse est plus
dense, 192 850 points contre 58 454, mais sa triangulation des annotations GCP
mesure 16,01 cm de RMSE horizontale et un biais vertical moyen de -2,765 m.
Cette densité supplémentaire ne s'est donc pas traduite par une meilleure
exactitude absolue.

Le protocole, les coordonnées par point, les limites et le plan d'amélioration
sont détaillés dans
[`helenenschacht-our-workflow-vs-metashape-2026-08-01.md`](helenenschacht-our-workflow-vs-metashape-2026-08-01.md),
avec les valeurs machine dans le fichier JSON homonyme.
