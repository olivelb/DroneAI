# Benchmark DroneGS ultra 5 mm — Helenenschacht

- Date : 2026-07-30
- Révision de base : `3405990`
- Dataset : `odm_data_helenenschacht`
- Matériel : NVIDIA RTX 4070 Laptop, 8 Gio
- CRS de sortie : `EPSG:32633` (WGS 84 / UTM 33N)

## Décision

Ce profil produit le rendu local le plus détaillé testé, mais il ne remplace
pas `DRONEGS_PRODUCTION_PROFILE_V1` :

- son PSNR held-out passe le seuil de production ;
- son SSIM held-out échoue ;
- il coûte environ 10,6 fois le temps d'entraînement du profil `balanced`
  mesuré précédemment sur cette scène ;
- le GSD demandé de 5 mm ne réduit pas l'erreur planimétrique de l'alignement,
  qui reste à 5,0 cm de RMSE sur les cinq checkpoints GCP.

Le résultat est donc un artefact diagnostique haute définition, pas une sortie
qualifiée production ni un relevé certifié à 5 mm.

## Configuration

Le meilleur alignement sparse du
[benchmark RTK/GCP](helenenschacht-alignment-quality-2026-07-29.md) a été
réutilisé sans modifier ses poses :

- 176 images Autel XT705, 5472 × 3648 pixels ;
- images undistordues utiles de 5435 × 3623 pixels ;
- GLOMAP, graphe GPS, `SIMPLE_RADIAL`, SIFT CUDA brute-force ;
- 2400 px, 4096 features, deux passes BA et retriangulation globale ;
- 176/176 caméras, 27 535 points, reprojection interne médiane de 1,3899 px.

L'entraînement est une recette `custom` dérivée du profil de production :

| Paramètre | Valeur |
|---|---:|
| Optimiseur / raster | `reference-absolute` / `fastgs` |
| Pruning | `spatial-bounds` |
| SH | degré 3, activation tous les 1000 pas |
| Images | facteur 1, largeur maximale 4096 px |
| Itérations | 30 000 |
| Plafond | 2 000 000 Gaussiennes |
| Tuilage | 4 |
| Cooldown / finish photométrique | 1000 / 1000 |
| Objectif final | 100 % MSE |
| Seed / split | 42 / modulo 8 |
| Seuils production | 18,0 dB PSNR / 0,35 SSIM |
| Résolution du rendu | 0,005 m/pixel |

Le runner local reclasse automatiquement cette combinaison en `custom`,
car ses paramètres d'entraînement diffèrent de la recette immuable V1. Une
commande équivalente est :

```bash
./tools/run_local_gaussian.sh WORKSPACE \
  --profile balanced \
  --iterations 30000 \
  --cap-max 2000000 \
  --data-factor 1 \
  --max-width 4096 \
  --resolution 0.005 \
  --output /workspace/orthomosaic.ultra-5mm.tif \
  --verbose
```

`run_local_gaussian.sh` monte le workspace hôte dans le conteneur sous
`/workspace` ; un chemin `--output` explicite doit donc utiliser ce chemin
conteneur.

Le plafond local de largeur est actuellement de 4096 px. La largeur utile de
5435 px n'est donc pas injectée intégralement dans le trainer, même avec
`data-factor=1`.

## Temps et occupation du modèle

| Étape | Temps |
|---|---:|
| Entraînement mur | 6404,35 s (1 h 46 min 44 s) |
| Calcul d'entraînement pur | 5265,21 s |
| Chargement/attente images | 1129,43 s |
| Réutilisation, filtrage et rendu GeoTIFF | 121,77 s |
| Conversion COG RGB + hauteur | environ 86 s |

Le modèle atteint le plafond de 2 000 000 de Gaussiennes, termine avec
1 738 760 Gaussiennes, puis en conserve 1 697 495 après géo-alignement et
filtrage de publication.

## Qualification held-out

L'évaluation utilise 22 images réservées :

| Métrique | Ultra 5 mm | Seuil production | Verdict |
|---|---:|---:|---|
| PSNR | 19,4342 dB | 18,0 dB | réussi |
| SSIM | 0,2763 | 0,35 | échoué |

Le premier passage a été conservé comme échec production. Un second passage
a abaissé explicitement le seuil SSIM à 0,25 pour permettre uniquement le
rendu diagnostique. Abaisser un seuil de canari ne corrige pas le modèle et ne
doit jamais transformer l'artefact en sortie qualifiée.

Comparaison avec les essais antérieurs sur le même workspace :

| Profil | PSNR | SSIM | Temps |
|---|---:|---:|---:|
| `low-memory`, 5000 pas, 500 k, facteur 4 | 19,520 | 0,2854 | non comparé ici |
| `balanced`, 15 000 pas, 1,5 M, facteur 4 | 19,355 | 0,3319 | 403,9 s |
| `custom` ultra, 30 000 pas, 2 M, facteur 1 | 19,434 | 0,2763 | 6404,35 s |

L'ultra gagne seulement 0,079 dB de PSNR et perd 0,0556 de SSIM face au
`balanced`. Davantage de pixels, d'itérations et de Gaussiennes n'améliorent
donc pas la généralisation held-out de cette scène.

## COG et géoréférencement

Les COG RGB et hauteur ont été validés avec :

- dimensions : 28 243 × 30 960 pixels ;
- taille de pixel : 0,005 × -0,005 m ;
- blocs : 512 × 512 ;
- overviews : 2, 4, 8, 16, 32 et 64 ;
- emprise native :
  `[610789.4562606259, 5277629.624639729, 610930.6712606258, 5277784.424639729]`.

Le COG RGB occupe environ 1,5 Go et le COG de hauteur environ 2,8 Go. Les
overviews permettent au viewer et à QGIS de ne lire que les niveaux et fenêtres
nécessaires au zoom courant.

## Contrôle GCP sur le rendu

L'alignement sparse étant inchangé, les résidus planimétriques restent :

| GCP | Erreur horizontale | Écart à 5 mm/pixel |
|---|---:|---:|
| 1 | 3,21 cm | 6,43 px |
| 2 | 3,07 cm | 6,15 px |
| 3 | 5,43 cm | 10,87 px |
| 4 | 7,58 cm | 15,16 px |
| 5 | 4,26 cm | 8,51 px |

La RMSE horizontale est de **4,997 cm**. Le rendu à 5 mm/pixel représente
l'erreur avec davantage de pixels ; il ne rend pas les poses ou les
intrinsèques plus exactes. Les zooms montrent plus de détails, mais aussi des
cibles lissées, surexposées ou fantômes, notamment sur les GCP 2 et 4.

## Suite recommandée

Pour réduire l'erreur réelle plutôt que le seul pas d'échantillonnage :

1. optimiser poses et intrinsèques avec des contraintes GCP correctement
   pondérées, en gardant des checkpoints indépendants ;
2. distinguer explicitement GCP de contrôle et GCP d'ajustement ;
3. qualifier une stratégie photométrique sur plusieurs seeds et scènes ;
4. conserver les seuils stricts tant que le profil n'a pas passé les
   répétitions ALBAGNAC, SAVERES et Helenenschacht ;
5. ne créer un profil production V2 qu'après amélioration conjointe de la
   géométrie, du SSIM et de la répétabilité.
