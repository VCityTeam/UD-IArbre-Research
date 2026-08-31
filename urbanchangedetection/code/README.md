# Workflow de détection de changement (LiDAR + Orthophoto + FLAIR-HUB)

Ce script (`run_change_detection_workflow.py`) orchestre le pipeline
`run_workflow.py` sur **plusieurs années** (typiquement une année "avant" et
une année "après"), puis enchaîne automatiquement sur la détection de
changement combinant LiDAR et probabilités FLAIR-HUB. Il fonctionne aussi
bien pour la classe **bâtiment** que pour la classe **végétation**, et plus
généralement pour toute classe définie dans le `matrix_config`.

## 1. Ce que fait le script, étape par étape

Pour **chaque année** demandée (`--years`), le script :

1. **Télécharge/vérifie les inventaires** LiDAR et orthophoto (`--nuage-json`,
   `--ortho-json`), avec téléchargement optionnel depuis une URL de secours
   si absents (`--download-missing-inventories`).
2. **Extrait les tuiles** sur l'emprise demandée :
   - `extract_nuage.py` → tuiles LiDAR `.laz`
   - `ortho_extract.py` → tuiles orthophoto rééchantillonnées
3. **Rasterise le nuage de points** (`fusion_nuage.py`) en hauteur, classes,
   MNS et MNT, puis assemble chaque mosaïque avec `ortho_fusion.py`.
4. **Lance l'inférence FLAIR-HUB** (`flair_zonal_detection.main`) sur la
   mosaïque orthophoto pour produire un raster de probabilités par classe
   (`class_prob`).
5. Retourne, par année, les chemins vers : `lidar_height`, `lidar_class`,
   `lidar_mns`, `lidar_mnt`, `orthophoto_mosaic`, `class_prob`.

Une fois toutes les années traitées, le script lance **une fois par classe
cible** (`--target-classes`) le script de détection de changement
(`change-detection-lidar-flair-class_prob.py`), en comparant la première et
la dernière année de `--years` (ex : 2018 → 2023). Il produit un raster de
changement à 4 classes : destruction / construction / modification / pas de
changement.

Enfin, un fichier de métadonnées (`run_metadata.json` par défaut) est écrit
avec la commande exacte utilisée, les paramètres résolus, les chemins de
sortie et les versions des paquets Python — utile pour rejouer ou auditer un
run.

FLAIR-HUB peut aussi travailler avec des orthophotos infrarouges, mais nous avons intentionnellement gardé seulement un fonctionnement RGB par raison de simplicité.

## 2. Pré-requis

Si besoin, dans le dossier code, créer le répertoire `workdir/inputs` : 

```powershell
New-Item -ItemType Directory -Force -Path workdir`inputs | Out-Null
```

Et placer les inventaires dedans.

### Structure des entrées :

```text
workdir/
└── inputs/
    ├── nuage_2018.json
    ├── nuage_2023.json
    ├── ortho_2018.json
    └── ortho_2023.json
```

## 3. Structure des fichiers produits

```
<workspace>/
├── models/                         # modèle FLAIR-HUB téléchargé (partagé)
├── .deps/                          # source du dépôt FLAIR-HUB (partagé)
└── runs/
    └── <run-name>/
        ├── run_metadata.json
        ├── 2018/
        │   ├── lidar/{laz_tiles,tiles,mosaic}/
        │   ├── ortho/{temp_5cm,tiles,mosaic}/
        │   └── flair/probabilities/
        ├── 2023/
        │   └── ... (même structure)
        └── change_detection/
            ├── building/
            └── vegetation/
```

## 4. Utilisation

### 4.0 Build

Depuis le dossier code :

```powershell
docker compose build change-detection
docker compose build change-detection-gpu
```

`change-detection` est l'image CPU.

`change-detection-gpu` build une image PyTorch CUDA-enabled avec `gpus: all` pour les hosts NVIDIA.

### 4.1 Exemple — détection de changement sur les bâtiments

Sur **CPU** :

```bash
docker compose run --rm change-detection `
  python run_change_detection_workflow.py `
  --run-name gc_2018-2023 `
  --years 2018 2023 `
  --xmin-start 1845000 `
  --xmin-end 1845500 `
  --ymin-start 5175000 `
  --ymin-end 5175500 `
  --nuage-json 2018=workdir/inputs/nuage_2018.json `
  --nuage-json 2023=workdir/inputs/nuage_2023.json `
  --matrix-config configs/baseline/configs.yml ` 
  --target-classes building ` 
```

Sur **GPU** :

```bash
docker compose run --rm change-detection-gpu `
  python run_change_detection_workflow.py `
  --run-name gc_2018-2023 `
  --years 2018 2023 `
  --xmin-start 1845000 `
  --xmin-end 1845500 `
  --ymin-start 5175000 `
  --ymin-end 5175500 `
  --nuage-json 2018=workdir/inputs/nuage_2018.json `
  --nuage-json 2023=workdir/inputs/nuage_2023.json `
  --target-classes building ` 
  --matrix-config configs/baseline/configs.yml ` 
  --use-gpu
```

### 4.2 Exemple — détection de changement sur la végétation

Il suffit de changer `--target-classes` (la classe doit exister dans le
`matrix_config` utilisé) :

Sur **CPU** :

```bash
docker compose run --rm change-detection `
  python run_change_detection_workflow.py `
  --run-name gc_2018-2023 `
  --years 2018 2023 `
  --xmin-start 1845000 `
  --xmin-end 1845500 `
  --ymin-start 5175000 `
  --ymin-end 5175500 `
  --nuage-json 2018=workdir/inputs/nuage_2018.json `
  --nuage-json 2023=workdir/inputs/nuage_2023.json `
  --target-classes vegetalisation ` 
  --matrix-config configs/baseline/configs.yml
```

Sur **GPU** :

```bash
docker compose run --rm change-detection-gpu `
  python run_change_detection_workflow.py `
  --run-name gc_2018-2023 `
  --years 2018 2023 `
  --xmin-start 1845000 `
  --xmin-end 1845500 `
  --ymin-start 5175000 `
  --ymin-end 5175500 `
  --nuage-json 2018=workdir/inputs/nuage_2018.json `
  --nuage-json 2023=workdir/inputs/nuage_2023.json `
  --target-classes vegetalisation ` 
  --matrix-config configs/baseline/configs.yml ` 
  --use-gpu
```

### 4.3 Traiter plusieurs classes en une seule commande

```bash
--target-classes building vegetation
```

Un dossier de sortie séparé est créé par classe sous
`change_detection/<classe>/`.

### 4.4 Télécharger automatiquement les inventaires manquants

```bash
--download-missing-inventories `
--nuage-json-url 2018=https://.../nuage_2018.json `
--ortho-json-url 2018=https://.../ortho_2018.json
```

### 4.5 Relancer rapidement sans refaire l'extraction/rasterisation

Si les mosaïques LiDAR/orthophoto existent déjà pour les années concernées :

```bash
--reuse-derived-rasters
```

Si les probabilités FLAIR existent déjà et qu'on veut juste rejouer la
détection de changement :

```bash
--skip-flair
```

Pour ne rejouer que la détection de changement finale (les rasters par
année existent déjà, pas de retraitement) :

```bash
docker compose run --rm change-detection `
  python run_change_detection_workflow.py `
  ... `
  --skip-download `
  --reuse-derived-rasters `
  --skip-flair
```

Pour l'inverse (traiter les deux années sans lancer la détection de
changement finale) :

```bash
--skip-change-detection
```

### 4.6 Aide

```powershell
docker compose run --rm change-detection python run_change_detection_workflow.py --help
```

## 5. Référence des arguments principaux

| Argument | Rôle |
|---|---|
| `--run-name` | Nom du run (obligatoire), détermine le sous-dossier `runs/<run-name>/` |
| `--workspace` | Dossier racine de travail (défaut : `workdir`) |
| `--years` | Liste des années à traiter, ordre chronologique (défaut : `2018 2023`) |
| `--xmin-start/--xmin-end/--ymin-start/--ymin-end` | Emprise spatiale (bbox), partagée entre toutes les années |
| `--nuage-json ANNEE=CHEMIN` | Inventaire LiDAR par année (répétable) |
| `--ortho-json ANNEE=CHEMIN` | Inventaire orthophoto par année (répétable) |
| `--nuage-json-url` / `--ortho-json-url` | URL de secours si l'inventaire est absent |
| `--download-missing-inventories` | Active le téléchargement automatique |
| `--resolution` | Résolution (m) de rasterisation LiDAR |
| `--ortho-source-resolution` / `--ortho-target-resolution` | Résolutions orthophoto source/sortie |
| `--batch-size`, `--num-worker`, `--img-pixels-detection`, `--margin` | Paramètres d'inférence FLAIR-HUB |
| `--use-gpu` / `--no-use-gpu` | Active/force la désactivation du GPU |
| `--model-path` / `--model-repo` / `--model-filename` / `--model-revision` | Modèle FLAIR-HUB à utiliser |
| `--flair-hub-ref` / `--flair-hub-repo-url` | Dépôt/branche FLAIR-HUB à cloner |
| `--skip-download` | Saute le téléchargement/extraction des tuiles |
| `--reuse-derived-rasters` | Réutilise les mosaïques déjà présentes |
| `--skip-flair` | Saute l'inférence FLAIR-HUB, réutilise les probabilités déjà produites |
| `--change-detection-script` | Script de détection de changement à appeler (défaut : `change-detection-lidar-flair-class_prob.py`) |
| `--skip-change-detection` | N'exécute que les étapes par année |
| `--target-classes` | Une ou plusieurs classes du `matrix_config` (défaut : `building`) |
| `--prob-threshold` | Seuil (0–1) de binarisation des probabilités (défaut : `20/255`) |
| `--metadata-name` | Nom du fichier de métadonnées de run (défaut : `run_metadata.json`) |


## 6. Sorties

- **Par année** : mosaïques LiDAR (hauteur, classes, MNS, MNT), mosaïque
  orthophoto, raster de probabilités FLAIR-HUB (`class_prob`).
- **Détection de changement, par classe** : raster 4 classes
  (destruction / construction / modification / pas de changement) dans
  `runs/<run-name>/change_detection/<classe>/`.
- **`run_metadata.json`** : commande exacte, paramètres résolus, chemins de
  toutes les sorties, infos modèle et versions des paquets — permet de
  reproduire un run à l'identique.

## 7. Point d'attention

Pour les `xmin` et `ymin`, ces paramètres correspondent à une zone spatiale. Toutes les orthophotos (et nuages de points LiDAR) qui ont le coin **bas-gauche** à l'intérieur de cette zone seront inclus. Cela fait aussi que si aucune origine d'orthophoto n'est trouvée dans cette zone, il y aura un problème. <br/>
Maintenant que cela est dit, un point qui peut amener à des erreurs est le fait que les orthophotos et nuages de points de 2018 sont 4x plus grands que ceux de 2023. Il faut donc faire attention à ces 2 points :
- bien inclure le coin bas gauche des données 2018
- bien mettre le `xmin-end` et `ymin-end` de manière à englober les 3 autres orthophotos de 2023, afin d'avoir les 4 orthos de 2023 qui font la taille de l'unique ortho 2018. 