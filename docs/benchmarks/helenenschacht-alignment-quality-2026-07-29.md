# Benchmark d’alignement RTK/GCP — Helenenschacht

- Date : 2026-07-29
- Commit testé : `0ebb86b`
- Dataset : `odm_data_helenenschacht`
CRS planimétrique d’évaluation : `EPSG:32633` (WGS 84 / UTM 33N)

## Conclusion opérationnelle

Pour un relevé évalué **uniquement en planimétrie**, le meilleur candidat
mesuré et désormais retenu comme profil par défaut est :

- GLOMAP ;
- graphe de paires GPS ;
- SIFT CUDA + brute-force CUDA ;
- `SIMPLE_RADIAL` partagé par la mission ;
- résolution d’alignement 2400 px et 4096 features ;
- deux passes de BA globale, 50 itérations Ceres au maximum par passe ;
- retriangulation finale activée.

Ce profil enregistre 176/176 images en 174 s, produit 27 535 points et atteint
5,0 cm de RMSE horizontale sur les cinq GCP utilisés exclusivement comme
checkpoints, avec une erreur horizontale maximale de 7,58 cm.

Le profil 1600 px, 2048 features, BA1 sans retriangulation reste le preset
**rapide** : 176/176 images en 133–161 s et 8,6–9,4 cm de RMSE horizontale sur
trois répétitions. Il reste préférable lorsque la durée sur une très grande
mission est prioritaire.

Cette décision ne certifie pas l’altimétrie : le datum vertical du dataset
n’est pas déclaré et la verticale a été exclue du critère demandé. La
retriangulation introduit ici un écart vertical plus élevé. Tout contrat
altimétrique doit donc utiliser un CRS vertical explicite et des checkpoints
indépendants avant d’adopter ce même profil.

## Protocole

### Dataset

- 176 images `MAX_0002.JPG` à `MAX_0177.JPG` ;
- 5472 × 3648 px, Autel Robotics XT705 / Evo II Pro RTK ;
- 2,55 Go d’images ;
- 176 enregistrements MRK ;
- observation RINEX 3.04 multi-GNSS à 5 Hz sur toute la mission ;
- 5 GCP et 35 observations image, soit 7 observations par point ;
- empreinte du vol : environ 92,5 × 84,8 m ;
- emprise GCP : environ 29,1 × 41,6 m, soit 15 % de l’emprise rectangulaire
  du vol.

Empreintes de reproductibilité :

- manifeste SHA-256 des images :
  `66df86d94a3a284077e79a0c42b20272fadb47bb079eb3c65e19ac43101f5a93` ;
- `gcp_list.txt` :
  `df3390a82c5a638d54046c08d8a6afd2a029a482c026627a58a7afbb5eb61dd5` ;
- MRK :
  `68968937066cb7e91a0f960bcd7b2c1946b47d0c6ddea4d3a6a92d521c532e84`.

### Matériel

- Intel Core i9-13900H, 20 vCPU exposés à WSL ;
- 23 Gio de RAM WSL ;
- NVIDIA RTX 4070 Laptop, 8188 Mio ;
- pilote 610.62 ;
- Ubuntu WSL2, Docker 29.3.0 ;
- COLMAP 4.1.1, CUDA 12.8.

### Mesure GCP

Les GCP n’ont été utilisés ni par le mapper, ni par le BA, ni par
l’alignement géographique. Ils restent donc des checkpoints indépendants.

Pour chaque GCP :

1. le pixel annoté est ramené en rayon caméra avec le modèle de distorsion
   reconstruit ;
2. les 7 rayons sont intersectés par moindres carrés ;
3. un rejet robuste MAD, avec seuil minimal de 5 px, élimine uniquement une
   observation clairement aberrante ;
4. le point triangulé est comparé à la coordonnée géodésique transformée
   directement de `EPSG:4326` vers `EPSG:32633` ;
5. les résidus de reprojection du point levé et du point triangulé sont
   enregistrés.

Le calcul utilise les coordonnées géographiques complètes de
`gcp_list.txt`. Le CSV UTM annexe est arrondi au centimètre et diffère de la
transformation complète de 2,6 cm en moyenne ; il n’est pas utilisé.

### Matrice testée

La matrice est une étude systématique « un facteur à la fois », complétée par
des profils combinés. Elle couvre :

- 1600 et 2400 px ;
- une et deux passes de BA ;
- retriangulation activée/désactivée ;
- `SIMPLE_RADIAL` et `OPENCV` ;
- graphes GPS, spatial, séquentiel et exhaustif ;
- GLOMAP, CASPAR et CERES ;
- SIFT brute-force, SIFT LightGlue et ALIKED LightGlue ;
- RTK covariance-aware ;
- focale verrouillée et intrinsics complètement verrouillés ;
- trois répétitions du profil de référence.

Les temps des profils 14 à 16 sont des expériences chaudes réutilisant la
base de matches du profil 1. Ils ne sont donc pas comparables aux temps
end-to-end des profils 1 à 13 et 17 à 18.

## Résultats

`H` et `V` sont les RMSE checkpoint horizontale et verticale. `Px levé` est
la RMSE de reprojection des coordonnées levées, non celle des points du modèle.

| # | Profil | Temps | Caméras | Points | Reproj. interne médiane | H | V | Px levé |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | GPS, SR, 1600, BA1 | 161 s | 176 | 16 935 | 2,318 px | 9,4 cm | 9,6 cm | 9,86 px |
| 2 | GPS, SR, 2400, BA1 | 172 s | 176 | 31 600 | 1,990 px | 8,1 cm | 13,3 cm | 9,00 px |
| 3 | GPS, SR, 1600, BA2 | 152 s | 176 | 16 793 | 2,303 px | 8,1 cm | 10,8 cm | 9,03 px |
| 4 | GPS, SR, 1600, BA1 + retri | 155 s | 176 | 12 570 | 1,412 px | 5,3 cm | 19,0 cm | 7,60 px |
| 5 | GPS, SR, 2400, BA2 + retri | 174 s | 176 | 27 535 | 1,390 px | 5,0 cm | 27,0 cm | 7,72 px |
| 6 | GPS, OPENCV, 2400, BA2 + retri | 185 s | 176 | 30 397 | 1,308 px | 8,7 cm | 28,5 cm | 8,83 px |
| 7 | Exhaustif, SR, 1600, BA1 | 159 s | 176 | 16 567 | 2,307 px | 9,1 cm | 9,8 cm | 9,67 px |
| 8 | Spatial, SR, 1600, BA1 | 144 s | 176 | 16 665 | 2,304 px | 9,0 cm | 10,4 cm | 9,57 px |
| 9 | Séquentiel, SR, 1600, BA1 | 136 s | 176 | 13 412 | 1,540 px | 8,3 cm | 39,2 cm | 9,29 px |
| 10 | CASPAR, GPS, SR, 1600 | 277 s | 176 | 20 281 | 1,552 px | 9,0 cm | 28,0 cm | 10,20 px |
| 11 | CERES, GPS, SR, 1600 | 175 s | 175 | 20 464 | 1,410 px | 8,0 cm | 38,8 cm | 10,23 px |
| 12 | SIFT LightGlue, GPS, SR, 1600 | 782 s | 175 | 23 117 | 3,436 px | 12,0 cm | 9,4 cm | 11,76 px |
| 13 | ALIKED LightGlue, GPS, SR, 1600 | 608 s | 176 | 26 388 | 1,846 px | 5,6 cm | 29,3 cm | 8,13 px |
| 14 | Adaptateur MRK + raffinement RTK chaud | 6 s | 176 | 14 940 | 2,044 px | 9,6 cm | 9,1 cm | 9,90 px |
| 15 | Focale XMP verrouillée, distorsion libre | 20 s | 176 | 16 935 | 2,701 px | 84,6 cm | 39,3 cm | 74,27 px |
| 16 | Focale et distorsion verrouillées | 15 s | 176 | 16 935 | 3,167 px | 24,5 cm | 190,8 cm | 32,59 px |
| 17 | Répétition référence 2 | 133 s | 176 | 16 140 | 2,330 px | 8,7 cm | 10,7 cm | 9,39 px |
| 18 | Répétition référence 3 | 136 s | 176 | 16 507 | 2,300 px | 8,6 cm | 10,8 cm | 9,34 px |

Le fichier machine-readable complet, incluant les timings de chaque commande,
les paramètres caméra et les métriques GPS, est conservé dans :

`/home/olivier/droneai-workspaces/helenenschacht-benchmark/benchmark_summary.json`.

## Analyse des écarts

### Variabilité du profil de référence

Sur les trois répétitions :

- temps moyen 143 s, plage 133–161 s ;
- points moyens 16 527, plage 16 140–16 935 ;
- RMSE H moyenne 8,9 cm, plage 8,6–9,4 cm ;
- RMSE V moyenne 10,4 cm, plage 9,6–10,8 cm ;
- RMSE 3D moyenne 13,7 cm, plage 13,5–13,8 cm.

Une amélioration inférieure à environ 1 cm sur H ou V ne peut pas être
considérée significative sur une exécution unique de ce dataset.

### Résolution et nombre de BA

2400 px double presque la densité et améliore les erreurs de reprojection.
BA2 seul reste dans la variabilité du profil de référence, mais la combinaison
2400 px + BA2 + retriangulation donne la meilleure RMSE horizontale mesurée.
Sur ALBAGNAC, la seconde BA ajoute plusieurs minutes : le preset rapide reste
disponible pour préserver l’objectif de durée.

### Retriangulation

La retriangulation améliore la planimétrie et la reprojection : 5,3 cm de
RMSE H à 1600 px et 5,0 cm à 2400 px. Elle dégrade toutefois V de 9,6 à
19,0 cm à 1600 px et jusqu’à 27,0 cm à 2400 px. Cette réserve est conservée
dans l’interface et impose une validation séparée pour tout usage altimétrique.

### Modèle caméra

Le profil `OPENCV` dispose de plus de degrés de liberté et obtient la meilleure
reprojection interne (1,308 px), sans améliorer les checkpoints. Sa RMSE
verticale atteint 28,5 cm. Il ne doit être proposé qu’avec une calibration
validée et une couverture géométrique suffisante.

Le XMP annonce une focale calibrée de 4404,17 px et COLMAP initialise à
4407,66 px. Les meilleurs modèles libres convergent autour de 4197–4204 px.
Verrouiller la valeur XMP produit des erreurs de 25 à 191 cm, ou 85 cm quand
la distorsion reste libre. La calibration embarquée est donc un prior, pas
une vérité à figer.

### Graphe de matches

L’exhaustif ajoute seulement 11 s sur 176 images, mais n’améliore la RMSE 3D
que de moins d’un millimètre par rapport à la meilleure répétition. Ce coût
devient quadratique sur ALBAGNAC.

Le séquentiel conserve 176 caméras et une bonne reprojection, mais perd les
liaisons transversales nécessaires à la rigidité en Z : sa RMSE V atteint
39,2 cm. Le graphe GPS borné reste le choix le plus sûr et scalable.

### Moteur

Le mapping seul prend :

- 17,1 s avec GLOMAP ;
- 55,1 s avec CERES ;
- 152,5 s avec CASPAR.

CASPAR et CERES augmentent le nombre de points et diminuent l’erreur interne,
mais détériorent V à 28,0 et 38,8 cm. CERES n’enregistre que 175 images.
CASPAR doit rester le fallback GPU incrémental de GLOMAP, et CERES le fallback
des modèles non supportés par CASPAR.

### Frontends neuronaux

SIFT LightGlue coûte 654,6 s de matching contre 4,2 s pour SIFT brute-force,
perd une caméra et dégrade H.

ALIKED LightGlue produit 26 388 points et 5,6 cm en H, mais coûte 608 s, occupe
environ 7,8 Gio pendant l’extraction et porte V à 29,3 cm. Il peut rester un
mode expérimental pour les scènes où SIFT échoue, pas un preset de production.

### RTK

Les images contiennent déjà exactement les positions RTK :

- 176/176 XMP ont `RtkFlag="50"` ;
- 176/176 ont `RtkStdLon`, `RtkStdLat` et `RtkStdHgt` ;
- incertitude horizontale XMP/MRK médiane : 1,381 cm ;
- incertitude verticale XMP/MRK médiane : 2,623 cm ;
- écart EXIF–MRK horizontal médian : 0,000004 m ;
- écart EXIF–MRK horizontal maximal : 0,000009 m ;
- écart vertical EXIF–MRK : ±0,00049 m au maximum.

Le raffinement RTK adapté prend 1,97 s. Il fait passer la RMSE V de 9,63 à
9,05 cm, mais H de 9,41 à 9,58 cm et réduit le modèle de 16 935 à 14 940
points. Le gain 3D de 13,47 à 13,18 cm est inférieur à la variabilité observée.
Le raffinement doit donc être conservé sous forme conditionnelle avec
comparaison avant/après et rollback, pas accepté automatiquement.

## Défauts découverts

### P0 — Les RTK Autel ne sont pas reconnus

Le préflight actuel classe les 176 positions comme `exif`, sans covariance.
Deux causes se cumulent :

1. `image_sequence_number()` attend un nom DJI contenant un suffixe après le
   numéro ; il ne reconnaît aucun `MAX_0002.JPG` ;
2. le résolveur exige que le MRK soit dans le même dossier que les images,
   alors qu’il se trouve dans `RTK_Data/`.

Mesure directe du code actuel :

```text
images=176, sequence_matches=0, mrk_overrides=0
```

`--gps-quality rtk` ne déclenche pas non plus d’avertissement lorsque la liste
d’incertitudes est vide. Le pipeline ne découvre l’erreur qu’après
l’extraction des features, dans `inject_database_pose_priors()`, avec :

```text
RTK refinement requires at least three DJI MRK records with positive east,
north, and vertical standard deviations
```

Cette validation doit avoir lieu avant toute consommation GPU.

### P0 — Les métadonnées XMP RTK sont ignorées

Toutes les images contiennent les coordonnées, covariances, altitudes relative
et absolue, attitudes vol/gimbal et focale calibrée. Le préflight ne lit que
l’EXIF standard et perd ces données.

### P1 — Chaînes EXIF non nettoyées

Le fabricant et le modèle contiennent des NUL terminaux. `.strip()` ne les
supprime pas ; ils apparaissent donc dans les rapports et les regroupements de
capteurs.

### P1 — Datum vertical non déclaré

Le MRK marque la hauteur `Ellh`, mais `gcp_list.txt` ne déclare que
`EPSG:4326`, sans CRS vertical. Les hauteurs GCP semblent cohérentes avec
environ 50 m d’altitude relative, mais ce n’est pas une preuve de datum.
Toutes les valeurs V de ce rapport sont donc des diagnostics du dataset, pas
une certification altimétrique.

### P1 — Les métriques internes peuvent sélectionner un mauvais modèle

La reprojection, le nombre de points et même le résidu GPS ne suffisent pas à
détecter les biais verticaux vus avec le séquentiel, CASPAR, CERES, OPENCV et
ALIKED. Le pipeline doit promouvoir les checkpoints indépendants au rang de
quality gate.

## Plan d’intégration

### Lot 1 — Métadonnées RTK multi-constructeurs (P0)

1. Créer une abstraction `AerialMetadataProvider` et conserver
   `dji_metadata.py` comme façade compatible.
2. Ajouter un provider XMP DJI/Autel lisant :
   coordonnées, `RtkFlag`, trois écarts-types, altitudes, focale, attitudes
   vol/gimbal et date.
3. Ajouter les motifs de séquence DJI et Autel, dont `MAX_0002.JPG`.
4. Résoudre les sidecars par mission :
   même dossier, sous-dossier mission unique, puis correspondance
   numéro+timestamp+proximité GPS.
5. Refuser toute association ambiguë ; ne jamais choisir le premier MRK.
6. Appliquer la priorité :
   MRK corrigé > XMP RTK valide > EXIF avec erreur > EXIF simple.
7. Normaliser les covariances en ENU et conserver valeur brute, fournisseur,
   fichier source et règle de sélection.
8. Nettoyer NUL et espaces de toutes les chaînes EXIF/XMP.
9. Si RTK est explicitement demandé, échouer en préflight lorsque moins de
   95 % des images ont une covariance positive complète.
10. Afficher au dashboard la qualité réellement détectée, jamais seulement
    le choix demandé.

Tests d’acceptation :

- 176/176 images Helenenschacht reconnues RTK ;
- 176/176 covariances ;
- égalité MRK/XMP sous 1 mm ;
- ambiguïté multi-MRK testée ;
- échec avant feature extraction si RTK incomplet ;
- non-régression SAVERES et ALBAGNAC.

### Lot 2 — Quality gate GCP/checkpoint (P0)

1. Intégrer `tools/evaluate_gcp_checkpoints.py` au worker d’alignement.
2. Versionner en base les GCP, observations, CRS horizontal, CRS vertical et
   rôle `control`/`checkpoint`.
3. Interdire l’utilisation d’un checkpoint dans le BA.
4. Produire résidus par point, vecteurs d’erreur, reprojection par image,
   couverture et condition d’intersection.
5. Persister le rapport avec hash dataset/config/image.
6. Exposer au dashboard une couche GCP, un tableau triable et un zoom sur
   les erreurs.
7. Rendre les seuils dépendants du contrat projet : tolérance métrique ou
   multiple du GSD, sans seuil universel codé en dur.
8. Conserver le modèle précédent si le nouveau profil échoue au quality gate.

Avec seulement cinq points, la première intégration de GCP de contrôle devra
utiliser une validation leave-one-out en cinq folds. Deux checkpoints fixes
seraient insuffisants pour conclure statistiquement.

### Lot 3 — Presets d’alignement et dashboard (P0/P1)

Conserver quatre usages :

- **Relevé précis planimétrique (défaut)** : profil 5 ;
- **Production rapide** : profil 1 ;
- **Densité** : 2400 px, BA1, sans retriangulation ;
- **Calibré** : modèle caméra fourni et validé, options avancées ;
- **Expérimental** : OPENCV, retriangulation et frontends neuronaux avec
  avertissement explicite.

Montrer en premier : résolution, qualité GPS détectée, moteur, matcher,
caméras attendues, seuil checkpoint et espace disque. Placer BA, modèle
caméra, retriangulation, voisinage GPS et itérations dans un panneau avancé.

La retriangulation doit afficher : « peut améliorer H/reprojection tout en
dégradant V ». CASPAR et CERES doivent être présentés comme fallbacks, pas
comme modes plus précis.

### Lot 4 — Calibration et BA contrôlés (P1)

1. Conserver `SIMPLE_RADIAL`, centre principal fixe, focale et un coefficient
   radial libres au premier passage.
2. Enregistrer le drift focal et avertir au-delà d’un seuil configurable.
3. Ne jamais verrouiller la focale XMP sans benchmark checkpoint préalable.
4. Ajouter une passe de calibration optionnelle séparée du profil production.
5. Évaluer les modèles caméra par validation checkpoint, pas reprojection.
6. Grouper les intrinsics par capteur/focale/session et détecter les zooms.
7. Ajouter un BA GCP robuste :
   observations 2D, point 3D latent, prior levé avec covariance, loss robuste.
8. Maintenir strictement hors optimisation les checkpoints.
9. Exploiter attitudes XMP et altitude relative pour estimer
   lever-arm/bore-sight et éventuel décalage temporel.

### Lot 5 — Raffinement RTK conditionnel (P1)

1. Exécuter le raffinement seulement si le résidu initial dépasse la
   covariance attendue ou si le réseau est géométriquement faible.
2. Comparer modèle initial et raffiné sur checkpoints, complétude, points,
   reprojection et résidus GPS.
3. Rejeter automatiquement le raffinement s’il perd des caméras, dépasse une
   perte de densité configurée ou dégrade le score checkpoint.
4. Conserver les deux modèles et la décision dans les artefacts de reprise.
5. N’exécuter ni MRK ni RINEX comme simple « label RTK » : vérifier statut,
   covariance, continuité temporelle et taux de couverture.

### Lot 6 — Harness de non-régression (P1)

1. Transformer cette campagne en suite déclarative versionnée.
2. Fixer seeds quand disponibles et exécuter trois répétitions des petits cas.
3. Capturer temps par étape, pic VRAM, version binaire, hash image et config.
4. Gater :
   inscription, RMSE checkpoint, perte de points, temps et mémoire.
5. Ajouter Helenenschacht au CI nightly GPU ; conserver SAVERES comme grand
   test RTK et ALBAGNAC comme test de performance.
6. Tester aussi ortho/DSM/COG afin de ne pas limiter la qualité à la sparse map.

## Ordre recommandé

1. **P0 immédiat** : provider XMP/Autel, association MRK robuste et échec RTK
   précoce.
2. **P0 immédiat** : intégrer l’évaluateur checkpoint et ses artefacts au
   pipeline/dashboard.
3. **P0 production** : utiliser GLOMAP GPS/SIFT/SR/2400/BA2 avec
   retriangulation comme défaut planimétrique et conserver 1600/BA1 sans
   retriangulation comme preset rapide.
4. **P1** : validation avant/après du raffinement RTK avec rollback.
5. **P1** : BA GCP robuste et validation leave-one-out.
6. **P1** : calibration capteur et lever-arm.
7. **P2** : campagne multi-datasets et quality gates ortho/DSM.

Le dataset valide donc la vitesse et la robustesse du chemin GLOMAP actuel,
mais met en évidence un blocage RTK Autel P0 et l’absence d’une décision
qualité fondée sur des checkpoints indépendants.
