# Validation du worker COLMAP modularisé — Helenenschacht

- Date : 2026-08-03
- Branche candidate : `codex/harden-colmap-stages`
- Base Git : `fd7e687`
- Dataset : `odm_data_helenenschacht`
- Images : 176/176, Autel XT705
- CRS : `EPSG:32633` (WGS 84 / UTM 33N)
- Matériel : RTX 4070 Laptop, pilote hôte laissé en gestion automatique
- Runtime : COLMAP 4.1.1, commit `a0d785f`, CUDA 12.8.1

## Objectif

Cette exécution valide le chemin réel reconstruction, raffinement RTK et
alignement après la séparation du worker COLMAP en modules de préparation,
mapping sparse, alignement, configuration DroneGS et publication. Le dataset
Windows a été monté en lecture seule et aucun contrôle de puissance, mémoire
ou fréquence GPU n'a été appliqué.

Les empreintes des entrées correspondent au benchmark historique :

- `gcp_list.txt` :
  `df3390a82c5a638d54046c08d8a6afd2a029a482c026627a58a7afbb5eb61dd5` ;
- MRK :
  `68968937066cb7e91a0f960bcd7b2c1946b47d0c6ddea4d3a6a92d521c532e84`.

## Configuration

Le test utilise le profil Survey actuel : SIFT CUDA, 2 400 px, 4 096
features, `SIMPLE_RADIAL`, graphe GPS borné, deux passes de BA globale,
retriangulation finale, puis `pose_prior_mapper` limité à 25 itérations.

## Résultat de bout en bout

| Métrique | Résultat |
| --- | ---: |
| Images enregistrées | 176/176 (100 %) |
| Points sparse après RTK | 25 472 |
| Erreur de reprojection moyenne | 1,408085 px |
| Erreur de reprojection médiane | 1,430772 px |
| Extraction SIFT | 98,361 s |
| Matching | 8,489 s |
| Mapping GLOMAP | 31,832 s |
| Raffinement RTK | 2,071 s |
| Exécution complète | 155,9 s |
| Résidu GNSS horizontal médian | 8,55 cm |
| Résidu GNSS vertical médian | 3,71 cm |

Les cinq GCP ont ensuite été évalués comme checkpoints indépendants, sans
participer au mapping, au bundle adjustment ou à l'alignement géographique :

| Métrique checkpoint | Résultat |
| --- | ---: |
| RMSE horizontale | 5,015 cm |
| RMSE verticale | 24,591 cm |
| Erreur horizontale maximale | 7,180 cm |
| Erreur verticale maximale | 34,954 cm |

La planimétrie reproduit donc le résultat historique de 5,0 cm. La verticale
reste non certifiée puisque le datum vertical du dataset n'est pas déclaré.

## Garde-fous RTK

Le candidat RTK a été comparé au sparse visuel avant promotion :

| Contrôle | Mesure | Limite | Verdict |
| --- | ---: | ---: | --- |
| Ratio de points sparse | 1,0141 | >= 0,90 | accepté |
| Dégradation de reprojection | +0,06555 px | <= +0,10 px | accepté |
| Perte de longueur de piste moyenne | 0,160 % | <= 25 % | accepté |
| Variation de focale | 0,03196 % | <= 2 % | accepté |
| Images enregistrées | 176/176 | aucune perte | accepté |

Le raffinement RTK est donc correctement promu par le worker.

## Contrôle GCP pondéré

Le même modèle a été diagnostiqué avec les points 1, 2 et 5 en ajustement et
les points 3 et 4 comme checkpoints indépendants, à partir du CSV versionné
`helenenschacht-gcp-accuracy-3-adjustment-2-checkpoint.csv`.

| Métrique | Résultat | Limite |
| --- | ---: | ---: |
| RMSE horizontale checkpoint | 5,996 cm | 10 cm |
| RMSE verticale checkpoint | 13,647 cm | 20 cm |
| Erreur normalisée maximale | 5,659 sigma | 5 sigma |

La promotion GCP est volontairement rejetée sur le dernier contrôle. Le
pipeline ne transforme donc pas silencieusement un ajustement qui dépasse
l'incertitude déclarée, même lorsque ses erreurs métriques absolues restent
sous les plafonds horizontaux et verticaux.

## Validation logicielle associée

- 347 tests CPU réussis, 13 tests GPU désélectionnés dans ce passage ;
- 13/13 tests CuPy/CUDA réussis séparément sur la RTX 4070 ;
- Ruff et compilation Python réussis ;
- `mypy --strict` réussi sur les 15 modules du worker ;
- aucune duplication détectée par jscpd sur 103 fichiers Python ;
- workflows YAML et liens de documentation validés.

Les images Docker, le cache de build, les modèles téléchargés et le workspace
de validation ont été supprimés après la collecte des métriques. Le dataset
source n'a pas été modifié.
