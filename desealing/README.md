# README

## Description

Ce projet a pour objectif de fournir un outil permettant de traiter et d'analyser des données géospatiales pour générer des fichiers Shapefile (.shp) exploitables en utilisant la méthode des casiers ou la méthode de l'indice de Beven-Kirkby (IBK). 

Voici un aperçu des fonctionnalités principales :

- **lecture.py** : Ce fichier contient les fonctions nécessaires pour lire et interpréter les données d'entrée, telles que les fichiers MNT (Modèle Numérique de Terrain) et le fichier d'imperméabilité, tout deux des fichiers raster au format .tif.

- **methode.py** : Ce fichier implémente les différentes méthodes de calcul utilisées pour analyser les données, notamment les algorithmes de segmentation en casiers et les calculs de pente.

- **visualisation.py** : Ce fichier propose des outils pour visualiser les résultats intermédiaires ou finaux, facilitant ainsi l'interprétation des données traitées.

- **main.py** : Ce fichier est le point d'entrée principal du projet. Il orchestre l'exécution des différentes étapes en fonction des paramètres fournis par l'utilisateur.


## Prérequis

Pour exécuter le code, assurez-vous d'avoir les éléments suivants installés :
- Python 3.8 ou plus
- Bibliothèques Python requises (voir `requirements.txt`)

Installez les dépendances avec la commande suivante :
```bash
pip install -r requirements.txt
```

## Utilisation

Pour pouvoir executer ce code, pensez d'abord à vous mettre dans le repertoire `desealing` avec la commande:
```bash
cd Chemin vers le dossier/desealing
```

Ensuite, il existe deux façons de lancer le code:
 - Avec un fichier de configuration au format YAML créé au préalable (les informations sur l'utilité des arguments est dans le fichier configtemplate.yaml)
 - En passant tous les arguments en ligne de commande

Avec un fichier de configuration, la commande à faire est:
```bash
python main.py -c nom_du_fichier_de_configuration.yaml
```

Un fichier configtemplate.yaml est disponible avec tous les arguments existants et leur entrées nécessaires, ainsi qu'une précision sur la nécessité ou non des certains paramètres en fonction de la méthode utilisée.

Les arguments sont:
-m/--method: Permet de choisir la méthode, et par conséquent le code à lancer
-t/--tile_path: Permet de choisir un fichier MNT au format raster (tuiles avec informations sur l'atimétrie de la zone)
-i/--imperviousness_path: Permet de choisir un fichier au format raster contenant des informations sur la perméabilité de la zone
-out/--output_path: Pour stocker les fichiers de sortie du code, au formats .shp, .shx, .prj, .dbf, .cpg
-slope/--slope: Permet de choisir la méthode de calcul de pente et d'aggrégation dans les casiers
-cs/--casiersize: Taille des casiers en mètres (choisir des valeurs entières)
-if/--imperviousness_factor: Poids du score d'imperméabilité. On a Indice_désimper = score_imper * imperviousness_factor + score_pente * slope_factor, avec slope_factor = 1 - imperviousness_factor

Si vous préferez passer tous les arguments en ligne de commande, la commande est plus complexe: 
```bash
python main.py -t "chemin vers le fichier MNT" -i "chemin vers le fichier d'imperméabilité" -m "methode" -cs "taille de casier en mètre" -slope "méthode de calcul de pentes" -if "facteur entre 0 et 1 pour poids de l'imperméabilité" -out "chemin vers le dossier où mettre le fichier .shp résultant"
```

Exemple: 
```bash
python main.py -t "data/mnt.tif" -i "data/imperviousness.tif" -m "casier" -cs 10 -slope "best_fit_plane" -if 0.4 -out "./output"
```


## Résultats

Les résultats de ce code sont stockés dans le répertoire `output/`. Ces résultats sont au format Shapefile (.shp)

## Licence

Ce projet est sous licence LGPL.
