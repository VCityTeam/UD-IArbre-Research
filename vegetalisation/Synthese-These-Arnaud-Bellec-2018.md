# Synthèse de la thèse d'Arnaud Bellec

| FRANCE 2030 | Banque des Territoires, Groupe Caisse des Dépôts | IA.rbre | LIRIS |
| --- | --- | --- | --- |
| ![Logo FRANCE 2030](../assets/logos/LogoFRANCE2030.svg) | ![Logo Banque des Territoires, Groupe Caisse des Dépôts](../assets/logos/LogoBanquedesTerritoires.svg) | ![Logo IA.rbre](../assets/logos/LogoIArbreWithPicto.svg) | ![Logo LIRIS](../assets/logos/LogoLIRIS.svg) |

---

- Projet
  - **Projet** : IA.rbre
  - **Porteur du projet** : TelesCoop
  - **Membres du consortium** :
    - Métropole de Lyon
    - TelesCoop
    - Université Lumière Lyon 2 (agissant pour le compte du LIRIS)
  - **Durée** : 36 mois (2025 à 2028)
  - **Début** : 2025-03-10
  - **Appel à projet** : Démonstrateurs d’IA frugale au service de la transition écologique de territoires (DIAT)
  - **Plan** : FRANCE 2030
  - **Financement** : Banque des Territoires, Groupe Caisse des Dépôts

---

- Document
  - **Auteur(s)** :
    - Arthur Villarroya-Palau
  - **Relecteur(s)** :
    - Gilles Gesquière
    - John Samuel
  - **Date de création** : 2025-06-15
  - **Date de dernière mise à jour** : 2026-01-08
  - **Version** : 1.0.4
  - **Classification documentaire** : Interne
  - **Langue** : Français
  - **Statut** : Brouillon
  - **Licence** : GNU LGPL v2.1

## Introduction
Au cours des dernières décennies, le concept de ville intelligente a gagné en popularité, devenant un objectif à la fois technologique et sociétal pour répondre aux défis de l’urbanisation moderne. L’un des enjeux importants de cette évolution est le verdissement urbain, l’augmentation et la gestion de la végétation en ville, avec comme objectif l'augementation de la qualité de vie, de l'adaptation aux changements climatiques et de l'aménagement durable.

Pour accompagner ces transformations, il devient important de mieux observer, cartographier et analyser la végétation urbaine. Dans cette perspective, la thèse d’Arnaud Bellec offre un cadre pertinent pour explorer les méthodes utilisants des orthophotographies afin de cartographier la végétation en milieu urbain. Ces approches peuvent contribuer à repérer et délimiter des zones végétalisées ou d’intérêt pour la compréhension et la gestion des dynamiques urbaines.

Comment les données issues de l’observation aérienne peuvent-elles être exploitées pour identifier et caractériser la végétation en ville ? Et en quoi la démarche développée dans la thèse d’Arnaud Bellec permet-elle d’éclairer les choix méthodologiques possibles pour répondre à cet objectif ?

Dans cette synthèse, nous allons voir et expliciter les algorithmes, les techniques/logiciels et les données utilisées pour l’analyse de la végétation urbaine, en nous appuyant sur les travaux menés dans le cadre de la thèse d’Arnaud Bellec.

## Contexe de la thèse d'Arnaud Bellec
La thèse s'inscrit dans le cadre d'une réflexion sur l'évolution de la végétation urbaine à l’échelle métropolitaine, en l'occurrence celle de la métropole de Lyon. Elle vise à analyser, sur près de trente ans, l’évolution de la couverture végétale à partir de données hétérogènes. Les travaux d'Aranud Bellec on été ensuite réutilisés dans le projet **Armature2 et collectif** (https://imu.universite-lyon.fr/armature-2-282507.kjsp?RH=imu_proj et https://collectifs-biodiversite.universite-lyon.fr/carte-dynamique-vegetation/).

## Les données utilisées
La thèse s’appuie principalement sur l’analyse d’orthophotographies de 1984, 1999 et 2015, qui offrent une très haute résolution spatiale (généralement < 1 mètre). Elles permettent de visualiser avec précision les éléments végétaux et les bâtiments, ce qui en fait une donnée très intéressante pour de la segementation précise. Toutefois, leur couverture n’est pas toujours complet sur l’ensemble de la métropole en fonction des millésimes. Ce qui peut poser problèmes pour une visualisation de l'évolution de la végétation. 

Des images satellites (SPOT, Landsat), de résolution plus faible (de l’ordre de 10 à 30 mètres), sont également utilisées. Moins précise mais complètes, elles permettent de faire une évolution dans le temps de la végétalisation de la métropole de Lyon.  

La thèse mobilise aussi des séries d’indices de végétation (notamment le NDVI) dérivées de données satellitaires. Ces indices synthétisent l’intensité végétale d’un pixel, ce qui en fait un outil efficace pour le suivi global, mais leur résolution limite leur usage pour une analyse fine en contexte urbain dense.

Les données LIDAR, disponibles sur les années récentes, apportent une information de hauteur indispensable à l’analyse orientée objet. Elles permettent notamment de distinguer la végétation arborée de la végétation herbacée. Toutefois, leur absence sur les millésimes plus anciens restreint leur usage dans l’analyse temporelle.

Enfin, des couches vectorielles qui décrivent l'occupation des sols sont intégrées afin d'apporter des informations additionnelles pour l’analyse.

## Algorithmes utilisés
Dans le domaine de la télédétection, deux grandes méthodes permettent de classifier l’occupation du sol à partir d’images satellites ou aériennes : **PBIA (Pixel-Based Image Analysis)** et **OBIA (Object-Based Image Analysis)**. Dans cette section nous allons les décrire et résumer ces méthodes et les conclusions sur leurs utilités dans le cadre de la détection de végétaux.  

### L'approche PBIA
#### Défintion
L’approche PBIA (Pixel-Based Image Analysis) consiste à classifier l’image pixel par pixel en fonction des valeurs spectrales. Chaque pixel est traité de manière indépendante, sans considération de son contexte spatial ou morphologique. Cette méthode est historiquement la plus utilisée en télédétection, notamment en raison de sa simplicité et de sa compatibilité avec les capteurs satellites, qui fournissent des images à résolution spatiale moyenne (10m à 30mn), pour lesquelles les objets au sol sont souvent correctement représentés par des pixels uniques. 

#### Algorithme 
Dans la thèse, l’approche PBIA repose sur une classification supervisée. Après avoir comparé plusieurs algorithmes (Minimum Distance, Maximum Likelihood et Spectral Angle Mapper), Arnaud Bellec retient la classification par Maximum Likelihood, jugée plus performante et plus efficace. Cette méthode fonctionne selon les étapes suivantes :

1. **Sélection d’échantillons d’apprentissage** : l’utilisateur définit manuellement des zones représentatives de chaque classe (végétation, bâti, etc.).
2. **Estimation des statistiques** : pour chaque classe, on calcule la moyenne et la variance des valeurs spectrales sur les bandes considérées.
3. **Calcul de la vraisemblance** : chaque pixel est ensuite évalué pour estimer la probabilité d’appartenance à chaque classe. 
4. **Attribution de classe** : le pixel est affecté à la classe pour laquelle il présente la plus forte vraisemblance. 


#### Utilisation
Cette approche est utilisée principalement sur les données satellites SPOT et Landsat. Elle permet d'obtenir des classifications globales, utiles pour des analyses à large échelle. Toutefois, en contexte urbain, elle montre ses limites: elle est sensible au bruit spectral, ignore les structures spatiales, et produit souvent des résultats imprécis ou des artefacts comme du bruit poivre et sel. Elle n'est donc pas adapté à une résolution très fine (comme avec les ortophotographies), et à des lieux hétérogènes et complexe comme le milieu urbain. 

### L'approche OBIA
L’approche OBIA (Object-Based Image Analysis) repose sur l’idée que l’unité d’analyse ne doit plus être le pixel, mais un objet spatial homogène. Elle commence donc par une segmentation de l’image en objets, puis applique une classification à ces objets en prenant en compte des critères spectro-spatiaux (forme, texture, voisinage, etc.). Cette approche est particulièrement adaptée aux images à haute résolution, notamment les orthophotographies. C'est aussi ici que l'approche est alimentée avec d'autres données comme les données LiDAR ou NDVI qui apportent une information supplémentaire.

#### Défintion
#### Algorithme et eCognition
La segmentation est réalisée avec l’algorithme de **segmentation multirésolution** de Baatz & Schäpe (2000), implémenté dans le logiciel **eCognition**. Cet algorithme fonctionne selon un processus itératif de fusion de régions, contrôlé par une fonction d’optimisation de l’hétérogénéité. Voici ses étapes clés :

1. **Initialisation** : chaque pixel est considéré comme un objet.
2. **Fusion progressive** : des paires d’objets voisins sont évaluées pour fusion, en minimisant un coût d’hétérogénéité.
3. **Critère d’hétérogénéité** : il combine deux composantes :
   - **h_color** (spectrale) : la différence de couleur ou de valeurs spectrales entre les objets (ex. : vert clair vs vert foncé).
   - **h_shape** (spatiale) :  la différence de forme, selon :
      - la compacité (est-ce que l’objet est bien « rond » ou plutôt étiré ?), 
      - et le lissage (est-ce que les contours sont régetuliers ou très irréguliers ?).
   Chacune de ces deux parties (couleur et forme) reçoit un poids:
      - importance donnée à la couleur,
      - importance donnée à la forme.
Ces poids doivent s’additionner à 1 (par exemple : 0,8 pour la couleur et 0,2 pour la forme).
4. **Paramètre d’échelle** : Ce paramètre sert à contrôler la taille des objets finaux.
C’est un seuil de tolérance : plus il est élevé, plus l’algorithme accepte de fusionner des objets même s’ils sont un peu différents → cela donne des objets plus gros. Inversement, un petit paramètre d’échelle signifie que l’algorithme est plus exigeant : il fusionnera seulement des objets très semblables → cela produit des objets plus petits et nombreux.

5. **Arrêt** : la segmentation s’arrête lorsque plus aucune fusion ne respecte le seuil d’hétérogénéité défini par l’utilisateur.

eCognition permet ensuite d’enrichir les objets segmentés avec des attributs statistiques (moyenne NDVI, LIDAR, etc.) et d’y appliquer une classification.

#### Utilisation
Dans la thèse, cette méthode est appliquée aux orthophotographies de 1984, 1999 et 2015. Les objets segmentés sont enrichis avec la hauteur issue du LIDAR (lorsqu'elles sont disponible) pour distinguer végétation basse et haute. La classification hiérarchique distingue dans un premier temps les grandes classes (végétation, bâti, eau), puis des sous-classes (herbacée, arborée, etc.). Les résultats sont robustes et mieux adaptés à la complexité de l’environnement urbain. Les objets classifiés sont enfin intégrés dans un SIG pour analyse et visualisation.

### Vérification des résultats

Dans sa thèse, Arnaud Bellec vérifie les résultats des classifications obtenues par les approches PBIA et OBIA à l’aide de matrices de confusion, construites à partir de points d’échantillonnage indépendants. Ces points, définis manuellement à partir des orthophotographies, permettent d’évaluer la qualité des classifications selon des indicateurs standards tels que l’exactitude globale (OA), le coefficient Kappa, ainsi que les précisions utilisateur et producteur. Les classifications PBIA sont validées uniquement si elles atteignent un Kappa supérieur ou égal à 0,8, garantissant une fiabilité minimale. Pour OBIA, les résultats atteignent systématiquement ces seuils, avec des valeurs de Kappa allant jusqu’à 0,87–0,91, confirmant une meilleure performance. Ces évaluations mettent en évidence l’intérêt méthodologique de l’approche OBIA, plus précise et plus robuste que PBIA pour cartographier la végétation urbaine à partir d’orthophotographies à haute résolution.

### Compartif des deux approches et résumé

#### PBIA (Pixel-Based Image Analysis)
1. Acquisition d’images satellites (SPOT, Landsat)
2. Sélection manuelle d’échantillons d’apprentissage
3. Classification pixel par pixel (Maximum Likelihood)
4. Génération d’une carte raster (bruitée)
5. Validation par matrice de confusion (400 points aléatoires)

### OBIA (Object-Based Image Analysis)
1. Acquisition d’orthophotographies + données LIDAR
2. Segmentation multirésolution (eCognition)
3. Calcul d’attributs par objet 
4. Classification par règles logiques ou floues
5. Génération d’une carte d’objets 
6. Validation par matrice de confusion (échantillons objets)

## Comparaison des résultats des deux approches

| Critère                     | PBIA                                             | OBIA                                              |
|-----------------------------|--------------------------------------------------|---------------------------------------------------|
| Données utilisées           | Satellites SPOT, Landsat                         | Orthophotos + données LIDAR                   |
| Résolution                  | Moyenne (10–30 m)                                | Très haute (< 1 m)                                |
| Unité d’analyse             | Pixel                                            | Objet (groupe de pixels)                          |
| Méthode de classification   | Maximum Likelihood                         | Règles logiques / hiérarchiques dans eCognition   |
| Validation                  | Matrice de confusion (400 points)               | Matrice de confusion (objets échantillonnés)      |
| Exactitude globale (OA)     | ≥ 0,8                                            | Jusqu’à 0,91                                      |
| Coefficient Kappa           | ≥ 0,8 requis pour valider la carte              | Entre 0,82 et 0,91 selon les années               |
| Résultat visuel             | Carte bruitée, artefacts « poivre et sel »      | Carte cohérente, objets homogènes                 |
| Usage dans la thèse         | Complément pour l’analyse globale                | Méthode principale pour cartographie fine         |


## Conclusion
La thèse d’Arnaud Bellec met en évidence l’intérêt d’une approche orientée-objet pour l’analyse de la végétation urbaine. En comparant les approches PBIA et OBIA, elle montre les avantages méthodologiques qu’offre la segmentation multirésolution associée à eCognition, notamment pour tirer parti des orthophotographies à haute résolution. La coté temporalisation n’est pas au cœur de notre analyse, car si elle est le centre de la thèse d'Arnaud Bellec, ce qui nous est intéressant c'est la technique utilisé afin de cartographier la végétalisation et d'en suivre l'évolution. Pour cet objectif, le recours à OBIA permet une meilleure représentation des objets géographiques urbains, en particulier dans les contextes hétérogènes, en tirant parti des structures, de la forme, et de la hiérarchisation des objets. Ce travail ouvre également la voie à l'utilisation de modèle plus avancé afin d'éviter de devoir réutiliser la méthode sur un logiciel tier.

Les résultats de la cartographie de la végétalisation peuvent être retrouvés sur le site de [DataGrandLyon](https://data.grandlyon.com/portail/fr/jeux-de-donnees/vegetation-stratifiee-2018-metropole-lyon/info). C'est une donnée raster à très haute résolution (1m*1m par pixel).

Elle est classé comme suivant:

1. **Herbacées**
2. **Buissons** : (<1,5m)
3. **Arbustes** : (1,5 - 5 m)
4. **Petites Arbres** : (5 - 15m)
5. **Grands Arbres** : (>15m)

![Exemple du raster focus sur le parc de la tête d'or et le campus La Doua](images/carteVegDoua.png)
``Exemple du raster focus sur le parc de la tête d'or et le campus La Doua``

## Bibliographie/Webographie
Bellec, A. (2018). **Observer et spatialiser l’armature verte métropolitaine à partir de données hétérogènes.** Thèse de doctorat, Université de Lyon. [PDF](https://scd-resnum.univ-lyon3.fr/out/theses/2018_out_bellec_a.pdf)

Baatz, M. and Schape, A. (2000) **Multiresolution Segmentation: An Optimization Approach for High Quality Multi-Scale Image Segmentation**. [PDF](https://fr.scribd.com/document/155270890/baatz-schaepe)

Benz, U. C., Hofmann, P., Willhauck, G., Lingenfelder, I., & Heynen, M. (2004). **Multi-resolution, object-oriented fuzzy analysis of remote sensing data for GIS-ready information.** ISPRS Journal of photogrammetry and remote sensing, 58(3-4), 239-258. [PDF](https://d1wqtxts1xzle7.cloudfront.net/15815968/benz_etal_2004_jprs-libre.pdf?1390864490=&response-content-disposition=inline%3B+filename%3DMulti_resolution_object_oriented_fuzzy_a.pdf&Expires=1750955626&Signature=GAzezvJ7qdztTfWQmYGgU7vOU~hhNLH33ykXPSU1egIqWx4vlBPNPhS9VAki0NoYQCNQgduEbwLmHgdSzJlNcizqEj~vlkkRKfwSCJlE-5T0720K~qXzvV6ItUUXxUBMuUk1LHzwbRVZo-OfZ~HHAigdiNJDOspoHjiZnFO9OC290tFy2t5YhRrRBpTb0v7fKs1XhdX-sxqvHBsD60kKgSJ~91PpmW-F2Sg6tsYrsbCEiYvDpnZi6fMfkif0ST69OpP5-cH2Wr6nG5B-27yO1hEPF-YDw0rYX9fAjjM8CJwKgw4w8nMzKWXLZgqBq9IxFykoFXm9jQ~M6m6aBFwZuw__&Key-Pair-Id=APKAJLOHF5GGSLRBV4ZA)

Nasiri, V., Hawryło, P., Janiec, P., & Socha, J. (2023). **Comparing object-based and pixel-based machine learning models for tree-cutting detection with planetscope satellite images: Exploring model generalization.** International Journal of Applied Earth Observation and Geoinformation, 125, 103555. [PDF](https://pdf.sciencedirectassets.com/272637/1-s2.0-S1569843223X00090/1-s2.0-S1569843223003795/main.pdf?X-Amz-Security-Token=IQoJb3JpZ2luX2VjEPX%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLWVhc3QtMSJIMEYCIQDNyktZPsKyiMBBzAIkZgmz8rh70lrD3YD0JJVFdKEHZQIhAKjSVZ%2BiUClZh%2BfIUTcUplLWiJajCkLh%2B%2FTSUmq73xJ%2FKrsFCO3%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQBRoMMDU5MDAzNTQ2ODY1IgwZAGyZ6DS0686AxFwqjwXcx88DYzvr4soDgDmYc5sbon8%2Bur33bb8kgcoFEDrPLFiHnKqmDuoPf9BsMiPgkLSAZRNhSEmtLja%2FF7BcWlRphabXe0c8efiZ%2FM6cvaXXzANBfZ52c5dUs174yFUkt5i6LhB7PU6rhl97YJ21EoV%2B6Ofo5lyxwkI8kgcVoKfuPtEOhhNEvjZHu6H6JnoYPRQ0tBV6zdG3SRg4iP4xaew7tgputMo%2FcjxncxrLqYlYmez5uFof7ss%2BikLocuy9huwhaRNSwrzGp4ETetSwoiMWqHJXRo7PTWAFiv6%2FFWkZ5LKp1bInpj4G0UV98XXDNXQlomIW6rMK0XgonYIPhnuIBrdfCuD88VM5ugljXRsS1Vupa4tMTfGFikiN6Y8tq%2FF05Dk1Orvv4NWA4Yjyb3oIcRU9jJMOR6av7uxTqAi8aEQDGGo7lE%2FY6yHp5APNbsCitsUBNe2mmvxyf2OeQaMhjAINq8%2B7HOoNuuTLAEYnRBvvhC2CuzDX5M%2FdStMRQRSafs9RL7JxaWhtcM%2FL%2FyhAxLVYCZ3NKWwd0XUUhJd0Yoh0gAwx1lWuDVkvBM%2FOzQOb4PEeBFLjbDp%2BIhOdcI9855vdRjPlJluDDOZOV8%2FaVzHXt%2FCy7wtzy6q3JdReALo3Xmf5v6aiBKZWGSn%2Be4R%2BtA0Ss8ra29wtrSTzTzC%2F4LUvl64PejzA3WrCfGU021i54fe74wlTMEKkavblVkJg5fjCjIcv3%2FYVgw6Mj%2Fwc0grBK5dqFlHcPxQuP3iSkJeVuikV1usyDgG3p38lJGmtEQawIozegOCts6Ui4GfqGrA4GjX3P9ueVmS4aPl5x0mUGabRLfbzRm%2FzRvB%2FOw0GLUE4NFiGRZwH9fBjVkpFMP7ElMMGOrABnpdC4mVvoFTZ71I7wL74sDZPjs8nYFbmyQp3T0BoTGCiBUFgVVojzP0xVR0KvF89wOzA0xaG8lZIv6Y6Vg8jzbWNdtk%2FxLCKif4HzLrsvMacY9Frl5rcNIiCz2Vbt4aqrK00NKcxkeGwqOx%2FEKVzkTqka6p%2B7EMZbOOyPkQtrAqJBS3E6WBVbjARxgxmpy%2FfjbC6Db5eqWNoRM4k10ZrUj49RGI1WrSwxuSOV6Ktg8g%3D&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20250702T130629Z&X-Amz-SignedHeaders=host&X-Amz-Expires=300&X-Amz-Credential=ASIAQ3PHCVTY4QVPEH4M%2F20250702%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Signature=f6e64fd6f9b919d7bba016e97b822bdd19887181bdfe010d4ad35ea33e5518f0&hash=7f1a451655babebbb4c15fa9d49ec3589b97e50d9f65d479391a11d220e164c5&host=68042c943591013ac2b2430a89b270f6af2c76d8dfd086a07176afe7c76c2c61&pii=S1569843223003795&tid=spdf-6400e44d-1722-4389-9630-8be31e19198d&sid=42915ea579af754db95b2105ef1a6a039bf4gxrqb&type=client&tsoh=d3d3LnNjaWVuY2VkaXJlY3QuY29t&rh=d3d3LnNjaWVuY2VkaXJlY3QuY29t&ua=1c145d535c055207575552&rr=958e5c551ad0c0a4&cc=fr)

Logiciel **eCognition** [Lien](https://www.gim.be/fr/produits/software/ecognition#:~:text=eCognition%20%3A%20l'application%20logicielle%20d,d'images%20satellitaires%20par%20excellence&text=eCognition%20de%20Trimble%20vous%20permet,technologie%20aussi%20rapide%20que%20pr%C3%A9cise)
