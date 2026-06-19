# Synthèse Myria3D

| FRANCE 2030 | Banque des Territoires, Groupe Caisse des Dépôts | IA.rbre | LIRIS |
| --- | --- | --- | --- |
| ![Logo FRANCE 2030](../assets/logos/logo-france-2030.svg) | ![Logo Banque des Territoires, Groupe Caisse des Dépôts](../assets/logos/logo-banque-des-territoires.svg) | ![Logo IA.rbre](../assets/logos/logo-iarbre-with-picto.svg) | ![Logo LIRIS](../assets/logos/logo-liris.svg) |

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
  - **Date de création** : 2025-10-15
  - **Date de dernière mise à jour** : 2025-12-18
  - **Version** : 1.0.2
  - **Classification documentaire** : Interne
  - **Langue** : Français
  - **Statut** : Brouillon
  - **Licence** : GNU LGPL v2.1

## Myria3D

### Présentation et objectifs

Myria3D est une bibliothèque open source de deep learning développée par l’IGN pour la segmentation sémantique de nuages de points LiDAR à haute densité.  
La segmentation sémantique consiste à attribuer à chaque point d’un nuage une étiquette correspondant à sa catégorie (sol, bâtiment, arbre, véhicule, etc.). C’est une tâche complexe car les nuages de points peuvent contenir plusieurs millions de points sans structure régulière, contrairement à une image.  
Myria3D a été conçue en tenant compte des contraintes du programme LIDAR HD : densité élevée (jusqu’à 10 points par m²), grande variabilité des scènes (urbaines, forestières, rurales) et volume massif de données à traiter.  
Elle permet d’entraîner des réseaux de neurones 3D, d’évaluer leurs performances et de réaliser des inférences sur de vastes ensembles de points. Bien qu’inspirée par les besoins de LIDAR HD, Myria3D est indépendante et peut être utilisée pour d’autres jeux de données LiDAR.  
La documentation officielle est disponible à cette adresse : [https://ignf.github.io/myria3d-docs](https://ignf.github.io/myria3d-docs), et le code source sur GitHub : [https://github.com/IGNF/myria3d](https://github.com/IGNF/myria3d). 

### Architecture de Myria3D : RandLA-Net et héritage de PointNet++

Myria3D utilise principalement RandLA-Net, un réseau de neurones conçu pour la segmentation sémantique de nuages de points 3D (Hu et al., 2020).
Un nuage de points est un ensemble de points dans l’espace 3D, où chaque point est défini par ses coordonnées (x, y, z) et parfois par d’autres informations comme la couleur ou l’intensité.

Le principe central de RandLA-Net est un traitement hiérarchique basé sur un échantillonnage aléatoire des points.
Concrètement, le réseau ne traite pas tous les points du nuage en même temps.
À chaque étape, il sélectionne un sous-ensemble de points, de plus en plus réduit, afin de limiter la quantité de données manipulées.
Cette stratégie permet de traiter des nuages de points très volumineux tout en conservant une représentation globale de la scène.
Les détails du fonctionnement de cet échantillonnage sont décrits dans l’article de Hu et al. (2020).

Pour éviter de perdre des informations importantes lors de cette réduction, RandLA-Net utilise un module appelé Local Feature Aggregation (LFA), dont le rôle est d’extraire des informations locales autour de chaque point sélectionné.
Ce module repose sur trois idées principales :
  1. **Encodage spatial local (LocSE)** : le réseau prend en compte la position relative des points voisins afin de comprendre la forme locale des objets (par exemple, une surface plane ou un bord).
  2. **Pondération des voisins** ou **Attentive Pooling** : tous les points voisins n’ont pas la même importance. Le réseau apprend à donner plus de poids aux points les plus informatifs lors du calcul des caractéristiques locales.
  3. **Prise en compte d’un voisinage élargi** ou **blocs résiduels avec dilatation**: le réseau combine progressivement les informations issues de voisinages de plus en plus larges, ce qui lui permet d’intégrer davantage de contexte autour de chaque point.

RandLA-Net s’inscrit dans la continuité des travaux sur l’apprentissage dans les nuages de points, et notamment de PointNet++ (Qi et al., 2017).
PointNet++ a introduit un traitement hiérarchique par voisinages, où les points proches sont regroupés pour apprendre des informations locales, puis combinés à des niveaux plus globaux.
Il a également montré l’intérêt d’analyser les données à différentes tailles de voisinage, afin de prendre en compte à la fois les détails fins et la structure générale des objets.

RandLA-Net reprend ces principes généraux, mais les adapte au traitement de nuages de points de grande taille, en mettant l’accent sur une organisation simple et efficace du calcul.
Les résultats expérimentaux, ainsi que les comparaisons avec d’autres approches, sont présentés et analysés dans l’article original de Hu et al. (2020).

Si vous souhaitez en savoir plus sur RandLA-Net et PointNet++ nous vous invitons à lire les papiers décrivants les deux algorithmes: RandLA-Net: Efficient Semantic Segmentation of Large-Scale Point Clouds [https://arxiv.org/abs/1911.11236](https://arxiv.org/abs/1911.11236); PointNet++: Deep Hierarchical Feature Learning on Point Sets in a Metric Space [https://arxiv.org/abs/1706.02413](https://arxiv.org/abs/1706.02413)

### Pipeline de traitement et apprentissage

Le pipeline de Myria3D est conçu pour traiter efficacement des données massives.  
Avant l’entraînement, les nuages de points sont sous-échantillonnés à l’aide du GridSampling de PyTorch-Geometric, généralement avec une résolution d’environ 25 centimètres. Cette étape réduit le nombre de points à traiter (souvent de 30 à 40 %) tout en conservant la forme générale des objets.  
Myria3D intègre une version de RandLA-Net, capable de traiter des nuages de tailles variables dans un même lot d’entraînement. Cela améliore la flexibilité et réduit la consommation mémoire.

Pendant l’entraînement, le modèle apprend à partir de ces nuages sous-échantillonnés. Les prédictions (appelées logits) ne sont pas interpolées vers le nuage complet à ce stade, afin d’accélérer le calcul. Lors de la phase de test ou d’inférence, les prédictions sont interpolées sur l’ensemble du nuage pour obtenir une évaluation complète.  
Les performances sont mesurées à l’aide de l’indicateur Intersection over Union (IoU), qui compare la correspondance entre les classes prédites et les classes réelles.

### Principes de conception et réutilisation

Myria3D a été conçue selon quatre principes : rapidité, performance, simplicité et fiabilité. Elle vise à fournir un cadre efficace pour expérimenter et produire des modèles de segmentation à grande échelle.  
Une version stable de la bibliothèque, appelée Production Release, inclut un modèle préentraîné multiclasses et les fichiers de configuration nécessaires pour réaliser des inférences sur des données similaires à celles du LIDAR HD.  
Grâce à sa nature open source, Myria3D peut être réutilisée pour d’autres projets de recherche ou de production, que ce soit pour des villes, des forêts ou d’autres environnements 3D.

Des essais ont été réalisés à partir de la documentation officielle afin d’installer et d’exécuter la bibliothèque dans le but de mener quelques tests. Malgré plusieurs jours d’expérimentation, il n’a pas été possible de la faire fonctionner complètement, en raison de la complexité de sa configuration et des dépendances logicielles requises. Toutefois, la structure du code, son organisation claire et sa logique de traitement constituent un exemple particulièrement intéressant de mise en œuvre d’architectures de deep learning appliquées aux nuages de points 3D.