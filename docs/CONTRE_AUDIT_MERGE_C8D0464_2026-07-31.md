# Contre-audit du merge `c8d0464`

Date : 31 juillet 2026
Périmètre : branche `main` au commit
`c8d0464401728f21c616962177280535e0ef8e79`, puis correctifs locaux de ce
contre-audit.

## Verdict

Le diagnostic général du rapport est solide, mais trois nuances changent son
classement :

1. le défaut Helm P0 est confirmé et touche cinq workloads, pas seulement les
   trois cités ;
2. la réserve sur l'absence de test `checkpoint_saved.path` est invalidée : ce
   contrat possède déjà un test exécutable exact ;
3. la prudence sur le profil Helenenschacht est justifiée, mais la
   documentation du dépôt le présentait déjà comme un profil planimétrique
   mono-dataset. Le défaut restant était surtout le libellé trop généraliste
   du dashboard.

Les défauts exécutables signalés ont été corrigés, à l'exception des chantiers
qui constituent de nouvelles fonctionnalités ou des changements
d'infrastructure majeurs : quality gate GCP intégré, agrégation spatiale
streamée, migration non-root du worker COLMAP et durcissement d'identité
multi-tenant.

## Contre-analyse point par point

| Point du rapport | Verdict après vérification | Action |
|---|---|---|
| Adaptateur DroneGS `path`/`checkpoint` | Validé | Aucun correctif nécessaire |
| Absence de test `checkpoint_saved.path` | Invalidé | Le test `test_dronegs_cancellation_terminates_process_and_keeps_checkpoint` émet déjà `path`, vérifie l'itération et le contenu du checkpoint |
| Publication S3 obligatoire et vérifiée | Validé | Conservé |
| Protection contre `../` dans les clés S3 | Validé et testé | Conservé |
| Agrégation durable des tuiles legacy | Validé | Conservé ; la finalisation des campagnes IA a été renforcée séparément |
| Outbox terminale après `max_attempts` | Validé | Conservé |
| Publication Kafka sous verrou outbox | Validé | Remplacée par claim court, lease, publication hors transaction et commit final |
| Alembic dans Helm et round-trip CI | Validé | Conservé |
| Secret Helm production codé en dur | Confirmé P0 | Corrigé dans COLMAP, API, migration, IA et processing ; assertion ajoutée à la CI |
| Statuts HTTP | Globalement validé | Conservé ; preview trop grande renvoie désormais 413 |
| COG et lecture fenêtrée | Validé | Conservé |
| Preview 16 bits trop gourmande | Confirmé | Limite à 20 Mpx, normalisation NumPy `float32`, palette sans listes Python par pixel |
| Seed DroneGS partitionnée | Validé | Conservé |
| Quality gate sparse amélioré | Validé | Conservé ; il ne remplace pas des checkpoints indépendants |
| GCP indépendants dans le gate | Toujours absent | Dette fonctionnelle maintenue |
| Profil 2400/BA2/retri universalisé | Partiellement validé | Backend inchangé ; UI et métadonnées renommées et bornées explicitement à Helenenschacht/planimétrie |
| Support Autel/XMP RTK absent | Confirmé P0 | Parser XMP, deux motifs de noms, covariance ENU, attitudes, focale calibrée, priorité fournisseur et ambiguïtés explicites |
| MRK séparé des images | Sous-estimé par le rapport | Recherche récursive et association globale non ambiguë ; ingestion de datasets imbriqués corrigée |
| Finalisation IA concurrente | Confirmé | Lease persistante et propriétaire exclusif ajoutés par migration |
| Retries de tuiles IA infinis | Confirmé | Budget borné, état `dead`, campagne terminale et replay manuel |
| Agrégation IA complète en mémoire | Confirmé | Taille par tuile et cumulée, nombre brut et nombre final désormais bornés ; une refonte PostGIS/streaming reste nécessaire pour dépasser ces plafonds |
| Annulation IA empoisonnant un retry | Confirmé | Registre indexé par `(vol_id, run_id, attempt)` et nettoyage de fin de génération |
| Secrets API et sessions sans révocation | Confirmé | Dette acceptable uniquement dans la frontière mono-tenant interne documentée |
| CSRF cookie et token WebSocket | Validé | Conservé |
| CI incomplète (types, SBOM, chaos, GPU release) | Validé | Dette de gouvernance maintenue |
| Images exécutées en root | Partiellement validé | API, migration, processing, IA et frontend passent en UID fixe non-root avec racine lecture seule et volumes temporaires explicites ; COLMAP reste une exception documentée à cause des workspaces `hostPath`/GPU existants |
| Cache et rate limiting des tuiles COG | Partiellement invalidé | Le cache navigateur privé d'une heure existait déjà au commit audité ; ajout d'un token bucket borné par client pour le rate limiting, configurable dans Compose et Helm |
| Stratégie `RollingUpdate` API absente | Invalidé | `RollingUpdate` est déjà la stratégie Kubernetes par défaut d'un `Deployment` lorsque `strategy` est omise |
| Compose exposé sur toutes les interfaces | Confirmé | Bind `127.0.0.1` par défaut, opt-in LAN documenté |
| Suppression de mission non transactionnelle | Confirmé | Passage en `deleting`, suppression S3, puis suppression DB ; état `deletion_failed` si S3 échoue |

## Défauts supplémentaires trouvés

### Tentative métier écrasée par le retry Kafka

`process_message()` remplaçait le champ métier `attempt` du producteur par le
numéro de retry local du handler. Une annulation ou une détection d'une ancienne
génération pouvait donc être attribuée à la génération courante.

Le champ `attempt` est maintenant préservé. Le retry de livraison est exposé
séparément dans `delivery_attempt`.

### Reprises de mission non générationnelles

Toutes les reprises d'une même mission utilisaient le même identifiant
d'événement déterministe. Après une première publication outbox, une reprise
ultérieure pouvait retrouver la ligne déjà publiée et ne rien réémettre.

La mission possède maintenant un `retry_count`. Cette génération est propagée
de l'API à COLMAP, au tiler, à l'IA et au retour de détection. Les événements et
les vérifications de journaux incluent cette génération, et les résultats
retardataires sont refusés.

### Datasets imbriqués ignorés par COLMAP

Le téléchargement S3 conservait les sous-répertoires, mais la préparation
COLMAP ne regardait que la racine avec `os.listdir()`. Un dataset standard
`images/` + `RTK_Data/` pouvait donc contenir zéro image visible et aucun MRK.

La découverte est maintenant récursive. Les fichiers sont aplatis uniquement
si leurs noms sont uniques ; une collision est refusée explicitement.

### Dépendance NumPy implicite

L'API importe désormais directement NumPy pour les previews. NumPy a été ajouté
comme dépendance directe de l'API au lieu de dépendre transitivement de
Rasterio.

### Image IA mutable et environnement Python incohérent

L'image IA reposait sur `ultralytics/ultralytics:latest` et téléchargeait le
checkpoint YOLO sans contrôler son empreinte. De plus, l'installation des
dépendances mettait Click à jour vers une version incompatible avec l'outil de
développement `spin` hérité de l'image de base ; `pip check` échouait dans
l'image pourtant construite avec succès.

La base est maintenant figée par digest, le checkpoint par SHA-256, `spin` est
retiré puisqu'il n'est pas une dépendance runtime, et le build exécute
obligatoirement `pip check`.

### Avis de sécurité dans l'outillage frontend

L'audit npm de production est vierge, mais l'audit incluant les dépendances de
développement remonte neuf avis de sévérité haute via ESLint, `minimatch` et
`brace-expansion`. Le correctif proposé impose actuellement une mise à niveau
majeure d'ESLint non déclarée compatible par `eslint-config-next`. Le risque
est limité au lint/build sur des motifs contrôlés par le dépôt, pas au bundle
de production, mais cette dette supply-chain reste ouverte.

## Validation sur Helenenschacht

Le dataset local réel a été inspecté sans modifier ses sources :

- 176 images `MAX_*.JPG` détectées ;
- caméra `Autel Robotics XT705` sur 176 images ;
- 176 positions RTK associées ;
- 176 covariances ENU complètes et positives ;
- incertitude horizontale médiane : `0,013807 m` ;
- incertitude verticale médiane : `0,02623 m` ;
- aucun échec du preflight avec `gps_quality=rtk` ;
- écart maximal MRK/XMP : environ `5e-9` degré horizontal et `0,005 m`
  vertical.

Le datum vertical reste volontairement déclaré `vendor-ellipsoidal` ou
inconnu/mixte tant qu'aucune transformation verticale explicite n'est fournie.

### Mesures post-audit

La campagne a ensuite comparé plusieurs sparse causaux, un GeoTIFF DroneGS
complet et un projet Metashape 2.3.1 construit sans GCP de contrôle. Le preset
`Précision 3D · RTK` retenu atteint 6,32 cm de RMSE horizontale, 15,74 cm en
verticale et 16,96 cm en 3D sur le sparse. Son DSM final atteint 11,44 cm de
RMSE verticale et les centres de cible reconstruits dans l'orthomosaïque
DroneGS 6,24 cm de RMSE horizontale.

Sur les mêmes cinq cibles, l'orthomosaïque Metashape atteint 14,88 cm de RMSE
horizontale. Son sparse, plus dense, présente un biais vertical moyen de
-2,765 m. Les GCP sont restés exclusivement des checkpoints dans les deux
workflows. Le protocole complet et les limites sont consignés dans
[`benchmarks/helenenschacht-our-workflow-vs-metashape-2026-08-01.md`](benchmarks/helenenschacht-our-workflow-vs-metashape-2026-08-01.md).

## Validations exécutées

- Ruff : succès ;
- compilation Python : succès ;
- Pytest hors GPU/intégration : **244 réussis, 3 ignorés** (CuPy/Fiona
  optionnels) ;
- tests ciblés de reprise Gaussian, canari et DSM : **24 réussis** ;
- test contractuel DroneGS checkpoint : succès ;
- `pip check` : succès ;
- Alembic sur PostGIS réel : `upgrade head → downgrade base → upgrade head` ;
- `helm lint` : succès ;
- rendu de l'overlay production : aucune référence au Secret local et
  références au Secret externe attendues ;
- `docker compose config --quiet` : succès ;
- `npm audit --omit=dev --audit-level=high` : aucune vulnérabilité de
  production ;
- ESLint et build Next.js : succès ;
- builds Docker API, processing, IA et frontend : succès ;
- smoke tests non-root avec racine lecture seule sur ces quatre images : succès ;
- `git diff --check` : succès.

## Risques encore ouverts

Avant de qualifier la plateforme de métrologique générique ou de production
publique, il reste au minimum :

1. intégrer les évaluateurs GCP/DSM indépendants désormais disponibles au
   worker et au mécanisme de promotion/rollback, avec des gates horizontal et
   vertical distincts ;
2. qualifier les profils sur plusieurs capteurs, reliefs et géométries ;
3. remplacer les plafonds de sécurité de l'agrégation IA par un traitement par
   lots ou spatial en base lorsque les campagnes devront les dépasser ;
4. migrer COLMAP vers un UID non-root après qualification et préparation des
   workspaces persistants existants ;
5. utiliser un rate limiter partagé si l'API devient multi-réplique et ajouter
   un cache serveur/CDN si le cache navigateur ne suffit plus ;
6. introduire révocation, rotation et audit d'usage des identités pour sortir
   de la frontière mono-tenant interne ;
7. compléter la CI par types, couverture, scan de secrets/images, SBOM et
   gates GPU/nightly, puis résorber les avis npm de l'outillage de
   développement quand l'écosystème ESLint/Next proposera une mise à niveau
   compatible.

Conclusion : après ces correctifs, le dépôt constitue une base interne avancée
et cohérente. Il n'est toujours pas une solution métrologique universelle, mais
les P0 opérationnels identifiés dans le rapport ne restent plus ouverts.
