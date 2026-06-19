# Synthèse LIDARHD

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
  - **Date de création** : 2025-10-15
  - **Date de dernière mise à jour** : 2025-10-16
  - **Version** : 0.2.4
  - **Classification documentaire** : Interne
  - **Langue** : Français
  - **Statut** : Brouillon
  - **Licence** : GNU LGPL v2.1


## LIDAR HD

### Présentation générale

Le programme LIDAR HD, porté par l’Institut national de l’information géographique et forestière (IGN), vise à produire et à diffuser des données LiDAR aériennes à haute densité sur l’ensemble du territoire français.  

Le projet ambitionne de cartographier la France entière en 3D avec une densité moyenne d’environ dix impulsions par mètre carré, soit un niveau de détail jamais atteint à cette échelle. L’ensemble du programme et des produits peut être consulté sur le site officiel de l’IGN : [https://geoservices.ign.fr/lidarhd](https://geoservices.ign.fr/lidarhd).

### Objectifs, usages et diffusion

Le LIDAR HD a pour objectif de créer un référentiel altimétrique national précis, homogène et librement accessible. Ces données servent de base à de nombreux usages : l’aménagement du territoire, la gestion forestière et agricole, la prévention des risques naturels (comme les inondations ou les glissements de terrain), les études environnementales, ou encore la modélisation urbaine en 3D.  
Les produits sont diffusés en open data sous licence Etalab 2.0. Ils peuvent être visualisés ou téléchargés par dalles de 1 km² sur les plateformes de l’IGN.

Mais avant de décrire plus en détails ce qu'est que le LidarHD, nous devons d'abord présenter ce qu'est que la technologe Lidar. 

### Description du fonctionnement du Lidar et des formats de données

#### Fonctionnement général du LiDAR

Le LiDAR (pour Light Detection and Ranging) fonctionne en envoyant de courtes impulsions lumineuses vers le sol ou vers les objets présents dans l’environnement. Chaque impulsion se déplace, touche une surface, puis revient vers le capteur. Le temps qu’elle met pour faire l’aller-retour permet de calculer la distance entre le capteur et le point visé :

$$
\text{Distance} = \frac{t \times c}{2}
$$

où t correspond au temps mesuré et c à la vitesse de la lumière. En répétant ces mesures très rapidement, et en balayant la zone, le système produit un nuage de points 3D qui représente la scène.

Cette technique permet d’obtenir une mesure très précise, de l'ordre de 10cm pour la préision altimétrique et de 20 à 50cm pour la précision planimétrique de la distance entre le capteur et le sol, et donc de construire un nuage de points 3D représentant le relief et les objets de surface (bâtiments, végétation, etc.).

À partir de ces mesures, l’IGN produit différents modèles numériques 
- le Modèle Numérique de Terrain (MNT), qui représente la surface du sol nu 
- le Modèle Numérique de Surface (MNS), qui inclut le sursol (bâtiments, arbres, etc.) 
- et le Modèle Numérique de Hauteur (MNH), obtenu en soustrayant le MNT du MNS pour isoler la hauteur relative des objets.

#### Analyse du signal par rebonds

Lorsqu’une impulsion touche une surface simple, elle renvoie un seul écho. Mais lorsqu’elle traverse un milieu complexe, comme une zone végétalisée, elle peut produire plusieurs retours successifs. Une impulsion peut par exemple toucher la cime d’un arbre, puis les branches intermédiaires, puis enfin le sol. Chaque impact génère un écho différent.

En étudiant le nombre d’échos, leur ordre, leur intensité et leur position, il est possible de déterminer le type de surface rencontré. Une intensité faible correspond généralement à une surface sombre comme le goudron, alors qu’une intensité forte est plus typique de surfaces claires comme l’herbe sèche ou le sable. Les surfaces d’eau renvoient un signal particulier, souvent faible.

Les informations utilisées comprennent :

- le nombre total d’échos ;
- la position de chaque écho dans la séquence ;
- l’intensité du signal réfléchi ;
- la position 3D du point ;
- la distribution locale des points dans la zone.

Ces données servent à classer les points en différentes catégories : sol, eau, végétation, bâtiments et autres.

#### Modèle numérique de terrain (MNT)

Le modèle numérique de terrain représente la forme réelle du sol, sans les objets qui se trouvent dessus. Il est produit à partir des seuls points identifiés comme appartenant au sol. Dans les zones où le laser ne peut pas atteindre directement le terrain, comme sous un pont, des points calculés peuvent être ajoutés pour assurer la continuité du relief.

Le MNT est présenté sous forme de grille régulière. Chaque cellule contient une altitude obtenue par interpolation des points présents dans cette zone. Ce modèle est utilisé pour l’étude des pentes, la modélisation des écoulements, l’analyse des risques ou l’aménagement du territoire.

#### Modèle numérique de surface (MNS)

Le modèle numérique de surface représente l’altitude de tout ce qui se trouve au-dessus du sol : végétation, bâtiments, ponts ou structures diverses. Contrairement au MNT, il conserve tous les points du nuage. Pour chaque cellule de la grille, l’altitude retenue est généralement la valeur la plus élevée trouvée localement.

Le MNS est utile pour analyser la structure du paysage, produire des modèles 3D, visualiser la végétation ou étudier l’organisation du bâti.

#### Usage entre MNT, MNS et modèle de hauteur

Le modèle numérique de hauteur est obtenu en soustrayant le MNT au MNS. Il indique la hauteur des objets situés au-dessus du sol : arbres, bâtiments, pylônes, etc. L’utilisation combinée du MNT, du MNS et du modèle de hauteur donne une description complète du relief et des éléments présents dans l’environnement.

#### Format LAS

Le format LAS est un format binaire normalisé conçu pour stocker des nuages de points LiDAR. Il s’agit d’un standard ouvert publié par l’ASPRS, largement utilisé dans les domaines de la topographie, de la cartographie et de la modélisation 3D.  
Un fichier LAS contient des informations détaillées pour chaque point, notamment :

- les coordonnées x, y, z ;
- l’intensité du retour ;
- le numéro d’écho et le nombre total d’échos ;
- la classe du point (sol, bâtiment, végétation, eau, etc.) ;
- le temps GPS associé ;
- la direction du balayage et l’angle de scan ;
- des attributs additionnels liés au capteur ou au vol.

Les données sont stockées sans compression, ce qui garantit une lecture rapide mais produit des fichiers volumineux, surtout pour les nuages de points denses.

#### Format LAZ

Le format LAZ est la version compressée et sans perte du format LAS. Il utilise un algorithme de compression optimisé pour les données LiDAR, permettant de réduire très fortement la taille des fichiers (généralement un facteur 5 à 10) sans supprimer ni altérer aucune information.

Un fichier LAZ contient exactement les mêmes données qu’un fichier LAS : coordonnées, intensité, classes, échos, temps, angles, etc. La compression facilite le stockage, le transfert et la diffusion de grands jeux de données.  
De nombreux logiciels SIG et outils de traitement LiDAR lisent directement les fichiers LAZ ou les décompressent automatiquement, ce qui en fait un format très utilisé pour la distribution de données altimétriques.


### Contenu et caractéristiques techniques

Les données du LIDAR HD sont fournies au format LAS/LAZ version 1.4, un standard international défini par l’ASPRS (American Society for Photogrammetry and Remote Sensing).

Les données sont découpées en dalles de 1 km² et livrées dans les systèmes de coordonnées officiels, comme RGF93 / Lambert-93 pour la France métropolitaine.  
La classification sémantique des points suit la nomenclature standard ASPRS, enrichie par l’IGN. Elle distingue par exemple :
- le sol (valeur 2),
- la végétation (basse, moyenne ou haute selon la hauteur des points),
- les bâtiments (valeur 6),
- l’eau (valeur 9),
- les ponts (valeur 17),
- ou encore des points virtuels (valeur 66), utilisés pour modéliser le sol sous les structures. 
 
Cette classification est réalisée à l’aide d’algorithmes de traitement géométrique et d’intelligence artificielle, qui analysent la forme, la hauteur et la continuité spatiale des points.

### Qualité et produits dérivés

Le LIDAR HD garantit une précision altimétrique d’environ 10 centimètres et une précision planimétrique de l’ordre de 50 centimètres.  
Les modèles dérivés (MNT, MNS et MNH) sont fournis sous forme de grilles régulières avec une résolution de 50 cm, et une version simplifiée à 5 m pour les usages nécessitant moins de détail (comme les analyses à grande échelle).  
Les produits sont obtenus à l’aide de traitements automatisés, complétés par un contrôle qualité manuel. Deux versions de nuages existent :
- les nuages classés, produits automatiquement à partir des algorithmes ;
- et les nuages optimisés, corrigés manuellement et enrichis de points virtuels pour améliorer la continuité des surfaces, par exemple sous les ponts ou dans les zones boisées denses.

### Périmètre et limites

La couverture nationale du LIDAR HD est planifiée pour être complète d’ici 2025, mais quelques (une dizaine) zones ne sont soit pas encore totalement disponibles pour chaque mesures (manque de MNT, MNS), soit pas disponible du tout. Certaines zones sont exclues pour des raisons de sécurité (zones militaires ou sensibles).  
Chaque version du programme est accompagnée d’un descriptif de contenu détaillé qui documente les formats, les classes et les évolutions. Cette rigueur documentaire garantit la cohérence du référentiel et sa réutilisation dans des contextes variés, scientifiques ou opérationnels.