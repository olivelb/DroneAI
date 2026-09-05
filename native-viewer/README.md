# GSTile Native pour Windows

Viewer portable C++20 / Direct3D 11, Windows 10/11 x64, GPU Direct3D feature
level 11.0 minimum. Double-cliquer sur **GSTileViewer.exe**, puis choisir le
dossier contenant **manifest.json**. Aucun serveur, navigateur, Python ou WSL
n'est nécessaire pour utiliser le binaire.

Les bundles Q96 V4 sont chargés progressivement avec vérification SHA-256 et
CRC32. Les nouveaux bundles contiennent uniquement les fichiers .gst.base
et .gst.sh. Les anciens bundles à packs .gst restent lisibles ; les .zst
historiques ne sont pas nécessaires au viewer natif. Les caches sont limités à 768 MiB de
packs et 768 MiB de pages décodées, hors coupe active et travaux en cours.
Les nouveaux bundles ont deux flux : géométrie/couleur de base/opacité de base
d'abord, puis SH après stabilisation. Les anciens bundles restent compatibles.
La projection des ellipses anisotropes, les SH3 de couleur et d'opacité
directionnelle et le tri radix 32 bits sont exécutés sur le GPU.
Le moteur conserve la dernière représentation complète pendant le chargement.
La vue immobile ne déclenche pas de boucle de rendu.

## Navigation

| Action | Commande |
|---|---|
| Orbite autour du pivot | Glisser gauche |
| Déplacement latéral | Glisser milieu ou Maj + gauche |
| Vue libre | Glisser droit |
| Changer le pivot | Double-clic gauche sur une surface |
| Avancer / reculer | Molette |
| Changer la focale | Ctrl + molette |
| Se déplacer | ZQSD / WASD (positions physiques) ou flèches |
| Monter / descendre | R / F ou Page haut / bas |
| Roulis | A / E sur AZERTY, Q / E sur QWERTY, pavé 7 / 9 |
| Vitesse | Maj x4 ; Ctrl x0,2 |
| Cadrage global | Origine |
| Face / côté / dessus | 1 / 2 / 3 |
| Orbite / libre | Espace |
| Plein écran | F11 ; Echap pour sortir |
| Ouvrir un autre bundle | Ctrl + O |
| Aide | F1 |

Les menus donnent accès au budget (250 000 à 8 millions de gaussiennes),
à l'erreur de niveau de détail (0,5 à 4 pixels), à l'exposition et à la
synchronisation verticale. L'erreur affichée peut dépasser la cible lorsque
le budget empêche le raffinement. Les parents et leurs enfants ne sont jamais
dessinés ensemble dans une même coupe.
Le budget est réparti avec un seuil d'erreur commun à toute la vue :
une branche grossière ne cède plus sa priorité à des voisines moins coûteuses.
Si toutes les feuilles visibles tiennent dans le budget, leur résolution
maximale est choisie directement, même si des proxies intermédiaires sont
plus volumineux.
Des proxies peuvent subsister à 2 millions de splats ; pour examiner les
sculptures de près, le menu Qualité permet de passer à 4 millions au prix
d'un coût GPU supérieur. « Affinage LOD... » indique une coupe encore en cours
de chargement ; « Budget atteint » indique une limite de la sélection finale.

Dans **Affichage → SH de couleur**, choisir le degré **0, 1, 2 ou 3**.
Le degré 0 garde la couleur de base (DC), sans variation directionnelle.
Dans **Affichage → SH d'opacité**, l'interrupteur **Activer les SH d'opacité**
et le degré **0 à 3** sont indépendants du réglage de couleur. Désactiver cet
interrupteur conserve l'opacité de base et mémorise le degré choisi pour sa
réactivation. Le degré 0 garde également l'opacité de base.
Les changements s'appliquent immédiatement aux données présentes et restent
actifs lors d'un changement de LOD ou de bundle dans la session. Sur un bundle
à flux séparés, réactiver les SH peut déclencher leur chargement différé. Au démarrage,
les deux degrés valent 3 et les SH d'opacité sont activés.
Les degrés sont des plafonds : les coefficients absents du bundle restent
nuls. Le bundle Saint-Étienne fourni ne contient pas de SH d'opacité
directionnelle ; ce réglage n'y change donc pas l'image.

Le pivot est le centre de la gaussienne la plus proche qui contribue au pixel
cliqué avec alpha >= 0,1. Il s'agit d'un point sur la représentation courante,
pas d'une intersection avec un maillage de mesure.

## Compilation

Visual Studio 2022 avec le workload C++ Desktop, SDK Windows et CMake 3.24+.
La seule dépendance externe est nlohmann/json 3.12.0 (MIT), téléchargée et
vérifiée par SHA-256 lors de la configuration. Le runtime C++ est lié
statiquement ; les bibliothèques Direct3D sont fournies par Windows.

Dans ce workspace, exécuter la compilation depuis WSL, par exemple :

```bash
cd /home/olivier/droneAI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File \
  '\\wsl.localhost\Ubuntu\home\olivier\droneAI\native-viewer\build.ps1'
```

Le script compile en Release, lance les tests de contrat et installe
l'exécutable dans Documents/DroneAI/GSTileViewer. Aucun bundle n'est modifié.

## Mesures reproductibles

```powershell
.\GSTileViewer.exe --benchmark "Z:\chemin\gstile" --budget 2000000 `
  --frames 180 --orbit --output rapport.json --screenshot capture.bmp
```

L'option --camera chemin/vue.json fixe le point de vue de la comparaison
(eye, pivot, up, fov, version 1 ; même format que les vues enregistrées).
Elle accepte aussi --streaming, dont le rapport conserve désormais les caméras
initiale/finale et vérifie que toutes les tuiles cibles sont arrivées.
Elle ne modifie pas la vue d'accueil enregistrée.

Les options facultatives --color-sh-degree 0..3, --opacity-sh-degree 0..3
et --no-opacity-sh permettent aussi de régler les SH au lancement, avec ou
sans benchmark. Exemple : --color-sh-degree 1 --no-opacity-sh.

Le rapport conserve les réglages SH, GPU, résolution, identité du bundle, coupe LOD, budget,
caméra, temps de chargement, médiane/p95 GPU et échantillons. La mesure force
projection et tri à chaque frame, même en caméra fixe. Le tri GPU est relu
et vérifié comme permutation monotone. La trajectoire en orbite conserve
la coupe initiale pour isoler le coût du rendu ; elle ne mesure pas le
streaming en mouvement.

Une supériorité sur WebGPU doit être établie sur la même machine, la même
résolution, la même caméra, la même coupe et une qualité visuelle comparable.
Le temps d'accès à un partage réseau doit être distingué du temps GPU.

## Resultats et limites verifies

Sur la RTX 4070 Laptop et la coupe Saint-Etienne de 1 999 416 gaussiennes,
1440 x 900 : 4,7-4,8 ms GPU en natif (4,71 ms après ajout des réglages SH) contre 6,03 ms pour le moteur WebGPU du
depot, soit environ 20 % de temps GPU en moins sur ce passage. Voir
[QUALIFICATION.md](QUALIFICATION.md) pour les preuves et limites.
La validation interactive des gestes Windows reste manuelle.

Si le SDK Windows manque, Python 3.11+ peut extraire les archives officielles
sans installation systeme :

```bash
python3 native-viewer/fetch-portable-sdk.py /mnt/c/Users/user/Documents/DroneAI/.native-viewer-sdk
```

Puis passer au script build.ps1 les arguments suivants, depuis WSL :

```text
-BuildDirectory C:/Users/user/Documents/DroneAI/native-viewer-build-portable
-PortableSdk C:/Users/user/Documents/DroneAI/.native-viewer-sdk
```

Les SDK et repertoires de compilation sont des dependances de developpement,
pas des fichiers a copier pour utiliser le viewer. Le dossier portable contient
l'executable, ce guide, les licences et le rapport de qualification.

## Transitions LOD et vue d'accueil

Le raffinement continue pendant les mouvements rapides. La dernière coupe
valide reste visible pendant les lectures et uploads. Les remplacements se font
par groupes locaux parent/enfants, avec hystérésis pour limiter les allers-retours.
L'arène GPU conserve les tuiles réutilisables ; les nouvelles données brutes sont
transférées par tranches de 4 MiB. Les indices actifs sont publiés à la fin du
groupe. Les SH différés ne modifient ni le pivot ni la géométrie.

Dans Navigation, « Enregistrer cette vue » mémorise le cadrage pour ce bundle ;
« Vue d'accueil » le restaure. La vue enregistrée est restaurée à l'ouverture.
Les fichiers sont dans %LOCALAPPDATA%/DroneAI/GSTileViewer/views et le bundle
reste intact. La préférence est propre au viewer Windows.

Pour mesurer réellement le streaming et vérifier la convergence :

~~~powershell
.\GSTileViewer.exe --benchmark --streaming "Z:\chemin\gstile" --budget 2000000 --frames 360 --output streaming.json
~~~

Ce mode inclut zoom, orbite, déplacement, puis repos. Le rapport distingue
temps CPU de boucle, première image, commits, données transférées et réutilisées.
Il vérifie le tri final et les contrats GPU de l'arène. Le rapport de
qualification détaille les limites des mesures.

Pour comparer Saint-Étienne, ouvrir le dossier voisin gstile-streams avec cette
version du viewer. Les versions plus anciennes peuvent exiger le fichier .gst
historique et ne sont pas compatibles avec cette nouvelle sortie.

## Cache des alentours

Activé par défaut dans **Qualité → Précharger les alentours en RAM / VRAM**.
Après les tuiles visibles et leurs SH, le viewer prépare un champ 1,5 fois plus
large (plafonné à 120 degrés), en conservant la densité angulaire du LOD.
Les groupes de prélecture sont limités à 65 536 splats et interrompus à la
reprise du mouvement. Ils ne sont ni dessinés ni triés.

La réserve GPU conserve les pages récentes selon leur dernière utilisation :
jusqu'à 400 Mo supplémentaires sur une carte 8 Go, moins sur une petite carte,
au-delà du plus grand budget visible utilisé dans la session. Le halo occupe
au plus 200 Mo de cette réserve ; le reste peut garder les vues précédentes.
Les caches RAM de packs et de pages décodées ont chacun un plafond de 768 MiB,
hors pages actives et chargements en cours. Les pages actives sont réutilisées
directement même si elles ne tiennent plus dans le cache facultatif.

Une coupe entièrement prête en RAM et VRAM est activée directement, sans
nouvelle lecture, nouvel upload des splats ou passage par les proxies.
Cela ne supprime pas le coût du tri ni le chargement d'une vue absente du cache.

Pour mesurer un déplacement latéral et un retour à la vue initiale :

```text
GSTileViewer.exe dossier --benchmark --cache-cycle --camera vue.json --budget 2000000 --output cache-on.json
GSTileViewer.exe dossier --benchmark --cache-cycle --no-prefetch --camera vue.json --budget 2000000 --output cache-off.json
```

Le test attend le halo avant chaque changement de vue lorsque la prélecture
est activée. Les octets de lecture mesurent les appels de lecture des packs,
pas les transferts physiques SMB après les caches du système.

Les mesures détaillées sont dans CACHE-TRANSPARENCY-QUALIFICATION.md du dossier
portable. La prélecture aide les déplacements autour d'une vue préparée ;
les vues absentes du cache nécessitent encore des lectures.


## Opacité des bords de splats

Dans **Affichage → Opacite des bords de splats**, choisir une valeur de 25 %
à 200 % (préréglages et ajustement de 5 %). **100 %** conserve le rendu
d'origine ; une valeur inférieure rend les bords plus transparents, une valeur
supérieure les rend plus opaques. Le centre de chaque gaussienne conserve
son opacité, y compris ses SH. Le rayon maximal reste de trois sigmas.
Ce réglage peut changer l'occlusion globale et la netteté apparente ; il ne
charge pas davantage de détails. Il s'applique immédiatement sans recharger
les tiles. Le double-clic de pivot tient compte de la nouvelle opacité.
La valeur est réinitialisée à 100 % au prochain lancement.

Pour une mesure reproductible : `--edge-opacity 0.25`, `--edge-opacity 1`
ou `--edge-opacity 2`. Le viewer WebGPU propose le même réglage avec un
curseur et un bouton de réinitialisation au-dessus de l'image.
