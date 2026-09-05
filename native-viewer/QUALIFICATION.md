# Qualification Windows — 5 septembre 2026

Sources : branche locale `codex/windows-gstile-viewer`, base `a34009d`.
Travail non commité. Exécutable Release MSVC 19.38.33130.0, runtime statique.
SDK Windows officiel 10.0.26100.9169, nlohmann/json 3.12.0.
RTX 4070 Laptop, 8 Go, pilote 610.62, Windows x64.

## Résultats

Bundle Saint-Étienne fourni par l'utilisateur :
`sha256:7ca65986f3a890bcd5e442db254febcdbbebca7d43a8b09a63bb205146f76821`,
123 727 664 gaussiennes, 6 373 nœuds, 6 250 packs, environ 16,9 Go canoniques.
Lecture depuis le partage réseau Z:.

| Passage | Résidents | GPU médiane | GPU p95 | Ouverture + chargement |
|---|---:|---:|---:|---:|
| Natif, 500k, premier passage | 499 122 | 1,34 ms | 1,86 ms | 64,41 s |
| Natif, 2M, après correction chemins réseau | 1 999 416 | 4,67 ms | 4,78 ms | 4,71 s |
| Natif, 4M | 3 999 568 | 10,11 ms | 10,56 ms | 6,54 s |
| Natif, 2M, tests GPU supplémentaires | 1 999 416 | 4,84 ms | 5,42 ms | 5,39 s |
| WebGPU, même coupe 2M | 1 999 416 | 6,03 ms | 6,23 ms | non comparable |

La première ouverture était ralentie par la résolution de chaque chemin du
manifeste sur SMB. La version livrée vérifie lexicalement tous les chemins,
puis résout et vérifie les liens des packs sélectionnés à la lecture.
Les shaders sont compilés et embarqués à la compilation du programme.

Comparaison : 1440 × 900, FOV vertical 60°, même coupe de 118 nœuds,
même trajectoire d'orbite (0,0025 rad/frame), 32 frames de chauffe puis 120
frames mesurées. Les valeurs sont des durées GPU, pas des FPS affichés.
Sur ce passage, le natif consomme environ **20 % de temps GPU en moins**.
Ce résultat ponctuel n'est pas une garantie sur tous les bundles ou points de vue.

## Méthode WebGPU

Le harnais transpile les modules du checkout et utilise le PlayCanvas installé
et patché par le dépôt (2.21.4). Les shaders et le décodeur de production ne
sont pas modifiés. Une instrumentation remplace uniquement la coupe LOD par
celle du rapport natif et applique les poses de caméra pour isoler le rendu.
Les passes GPU sont celles du profiler de production. La population résidente
est vérifiée. L'exécution n'a signalé aucune erreur WebGPU.

Le navigateur intégré Chromium a obtenu un adaptateur NVIDIA Lovelace. Il
masque le modèle précis. La machine possède la RTX 4070 Laptop utilisée par
le natif ; le rapport conserve les informations d'adaptateur exposées.
Le natif mesure l'intervalle GPU par timestamps Direct3D ; WebGPU additionne
les passes instrumentées, avec une granularité visible de 65,536 µs.
Les temps de chargement ne sont pas comparés : HTTP local et fichiers SMB
n'ont pas la même chaîne de transport.

Les captures ont été inspectées : même contenu et cadrage. La parité
pixel à pixel, la qualité face au PLY source et le rendu en très gros plan ne
sont pas qualifiés. Les deux moteurs n'ont pas exactement les mêmes seuils
de contribution, support rasterisé et représentation interne des SH.

## Vérifications effectuées

- Compilation Release et shaders HLSL sur Windows.
- CTest : empreintes connues SHA256/CRC32, plages agrégées, décodage Q96,
  remplacement parent/enfants sous budget, annulation, corruption, profils,
  chemins malformés, géométrie du manifeste et commandes de caméra.
- GPU réel : projection Q96 et valeurs SH3/opacité directionnelle comparées
  aux valeurs analytiques sur l'axe +Y.
- Tri GPU : 65 539 éléments, population non puissance de deux, plus de
  256 groupes ; vérification de l'ordre et de la permutation.
- Sélection GPU du pivot et tri complet des coupes Saint-Étienne.
- Passage du moteur WebGPU de production sur la même coupe ; 0 erreur.
- Captures réelles du framebuffer natif inspectées.

Le contrôle interactif Windows par l'outil d'automatisation a expiré en
attente d'autorisation. Le parcours réel de la boîte de dossier, le plein
écran et les gestes souris restent à confirmer manuellement. Les opérations
de caméra et le calcul GPU du pivot ont été testés indépendamment.
Le workflow Windows ajouté n'a pas été exécuté sur GitHub.

## Limites opérationnelles

Le LOD est désormais recalculé pendant les mouvements (au plus une requête
toutes les 100 ms quand le chargeur et l'upload sont disponibles), puis sans
ce délai au repos. Les groupes locaux remplacent atomiquement leurs parents.
Les données brutes arrivent par tranches de 4 MiB dans une arène GPU persistante.
Le cache de packs est limité à 768 MiB, celui des pages décodées à 384 MiB,
hors références de la coupe et du travail en cours. La liste des indices actifs
reste reconstruite à chaque commit et les buffers de travail gardent leur
capacité maximale atteinte dans la session.

Les .zst ne sont pas décodés par le natif ; il utilise les flux séparés bruts
si disponibles, sinon les packs Q96 canoniques. Les anciens profils sont
refusés. Il n'y a pas de préchargement prédictif natif. Une lecture SMB lente
peut retarder l'arrêt jusqu'à sa fin. La perte du périphérique GPU nécessite
de rouvrir le viewer.

## Preuves conservées

Les rapports JSON, captures BMP, le harnais WebGPU et ses empreintes de source,
les logs de serveur, les répertoires de compilation et les archives du SDK
sont conservés dans le workspace Windows. Les mesures détaillées sont dans
`GSTileViewer/evidence/`. `SHA256SUMS.txt` identifie le binaire livré.

Pour reproduire la comparaison, générer d'abord un rapport natif avec
`--benchmark --orbit`, puis exécuter `benchmarks/prepare-webgpu.mjs` depuis
WSL. Lancer `benchmarks/launch-webgpu.ps1` avec les quatre chemins demandés.
Ouvrir l'adresse locale indiquée et cliquer « Lancer la mesure WebGPU ».

## Ajout des contrôles SH indépendants

Le binaire a été recompilé après ajout des menus de degré couleur 0–3,
degré opacité 0–3 et activation séparée des SH d'opacité. Les valeurs de base
restent actives au degré 0 ou lorsque les SH d'opacité sont désactivés.
Les options de lancement correspondantes sont conservées dans les rapports.

Validation supplémentaire sur la RTX 4070 Laptop :

- CTest Windows Release : succès.
- Lecture GPU de 65 539 projections pour chacune des 16 combinaisons de
  degrés couleur/opacité, plus 4 cas avec opacité directionnelle désactivée.
  Comparaison analytique indépendante des valeurs RGB et alpha, caméra fixe,
  après chaque changement de réglage ; sélection du pivot dans chaque cas.
- Rejet des degrés hors de 0–3 par l'API du moteur.
- Trois passages du bundle réel : SH complets, couleur 0 avec opacité SH
  désactivée, puis couleur 1 / opacité 2. Tri et contrats GPU validés.
- SH complets, même protocole d'orbite 2M / 120 frames : médiane 4,71 ms GPU,
  p95 5,43 ms. Aucun nouveau passage WebGPU n'a été effectué pour cet ajout.

Le bundle réel a un degré d'opacité SH de 0. Les données synthétiques de
contrat contiennent des coefficients d'opacité non nuls pour vérifier
l'interrupteur et les degrés. Le parcours interactif des nouveaux menus
reste à confirmer manuellement. Rapports : evidence/sh-controls-*.json ;
empreintes actualisées : evidence/source-provenance-sh-controls.json.

## Qualification du streaming progressif

Les sources WebGPU, bundler et API ont aussi été modifiées. Les nouveaux
bundles séparent base et SH avec vérification SHA256/CRC indépendante ; les
bundles existants gardent leur compatibilité. Un convertisseur sans
reconstruction LOD est fourni dans tools/split_gstile_attributes.py.

Sur RTX 4070 Laptop, passage natif Saint-Étienne à budget 2M :
50 commits, CPU boucle p95 2,01 ms, maximum 24,63 ms, première image 962 ms.
La coupe finale de cette trajectoire contient 946 114 gaussiennes et converge.
Ce n'est pas une mesure de FPS ni une comparaison directe au navigateur.
Sur le sous-ensemble à flux séparés : 20 commits, p95 CPU 0,86 ms,
245 760 gaussiennes finales, aucune page SH restante.

WebGPU, sous-ensemble à budget 250k : 14 changements de coupe/qualité observés,
245 760 gaussiennes finales, aucun SH manquant, aucune erreur GPU/Worker.
Intervalle entre frames p95 7,98 ms, maximum 31,68 ms ; dernière publication
CPU 6,18 ms. Le partage SMB et HTTP local ne sont pas comparables.

Les tests GPU natifs vérifient aussi le rendu de l'ancienne coupe pendant les
uploads partiels, la croissance d'arène, la réutilisation sans nouvel upload
brut, les SH et le pivot. Les tests automatisés Windows passent.
Le protocole complet et ses limites figurent dans
docs/benchmarks/gstile-progressive-streaming-qualification.md du dépôt.
Preuves : evidence/native-streaming-*-v2.json et evidence/webgpu-streaming-v2.json.
Lors de ce premier passage, le bundle complet n'avait pas été converti ; la séparation était
qualifiée sur un sous-ensemble réel et par tests de round-trip, avant la
republication complète des 16,9 Go.

## Sortie exclusivement base/SH

Le bundler et le convertisseur ne conservent plus les packs .gst/.zst.
Le natif est recompilé pour ce contrat et la reconstruction Q96 virtuelle.
Les contrats Windows vérifient les deux chemins de décodage lorsque le fichier
canonique est absent. Le jeu réel tronqué termine également ses transitions
sur GPU sans aucun pack historique : 245 760 résidents, aucun SH manquant.
Le parcours WebGPU équivalent termine sans erreur, 14 changements observés.
Preuves : native-streams-only-fixture.json et webgpu-streams-only.json.

Le bundle complet est maintenant converti à côté de l'original dans
gstile-streams : 12500 fichiers base/SH et manifest.json,
16.907 Go au total, sans .gst/.zst historiques.
La comparaison du framebuffer natif avec la même coupe/caméra donne des
octets identiques : True. Le parcours LOD complet
converge sans SH manquant. Voir STREAMING-QUALIFICATION.md dans le dossier
portable et les preuves saint-etienne-streams-conversion.json et
comparison-streams-pixels.json.

## Correction de sélection en gros plan — 2026-09-05

Sélection globale équilibrée, erreur bornée dans les volumes et frustum AABB.
Deux gros plans et un parcours LOD ont été vérifiés sur le bundle complet.
La zone sculptée devient plus détaillée à budget identique ; des proxies
restent à 2 M. À 4 M, le bas est nettement affiné (15,29 ms GPU médian sur
cette vue, contre 7,72 ms à 2 M). Ce n'est pas un gain de vitesse GPU.
Les comparaisons, caméras et limites figurent dans STREAMING-QUALIFICATION.md.

## Cache des alentours et transparence WebGPU — 2026-09-05

La prélecture RAM/VRAM et l'activation directe des coupes chaudes sont qualifiées
à 2 M et 4 M. Retour à la vue initiale : 32.2 /
39.6 ms sans nouvelle lecture ni upload de splats.
Sur une même coupe/caméra, l'alignement du noyau alpha WebGPU réduit l'écart
moyen des pixels sombres avec le natif de
2.54 à 1.29 niveaux RGB.
Voir CACHE-TRANSPARENCY-QUALIFICATION.md pour la méthode et les limites.


## Opacité des bords — 2026-09-05

Réglage 25–200 % vérifié sur le GPU réel dans les deux viewers. Le rendu natif
à 100 % est pixel-identique à la référence précédente. Voir
EDGE-OPACITY-QUALIFICATION.md dans le dossier portable pour les captures,
la méthode, le test reproductible et les limites.
