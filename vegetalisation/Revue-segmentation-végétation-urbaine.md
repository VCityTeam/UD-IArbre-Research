# Revue sur la segmentation de la végétation urbaine par Deep Learning

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
    - Arthur Villarroya-Palau (LIRIS, 	arthur.villarroya-palau@liris.cnrs.fr)
  - **Relecteur(s)** :
    - Gilles Gesquière
    - John Samuel
  - **Date de création** : 2025-09-12
  - **Date de dernière mise à jour** : 2026-02-06
  - **Version** : 1.1.9
  - **Classification documentaire** : Interne
  - **Langue** : Français
  - **Statut** : Brouillon
  - **Licence** : GNU LGPL v2.1

---

## Introduction  

La végétation occupe une place de plus en plus importante dans les villes modernes. Elle contribue directement à la régulation climatique locale, en réduisant les îlots de chaleur urbains et en améliorant la qualité de l’air. Elle participe également au maintien de la biodiversité et à la gestion des eaux pluviales en limitant l’imperméabilisation des sols et en favorisant l’infiltration. Dans le contexte des *villes intelligentes*, disposer d’informations fiables et actualisées sur la répartition, la composition et l’évolution de cette végétation est un enjeu stratégique. De tels inventaires permettent d’évaluer ses influences multiples et d’adapter en conséquence les politiques d’aménagement, les projets de construction ou encore les stratégies de résilience urbaine.  

La constitution d’un inventaire précis de la végétation urbaine repose sur une étape préalable de segmentation. Celle-ci vise à identifier automatiquement, dans des données spatiales variées (*orthophotos*, imagerie multispectrale, nuages de points LiDAR), les surfaces correspondant à de la végétation, et à distinguer éventuellement leurs différentes strates (*basse, moyenne, haute*). L’enjeu n’est pas uniquement de détecter la végétation présente à un instant donné, mais surtout de trouver une procédure réplicable et reproductible, adaptée à des données hétérogènes et des contextes variés.  

Historiquement, ces inventaires ont reposé sur des approches de télédétection optique. L’utilisation d’indices spectraux tels que le *NDVI* (Normalized Difference Vegetation Index) a constitué un première étape, en permettant de distinguer de manière robuste les zones de basses végétation et de hautes végétation. Ces méthodes ont ensuite été enrichies par des procédures de classification supervisée, combinant critères spectraux, texturaux et géométriques. Bien que pertinentes dans certains contextes, ces approches présentent toutefois des limites spécifiques. Dans le cas des indices spectraux tels que le NDVI, les résultats restent imparfaits, sont grandement dépendant du calibrage des capteurs et ne permettent pas de différencier correctement les strates de végétation. Quant aux méthodes de classification supervisée, si elles offrent de bons résultats dans certains contextes, leur mise en œuvre s’appuie souvent sur des chaînes de traitement complexes et parfois dépendantes d’outils logiciels peu transparents. Cette opacité complique la réplicabilité des résultats et rend difficile la reproduction à grande échelle des inventaires urbains. Les travaux d’Arnaud Bellec (2018) sur l'analyse de la végétation de la ville de Lyon constituent un exemple représentatif de cette difficulté, en mettant en lumière les contraintes liées à la disponibilité et à la reproductibilité des données. Une analyse plus détaillée de ce cas sera présentée dans une section spécifique.

Plus récemment, les méthodes d’apprentissage profond (Deep Learning, DL) sont de plus en plus utilisées dans le cadre de la segmentation d’images *2D* et, dans une moindre mesure, de nuages de points *3D*. Des architectures comme les *CNN*, *U-Net*, *FCN* ou plus récemment les *Transformers visuels* (ex. *Swin Transformer*) sont désormais largement mobilisées pour la segmentation des données spatiales. Dans le cas particulier des nuages de points LiDAR, le DL ne permet pas encore une analyse fine et détaillée des structures, mais il constitue une approche efficace pour produire une première segmentation large du terrain et obtenir rapidement des résultats exploitables.  

Dans ce travail, nous présentons d’abord les enjeux liés à la végétation urbaine, en mettant en évidence l’importance écologique et sociale de ces espaces ainsi que les difficultés méthodologiques liées à la caractérisation fine des différentes strates végétales. Nous abordons ensuite les approches de segmentation d’images, en retraçant l’évolution des méthodes classiques de télédétection et de classification supervisée vers les approches plus récentes basées sur le Deep Learning. La troisième partie est consacrée aux outils et ressources existants, mobilisés dans le contexte de la végétation urbaine : plateformes de segmentation d’images comme FlairHub et COSIA, données tridimensionnelles comme le LiDAR HD et Myriad 3D, ainsi que vérités terrain ponctuelles issues de travaux antérieurs. Sur cette base, nous proposons une analyse critique et comparative des approches dans le cadre du projet IA.rbre, avant d’explorer une approche alternative susceptible de permettre des résultats améliorés pour les inventaires de végétation urbaine.

---

## La végétation urbaine
La végétation en milieu urbain joue un rôle essentiel tant pour les habitants que pour l’équilibre écologique des villes. De nombreuses études ont montré que la proximité d’espaces verts contribue à améliorer le bien-être physique et psychologique des citadins, tout en apportant des services écosystémiques multiples tels que le captage du carbone, la régulation thermique via l’atténuation des îlots de chaleur urbains, ou encore la réduction du bruit et la dépollution atmosphérique par dépôt et dispersion des particules fines. (Janhäll, 2015) ou (Klingberg et al., 2017)  insiste par exemple sur le rôle différencié des espèces et des structures végétales dans la capture et la dispersion des polluants, soulignant combien la structure et la typologie de la végétation influencent directement la qualité de l’air urbain.

Dans ce contexte, établir un inventaire précis et actualisé de la végétation urbaine apparaît comme une condition indispensable à la gestion durable des territoires. Comme l’ont montré plusieurs travaux de synthèse sur l’évolution de la végétation en milieu urbain, et comme (Richards & Belcher, 2021) met en évidence l’ampleur des transformations récentes de la couverture végétale des villes à l’échelle mondiale, et rappelle que l’évolution des strates végétales a un impact direct sur les services rendus par ces espaces. La végétation connaît des transformations profondes sous l’effet des dynamiques d’urbanisation et des changements climatiques. Or, ces évolutions rendent difficile le suivi à grande échelle lorsqu’il repose uniquement sur des relevés de terrain, en raison du coût et du temps requis. Cela explique l’essor des méthodes de cartographie automatisée à partir d’images de télédétection, qui cherchent à dépasser les approches binaires (végétation présente / absente, haute / basse) pour proposer une caractérisation plus fine des différentes strates. L’hétérogénéité spatiale et spectrale des environnements urbains, la complexité tridimensionnelle des structures et la diversité des modes de gestion locale constituent des obstacles majeurs à l’inventaire. Ces limites concernent notamment la strate moyenne (arbustes, haies, jeunes arbres), dont la détection et la caractérisation restent particulièrement complexes en raison de la variabilité morphologique et de l’absence de critères standardisés dans l’espace urbain.

En somme, réaliser un inventaire précis de la végétation urbaine est essentiel pour mieux comprendre et gérer les espaces verts en ville. Cela permet de suivre l’évolution du couvert végétal, d’évaluer les services rendus à l’environnement et aux habitants, et de guider les politiques d’aménagement. Toutefois, cette tâche reste complexe en raison de la forte hétérogénéité des environnements urbains : la diversité des formes bâties, des matériaux et des types de végétation perturbe les mesures issues de la télédétection et rend difficile une caractérisation homogène des strates. Ces difficultés soulignent la nécessité d’élaborer des protocoles adaptés au contexte urbain, capables de prendre en compte la variabilité spatiale et structurelle propre aux villes.

---

## Approche pour la segmentation d'images
La segmentation d’image constitue l’une des problématiques centrales de l’analyse visuelle et de la vision par ordinateur. Elle vise à diviser une image en régions cohérentes associées à des objets ou à des structures du monde réel, permettant de passer d’un signal brut, une matrice de pixels à une représentation organisée et exploitable. Dans ce cadre, la segmentation peut prendre plusieurs formes : la segmentation sémantique, qui assigne à chaque pixel une classe (par exemple « bâtiment », « route », « arbre ») ; la segmentation d’instance, qui distingue les objets individuels appartenant à une même classe (par exemple chaque arbre séparément) ; et la segmentation panoptique, qui combine les deux approches en intégrant à la fois la classification des pixels et l’individualisation des objets. Ces différentes modalités répondent à des besoins variés selon les applications, qu’il s’agisse de l’imagerie médicale, de la robotique, de la conduite autonome ou de la télédétection.

Dans le champ spécifique de l’observation de la Terre, la segmentation permet de délimiter des surfaces cohérentes à partir d’images satellites ou aériennes, en vue d’identifier des catégories d’occupation du sol ou des structures géographiques particulières. Elle intervient à différentes échelles : du découpage grossier entre zones bâties, agricoles et naturelles, jusqu’à la distinction fine des composantes internes d’un paysage, telles que les cultures, les routes, les plans d’eau ou les espaces végétalisés. En ville, cette capacité à isoler et caractériser les objets est particulièrement stratégique, car les environnements urbains se distinguent par leur forte hétérogénéité spatiale et fonctionnelle, où coexistent sur de petites surfaces des matériaux variés, des bâtiments de morphologies différentes et des éléments naturels de tailles et de formes diversifiées.

Dans ce contexte, la segmentation de la végétation urbaine constitue un enjeu majeur car elle illustre bien les difficultés méthodologiques posées par l’hétérogénéité des environnements urbains. Réussir à distinguer correctement les espaces verts dans leur diversité comme les arbres, les pelouses, les surfaces arbustives, est indispensable pour produire des inventaires fiables permettant ensuite d’évaluer leurs fonctions écologiques et leur interaction avec les autres composantes de la ville. Pourtant, cette tâche reste difficile à automatiser de manière robuste. Les variations spectrales induites par la diversité des espèces, l’influence de l’éclairage et des ombres portées, les effets tridimensionnels liés à la hauteur des objets ou encore la proximité immédiate du bâti compliquent la segmentation automatique. À cela s’ajoute la nécessité de produire des résultats généralisables, comparables d’un territoire à l’autre et reproductibles dans le temps, afin d’accompagner efficacement les services urbains dans leurs besoins opérationnels.

Dans ce contexte, la segmentation de la végétation urbaine constitue un enjeu majeur car elle illustre bien les difficultés méthodologiques posées par l’hétérogénéité des environnements urbains. Identifier correctement les espaces verts dans leur diversité comme les arbres, les pelouses, les surfaces arbustives est indispensable pour établir des inventaires fiables, permettant ensuite d’évaluer leurs fonctions écologiques et leurs interactions avec les autres composantes de la ville. Pourtant, cette tâche reste difficile à automatiser de manière robuste. Les variations spectrales liées à la diversité des espèces, l’influence de l’éclairage et des ombres portées, les effets tridimensionnels associés à la hauteur des objets, ainsi que la proximité immédiate du bâti compliquent considérablement la segmentation automatique. À ces défis techniques s’ajoute une dimension sémantique : les définitions et niveaux de détail attendus peuvent varier selon les publics et les services métiers urbains. Enfin, la segmentation doit produire des résultats généralisables, comparables d’un territoire à l’autre et reproductibles dans le temps, afin de répondre aux besoins opérationnels des collectivités et d’accompagner efficacement les politiques urbaines.

### Données pouvant être utilisées

L’inventaire de la végétation urbaine peut se reposer sur plusieurs familles de données issues de la télédétection et de la photogrammétrie.  

#### Imagerie satellitaire  
Les images satellites constituent une source historique et encore largement mobilisée pour l’observation de la végétation. Elles peuvent être classées en deux grandes familles :  
- **Satellites publics à résolution moyenne**, comme **Landsat-8/9** (30 m, NASA/USGS) ou **Sentinel-2** (10 m, ESA), qui offrent des séries temporelles longues et gratuites. Leur principal atout est la possibilité de suivre les évolutions de la végétation dans le temps à grande échelle. En revanche, leur résolution spatiale reste insuffisante pour détecter finement des éléments urbains comme des haies ou de petits espaces verts.  
- **Satellites commerciaux à très haute résolution**, comme **SPOT** (1,5 m, Airbus), **Pléiades** (0,5 m, Airbus) ou **WorldView-3** (0,3 m, Maxar). Ces données permettent une cartographie très précise des surfaces végétalisées et sont particulièrement adaptées aux contextes urbains. Toutefois, leur coût élevé et la dépendance à la programmation d’acquisitions limitent leur usage dans des cadres opérationnels continus.  

Ces images sont généralement livrées en *GeoTIFF* multibandes (incluant souvent le proche infrarouge), permettant de calculer des indices spectraux tels que le *NDVI* (*Normalized Difference Vegetation Index*) ou l’*EVI* (*Enhanced Vegetation Index*). Ces indices facilitent la distinction entre végétation active et surfaces artificielles, même si leur efficacité diminue dans les environnements urbains complexes.

#### Orthophotos aériennes (RGB et CIR)  
Les campagnes aériennes constituent une autre source majeure. En France, l’*IGN* produit la *BD ORTHO*, couvrant l’ensemble du territoire avec des résolutions allant de 20 cm à 5 cm. Les orthophotos standard sont en *RGB*, mais certaines campagnes incluent un canal *proche infrarouge (CIR)*. Ce dernier permet d’améliorer la discrimination entre végétation et surfaces minérales, en exploitant la forte réflectance de la chlorophylle dans l’infrarouge.  

Les orthophotos sont précieuses pour identifier précisément les surfaces végétalisées, notamment les espaces verts urbains, mais elles présentent une limite structurelle : elles ne fournissent pas d’information tridimensionnelle. Ainsi, contrairement au LiDAR, elles ne permettent pas de connaître la hauteur de la végétation ni de distinguer ce qui se situe sous le couvert arboré (par exemple de la pelouse ou du sol).

#### Nuages de points LiDAR  
Le *LiDAR* fournit une description tridimensionnelle extrêmement détaillée de la surface terrestre grâce à des mesures de temps de vol de signaux laser. Ces données permettent de générer des *modèles numériques de terrain (MNT) et des *modèles numériques de surface (MNS)*, et surtout de calculer la hauteur et la structure de la canopée. En France, le programme LiDAR HD de l’IGN vise une couverture nationale. Certaines métropoles produisent également leurs propres relevés, parfois encore plus denses.  

Le principal atout du LiDAR est de capturer la dimension verticale de la végétation, ce qui le rend particulièrement adapté à la caractérisation des strates (haies, arbres, buissons). Ses limites sont liées au volume très important de données à traiter, ou à la définiton du sol (de la pelouse peut être souvent confondu avec un sol nu). Les formats standards sont le *LAS* et sa version compressée *LAZ*.

Les classes de base utilisées sont définies par l’ASPRS, l’association de référence dans le domaine du LiDAR. Elles incluent notamment le sol, la végétation (basse, moyenne et haute), les bâtiments, l’eau, les ponts et les structures artificielles. Ces classes constituent un socle commun largement partagé, que des producteurs comme l’IGN peuvent ensuite enrichir ou adapter selon les usages.

Les algorithmes de classification des nuages de points LiDAR reposent sur l’analyse du signal laser et sur le principe des rebonds. Lorsqu’une impulsion est émise, elle peut générer un ou plusieurs retours selon la nature des surfaces rencontrées. Les surfaces planes et compactes, comme le sol ou les toitures, produisent le plus souvent un retour unique, tandis que la végétation génère fréquemment plusieurs rebonds à différentes hauteurs. L’exploitation du nombre de retours, de leur ordre et de leur position verticale permet de distinguer le sol des éléments situés au-dessus.


#### Les cartes vectorielles
En géomatique et dans l’analyse de données urbaines, qu’elles proviennent de satellites ou de photographies aériennes, il est courant de travailler avec des données vectorielles. Celles-ci se distinguent des données raster par le fait qu’elles représentent l’information à travers des géométries explicites : points, lignes, polygones, plutôt que par une matrice de pixels. Dans le cas de la végétation urbaine, les polygones peuvent décrire des espaces verts, des alignements d’arbres ou des zones arborées, tandis que les points permettent de localiser individuellement chaque arbre et d’y associer des attributs (espèce, type d'arbre urbain). Ces données sont produites à partir de relevés de terrain, de campagnes de photointerprétation, ou encore dérivées de traitements automatisés appliqués aux images satellitaires, aériennes ou LiDAR. Elles présentent l’avantage d’être directement intégrables dans les systèmes d’information géographique (SIG), ce qui en facilite la gestion, la mise à jour et la mise en relation avec d’autres couches thématiques (parcelles cadastrales, zonages réglementaires, réseaux techniques). Leur format est généralement **Shapefile (SHP)**, **GeoPackage (GPKG)** ou **GeoJSON**, tous largement supportés par les logiciels SIG.
 
### Données Radar
Les données SAR, issues de satellites tels que Sentinel-1 ou de missions commerciales comme TerraSAR-X et Cosmo-SkyMed, offrent la capacité d’observer la surface terrestre indépendamment de la couverture nuageuse et des conditions d’éclairage. Elles permettent de détecter la structure générale des surfaces et des végétaux, mais présentent des limites en termes de résolution spatiale et de détail sur les différentes strates végétales. La disponibilité des séries temporelles peut varier selon les métropoles, et l’accès aux images commerciales est généralement payant, ce qui restreint leur exploitation systématique dans des contextes urbains locaux. De plus, malgré leur potentiel pour caractériser la végétation, ces données sont encore peu utilisées pour la cartographie fine de la végétalisation urbaine, ce qui limite l’utilisation de ces données pour développer des méthodes automatisées et reproductibles de suivi de la végétation urbaine.

#### Tableau récapitulatif

| **Type de données**          | **Acquisition**                  | **Résolution**        | **Avantages**                                | **Limites**                              | **Formats courants** | **Exemples de données** |
|-------------------------------|----------------------------------|-----------------------|----------------------------------------------|------------------------------------------|-----------------------|--------------------------|
| **Imagerie satellitaire**     | Capteurs orbitaux (ESA, NASA, Maxar, Airbus) | 30 m (Landsat) ; 10 m (Sentinel-2) ; 0,3–0,5 m (Pléiades, WorldView) | Séries temporelles longues, couverture mondiale | Coût élevé pour données commerciales, résolution limitée pour Sentinel/Landsat | GeoTIFF | Sentinel-2, Landsat-8/9, SPOT, Pléiades, WorldView-3 |
| **Orthophotos aériennes (RGB)** | Avions (IGN, collectivités)     | 20 cm – 5 cm          | Très haute résolution, couverture nationale | Pas d’info 3D → impossible de voir sous la canopée | GeoTIFF, ECW | BD ORTHO® IGN, BD métropoles |
| **Orthophotos CIR**           | Avions (IGN, services locaux)    | 20 cm – 5 cm          | Différenciation fine de la végétation vivante | Disponibilité variable | GeoTIFF | BD ORTHO® CIR IGN, BD métropoles |
| **Nuages de points LiDAR**    | Laser aéroporté (IGN, métropoles) | 5–20 points/m²        | Info 3D très précise, hauteur canopée | Données lourdes, pas toujours disponibles | LAS, LAZ | LiDAR HD® IGN, LiDAR métropolitains |
| Imagerie SAR | Sattellites, Aéroportés | 10 m à 30 m (selon satellite et mode) |  Capable de pénétrer les nuages et de fonctionner de jour comme de nuit, sensible à la structure et l’humidité du sol | Résolution spatiale plus faible que l’optique, informations spectrales limitées, exploitation peu développée pour la végétation urbaine, restrictions possibles selon les fournisseurs commerciaux | GeoTIFF | Sentinel-1, RADARSAT, TerraSAR-X |
 
#### Données rasterisées et données vectorisées  

En géomatique, l’une des distinctions fondamentales concerne la représentation de l’information spatiale sous forme rasterisée ou vectorisée. Les données **raster** correspondent à des grilles régulières constituées de pixels, chacun associé à une valeur numérique. Cette valeur peut représenter une intensité lumineuse, une réflectance dans une bande spectrale donnée, une altitude (dans le cas d’un MNT ou MNH) ou encore un indice dérivé (comme le NDVI pour la végétation). Leur force principale réside dans leur capacité à couvrir de manière continue de vastes territoires, ce qui en fait un format privilégié pour les images satellites, les photographies aériennes ou encore les modèles numériques issus du LiDAR. Néanmoins, cette approche matricielle a pour inconvénient une volumétrie élevée, surtout pour les résolutions fines, et une relative rigidité dans l’expression d’objets complexes (un arbre, une route ou un bâtiment sont représentés par un ensemble de pixels plutôt que par une entité explicite).  

À l’inverse, les données **vectorielles** représentent l’espace à partir d’objets géométriques : des points (ex. arbres isolés), des lignes (ex. axes routiers, réseaux de canalisations) ou des polygones (ex. parcelles cadastrales, zones de végétation). Chaque objet est enrichi d’attributs descriptifs qui en facilitent l’exploitation et l’analyse. Ce mode de représentation permet une structuration plus explicite et une lecture plus directe des entités spatiales, en particulier dans un contexte opérationnel où les services urbains doivent gérer des objets concrets comme des parcelles ou des équipements. Toutefois, les données vectorielles reposent souvent sur une interprétation ou une généralisation préalable (par digitalisation ou classification d’images raster), ce qui peut induire des pertes de précision ou des décalages par rapport à la réalité observée.  

Ainsi, raster et vecteur doivent être envisagés comme deux approches complémentaires plutôt que concurrentes : le raster fournit l’information brute et continue, tandis que le vecteur structure et synthétise cette information sous forme d’objets exploitables dans les processus décisionnels.  

| Caractéristique              | Raster                                         | Vectoriel                                       |
|-------------------------------|-----------------------------------------------|------------------------------------------------|
| **Structure**                 | Grille de pixels                              | Objets géométriques (points, lignes, polygones)|
| **Nature de l’information**   | Valeurs continues (spectre, altitude, intensité)| Entités discrètes (arbres, routes, parcelles)  |
| **Résolution**                | Dépend de la taille du pixel                  | Dépend du niveau de précision de la digitalisation|
| **Volume de données**         | Élevé, surtout en haute résolution            | Généralement plus léger                         |
| **Précision géométrique**     | Limitée par la taille du pixel                | Très élevée pour représenter des formes précises|
| **Usages typiques**           | Imagerie satellite, orthophotos, MNT, NDVI    | Cadastral, réseaux urbains, zonages|
| **Avantage principal**        | Couverture continue de grandes surfaces       | Représentation explicite et enrichie des objets|
| **Limite principale**         | Peu adapté à la description d’objets précis   | Dépend d’une interprétation préalable du réel   |  

#### Formats des données  

La manière dont les données spatiales sont encodées conditionne fortement leur circulation et leur exploitation. Pour les **raster**, le **GeoTIFF** s’est imposé comme le standard de référence, car il combine une image matricielle avec ses métadonnées géographiques (projection, système de coordonnées, résolution, etc.). Ce format garantit une interopérabilité avec la plupart des logiciels SIG et des bibliothèques de traitement d’image. D’autres formats existent (JPEG, PNG, parfois utilisés pour des visualisations rapides), mais ils sont moins adaptés pour des analyses précises car ils n’intègrent généralement pas l’information de géoréférencement.  

Du côté des **vectoriels**, plusieurs formats coexistent. Le plus ancien et répandu reste le **Shapefile (.shp)**, très utilisé dans les administrations et par de nombreux logiciels. Toutefois, ses limites techniques (taille maximale de fichier, gestion limitée des attributs, absence de prise en charge des caractères spéciaux) en font un format vieillissant. À l’inverse, le **GeoJSON** connaît une popularité croissante, notamment grâce à sa simplicité d’utilisation dans les applications web et son interopérabilité. Pour des projets à grande échelle ou nécessitant des requêtes complexes, les bases de données spatiales comme **PostGIS** permettent de gérer efficacement aussi bien des données vectorielles que rasterisées, avec des capacités avancées de traitement et d’analyse spatiale.  

La connaissance de ces formats et de leurs contraintes est essentielle pour garantir la cohérence, la reproductibilité et la pérennité des projets en analyse urbaine. Le choix du format ne relève pas uniquement d’un aspect technique, mais conditionne aussi la possibilité de partager des données entre acteurs, de les archiver dans la durée et de les intégrer dans des chaînes de traitement automatisées.  


### Approches classiques
Avant d'explorer les techniques implémenté avec le depp learning, il est important de voir les techniques de segmentation de végétation  par des approches classiques ou avec des modèles de Machin Learning supervisés.

#### Le NDVI

L’indice de végétation par différence normalisée, ou **NDVI** (*Normalized Difference Vegetation Index*), constitue l’une des méthodes historiques de détection et de suivi de la végétation à partir de l’imagerie satellitaire. Décrit notamment par **Pettorelli et al. (2005)**, il repose sur la différence de réflectance entre la bande du **rouge visible (RED)**, fortement absorbée par la chlorophylle, et celle du **proche infrarouge (NIR)**, fortement réfléchie par les structures internes des feuilles. Le NDVI se calcule selon la formule :  

$$
\text{NDVI} = \frac{(NIR - RED)}{(NIR + RED)}
$$

Les valeurs obtenues varient de **-1 à +1** : les surfaces non végétalisées (eau, bâtiments, routes) présentent des valeurs proches de 0 ou négatives, tandis que la végétation active et dense atteint des valeurs supérieures à **0,5**. Cet indice permet donc d’identifier de manière simple et robuste les zones végétalisées, indépendamment de leur type ou de leur étendue.  

Dans le contexte urbain, le NDVI a longtemps été utilisé pour cartographier la végétation et estimer la densité du couvert végétal, que ce soit à partir d’images Sentinel-2, Landsat, ou d’orthophotos disposant d’un canal infrarouge. L’efficacité du NDVI pour suivre les évolutions spatio-temporelles de la couverture végétale à partir d’images Landsat a été démontrée, permettant d’identifier des changements significatifs de classes végétales sur plusieurs décennies (Özyavuz et al., 2015). Le NDVI est particulièrement utile pour repérer la présence ou l’absence de végétation et pour suivre son évolution temporelle (saisonnalité, verdissement, perte de couverture). Des seuils empiriques peuvent être appliqués pour distinguer plusieurs catégories :  

- **NDVI < 0** : surfaces artificielles, eau, sols nus   
- **0 < NDVI < 0.3** : végétation clairsemée  
- **NDVI > 0.5** : végétation dense 

Cependant, malgré sa popularité, le NDVI présente plusieurs limites importantes. En milieu urbain, la forte hétérogénéité des matériaux (toitures, ombres, asphalte, sols nus) perturbe la mesure du signal et peut produire des valeurs faussement positives ou négatives. L’indice sature également dans les zones de végétation très dense, où il ne permet plus de différencier les variations internes du couvert (Pettorelli et al., 2005). Enfin, il ne renseigne pas sur la structure verticale : un gazon et une canopée arborée peuvent afficher des valeurs similaires, rendant impossible la distinction des strates végétales (basse, moyenne, haute).  

Pour pallier ces limites, le NDVI a été combiné avec d’autres variables climatiques (température, précipitations) afin d’établir des classifications globales plus fines de la végétation (Hashim et al., 2019). Néanmoins, dans le contexte urbain, il reste surtout considéré comme un indicateur de premier niveau, utile pour la détection rapide ou le suivi global de la végétation, mais insuffisant pour la segmentation fine et la caractérisation structurelle nécessaires aux inventaires urbains. Il sert néanmoins souvent de base ou de variable d’entrée dans des modèles de classification plus complexes, notamment ceux reposant sur des approches d’apprentissage supervisé ou de deep learning, qui exploitent conjointement la richesse spectrale, texturale et contextuelle des images.  

### Méthodes non fondées sur l’apprentissage automatique

#### Maximum Likelihood
La classification Maximum Likelihood repose sur l’hypothèse que les signatures spectrales des classes suivent une distribution gaussienne. Chaque pixel est associé à la classe dont la probabilité estimée, calculée à partir des paramètres statistiques de chaque groupe, est la plus élevée. Cette méthode a longtemps été utilisée dans les premières classifications à partir des images Landsat, et elle reste une référence dans les chaînes traditionnelles de télédétection. Sa force réside dans sa simplicité et son caractère probabiliste clair, mais elle se montre fragile lorsque les classes sont hétérogènes, lorsque les distributions ne respectent pas la normalité ou lorsque les conditions atmosphériques ne sont pas homogènes. Ces limites expliquent son remplacement progressif par des méthodes plus souples et non paramétriques dans les approches récentes (Phiri & Morgenroth, 2017).

#### Minimum Distance
L’approche du minimum distance affecte chaque pixel à la classe dont le centroïde spectral est le plus proche dans l’espace des caractéristiques. Cette méthode est rapide, facile à mettre en œuvre et ne repose sur aucune hypothèse statistique. Elle convient bien à des jeux de données simples et à des classes bien distinctes, mais elle ignore la variance interne des classes et tend à mal représenter les classes dispersées ou hétérogènes. Son emploi s’est surtout maintenu comme outil exploratoire ou comme étape de pré-classification avant des traitements plus sophistiqués.

#### Clustering non supervisé (K-Means, ISODATA)
Le regroupement non supervisé cherche à identifier des ensembles de pixels similaires selon leurs caractéristiques spectrales, sans disposer de classes d’apprentissage. K-Means et ISODATA figurent parmi les méthodes les plus utilisées pour l’analyse exploratoire et la segmentation initiale de grands jeux d’images. Ces techniques sont précieuses pour révéler la structure spectrale interne des données ou pour élaborer des classifications préliminaires en absence de données d’entraînement fiables. En revanche, elles nécessitent de fixer un nombre de clusters a priori, ce qui peut introduire une subjectivité, et leurs résultats sont souvent sensibles aux conditions d’acquisition ou à la variabilité saisonnière (Phiri & Morgenroth, 2017).


#### Object-Based Image Analysis (OBIA)
L’analyse orientée objet (OBIA) repose sur une segmentation préalable de l’image en entités spatiales homogènes selon des critères de couleur, texture, forme et taille. Contrairement aux approches pixel-par-pixel, cette méthode regroupe les pixels en objets significatifs, qui servent ensuite d’unités d’analyse. Chaque objet est caractérisé par des descripteurs spectraux, texturaux et géométriques, puis classé à l’aide de règles expertes ou d’algorithmes supervisés.

L’un des outils les plus utilisés pour mettre en œuvre cette approche est le logiciel eCognition (développé initialement par Definiens, aujourd’hui propriété de Trimble (https://geospatial.trimble.com/en/products/software/trimble-ecognition)). Il repose sur l’algorithme de segmentation multirésolution proposé par Baatz & Schäpe (2000), qui fusionne progressivement les pixels en objets en minimisant une fonction d’hétérogénéité. eCognition offre également un environnement intégré pour calculer automatiquement des indices spectraux, des mesures de texture et de forme, et pour construire des chaînes de classification complexes, combinant règles, seuils et apprentissage supervisé.

Cette approche permet de réduire considérablement l’effet “sel-poivre” propre aux classifications pixel-par-pixel et d’intégrer la dimension spatiale et contextuelle des objets. Elle est particulièrement performante avec les images à haute ou très haute résolution, notamment en télédétection urbaine ou agricole. L’expérience acquise avec les données Sentinel-2 a confirmé sa pertinence pour la classification des milieux agricoles et forestiers (Immitzer, Vuolo & Atzberger, 2016).
Le principal défi réside dans le choix du paramètre d’échelle lors de la segmentation : il détermine la taille des objets extraits et influence directement la qualité de la classification.


### Approches Deep Learning

#### GAN pour segmentation
Les GAN (Generative Adversarial Networks) reposent sur l’interaction entre deux réseaux : un générateur, qui produit une image de sortie (par exemple un masque de segmentation), et un discriminateur, qui cherche à distinguer si ce résultat est réaliste ou non. Dans le cas des cGAN (conditional GAN), la génération est conditionnée par une image d’entrée : le modèle apprend alors à produire un masque de segmentation directement à partir de l’image source, comme dans l’approche Pix2Pix.

Des travaux comme ceux d'Issam Kheder ont utilisé les cGAN comme Pix2Pix pour produire des cartes de chaleurs des sols à partir d'images orthophotographiques, ce qui implique une utilisation des cGAN pour la génération de données de classification des sols via des images aériennes. 

Mais en réalité pour la segmentation, ce type de modèle apprend surtout à produire des sorties visuellement cohérentes, pas une segmentation précise. Il est parfois utilisé dans la recherche en imagerie médicale, où les formes sont relativement régulières, comme pour l'indentification de tumeur. En revanche, pour la segmentation urbaine, les résultats sont souvent trop lissés ou flous sur les contours, ce qui limite la précision pixel par pixel. L’entraînement est également instable et difficile à contrôler. Pour ces raisons, les GAN et cGAN restent marginaux pour la segmentation urbaine opérationnelle et sont rarement utilisés seuls dans des chaînes de production.

#### CNN et variantes (U-Net, FCN)
Les réseaux de neurones convolutifs (CNN) ont été conçus au départ pour la classification d’images, c’est-à-dire pour identifier l’objet principal ou le type de scène présent dans une image entière. Leur principe repose sur l’application de filtres convolutifs qui extraient progressivement des informations locales, comme les contours, les textures ou les formes simples, puis des structures plus complexes. À la fin du réseau, les couches finales servent à regrouper toutes ces informations en une seule décision. Elles résument l’image complète afin de produire une étiquette unique, par exemple « bâtiment » ou « route ». Ces couches ne conservent plus la position précise des éléments dans l’image. C’est pour cette raison que les CNN classiques sont bien adaptés à la reconnaissance d’objets, mais pas à la segmentation d’images urbaines, où il est nécessaire de savoir précisément quelle classe correspond à chaque pixel.

Les Fully Convolutional Networks (FCN) représentent une première évolution majeure des CNN vers la segmentation d’images. Cette approche a été formalisée par Long, Shelhamer et Darrell (2015). Le principe consiste à adapter des réseaux initialement conçus pour la classification en supprimant les couches finales entièrement connectées et en les remplaçant par des couches convolutives. Le réseau conserve ainsi une organisation spatiale tout au long du traitement, ce qui lui permet de produire une sortie structurée sur l’image. Cette architecture a ouvert la voie à l’utilisation des CNN pour la segmentation d’images aériennes et satellitaires. Néanmoins, à force de réduire la résolution nécessaires à l’extraction de caractéristiques, une partie de l’information spatiale fine est perdue. Cela se traduit par des contours moins précis et des difficultés à représenter correctement les objets fins ou fragmentés, fréquents dans les environnements urbains complexes.

U-Net, introduit par Ronneberger et al. en 2015, s’inscrit dans cette continuité en proposant une architecture encodeur–décodeur avec des connexions directes entre les niveaux de même résolution. Cette structure permet de mieux préserver l’information spatiale tout en conservant des caractéristiques de haut niveau. U-Net est devenu un modèle de référence pour la segmentation, notamment grâce à sa simplicité et à sa robustesse. Néanmoins, dans le contexte urbain, il montre des limites sur des scènes très hétérogènes et sur des images à très haute résolution, où la prise en compte du contexte global et la cohérence à grande échelle restent difficiles à assurer.


#### Transformers (Swin Transformer)
Les Transformers appliqués à la vision sont issus des travaux menés initialement en traitement du langage naturel. Le modèle Transformer a été introduit par Vaswani et al. (2017) dans l’article Attention Is All You Need. Son idée centrale est le mécanisme d’auto-attention, qui permet à un modèle d’analyser les relations entre différents éléments d’une entrée, sans dépendre uniquement de leur proximité. En vision, ce principe a été transposé aux images avec le Vision Transformer (ViT) proposé par Dosovitskiy et al. (2020), où l’image est découpée en petits blocs traités comme une suite d’éléments.

Le mécanisme d’auto-attention consiste à comparer chaque partie de l’image avec toutes les autres afin de déterminer lesquelles sont les plus importantes à considérer ensemble. Contrairement aux CNN, qui analysent l’image localement à l’aide de filtres, les Transformers peuvent intégrer dès le départ des informations venant de zones éloignées de l’image. Cela leur permet de mieux comprendre le contexte global, par exemple la relation entre un bâtiment, une route et les espaces verts qui l’entourent.

Le Swin Transformer, introduit par Liu et al. (2021), est une adaptation plus efficace de cette approche pour les images de grande taille. Il applique l’auto-attention dans des fenêtres locales, qui se déplacent légèrement d’un niveau à l’autre du réseau. Cette stratégie limite le coût de calcul tout en permettant au modèle de capter des informations à différentes échelles. Pour la segmentation, le Swin Transformer est généralement associé à un décodeur comme UPerNet (Xiao et al., 2018), qui combine les informations issues de plusieurs niveaux du réseau afin de produire une carte de classes cohérente.

Dans le cadre de la segmentation urbaine, ces modèles sont particulièrement adaptés aux scènes complexes et hétérogènes. Leur capacité à prendre en compte un contexte large facilite la distinction entre des classes visuellement proches, comme différentes surfaces minérales ou types de végétation. En revanche, ils demandent davantage de ressources de calcul et de données d’entraînement que les architectures CNN plus classiques.

#### Critères d'évaluation
Afin de déterminer quel technologies et/ou quel modèle nous souhaitons utilisé pour nos problèmatique il faut savoir les évaluer.
L’évaluation pour la segmentation consiste à comparer les pixels prédits par le modèle avec les pixels réels.

- **Accuracy** : proportion de pixels correctement classés sur l’ensemble de l’image. Cette mesure est simple mais peu représentative lorsque certaines classes sont minoritaires.

$$
\text{Accuracy} = \frac{TP + TN}{TP + TN + FP + FN} 
$$

- **Précision** : proportion de pixels correctement prédits pour une classe parmi tous les pixels prédits comme appartenant à cette classe. Elle indique si le modèle évite de sur-détecter.

$$
\text{Précision} = \frac{TP}{TP + FP}
$$

- **Rappel** : proportion de pixels d’une classe réelle correctement détectés. Cela mesure la capacité du modèle à ne pas oublier de pixels appartenant à une classe.

$$
\text{Rappel} = \frac{TP}{TP + FN}
$$

- **Dice / F1-score** : mesure combinant précision et rappel. Elle évalue l’équilibre entre sur-détection et sous-détection.

$$
\text{Dice} = \text{F1} = \frac{2 \cdot TP}{2 \cdot TP + FP + FN}
$$

- **Intersection over Union (IoU)** : rapport entre les pixels communs à la prédiction et à la vérité terrain et l’ensemble des pixels concernés. C’est l'une des mesure la plus utilisée en segmentation.

$$
\text{IoU} = \frac{TP}{TP + FP + FN}
$$

- **IoU moyenne (mIoU)** : moyenne des IoU calculées pour chaque classe. Elle donne une vision globale et équilibrée des performances du modèle.

$$
\text{mIoU} = \frac{1}{N} \sum_{i=1}^{N} \frac{TP_i}{TP_i + FP_i + FN_i}
$$

Ces critères sont complémentaires et doivent être utilisés conjointement pour obtenir une évaluation fiable des performances pour de la segmentation d'image.

### Outils et ressources existants

#### COSIA
**COSIA (Couverture du Sol par Intelligence Artificielle)** est une base nationale de classification de l’occupation du sol produite par l’IGN dans le cadre du programme OCS GE (https://geoservices.ign.fr/ocsge). Elle repose sur une chaîne de traitement automatique combinant des orthophotographies aériennes à très haute résolution (BD ORTHO®), des données altimétriques (MNT, MNS) et des annotations manuelles couvrant plus de 2 500 km² répartis sur 63 départements.  
Les données sont produites à une résolution spatiale de 20 cm par pixel et mises à jour à chaque nouvelle campagne aérienne, garantissant une continuité temporelle du référentiel. Vous pouvez retrouvez le site du projet ici : https://geoservices.ign.fr/cosia
COSIA fournit une classification homogène du territoire français selon une nomenclature nationale de quinze classes principales, incluant le bâti, les surfaces imperméabilisées, les zones agricoles, les surfaces herbacées, les formations ligneuses basses, les forêts de feuillus et de conifères, ainsi que les zones humides et plans d’eau. La production de COSIA repose sur des modèles de deep learning appliqués à l’imagerie aérienne, tels que des Vision Transformers. Pour chaque pixel, le modèle estime une probabilité d’appartenance à chaque classe de la nomenclature.

Les performances sont évaluées à l’aide d’indicateurs standards :
- précision,
- rappel,
- Intersection over Union (IoU).

Sur les millésimes 2017–2023, COSIA atteint :
- une précision moyenne de **79,5 %**,
- un rappel de **81 %**,
- un IoU moyen de **68,1 %**.

Si ces résultats sont satisfaisants à grande échelle, plusieurs limites ont été identifiées, notamment des difficultés à distinguer finement les strates végétales intermédiaires (broussailles, arbustes). De plus, les modèles exacts utilisés par l’IGN ne sont pas publiquement documentés, ce qui limite la réplicabilité et la transparence méthodologique. Ces choix techniques peuvent néanmoins être partiellement inférés à partir du projet FLAIR-HUB, dont COSIA constitue une continuité opérationnelle.

Pour aller plus loin sur COSIA nous vous invitons a regarder ce document plus détaillé sur le sujet : [Synthèse COSIA](Synthese-COSIA.md)

#### FlairHub

FLAIR-HUB est un projet open source développé par l’IGN dans le cadre du programme FLAIR. Contrairement à COSIA, FLAIR-HUB ne fournit pas directement une base de données prédite, mais des jeux de données et des modèles de deep learning préentraînés pouvant être appliqués localement pour la segmentation de l’occupation du sol. 

La documentation officielle du projet est disponible à l’adresse suivante :  
https://ignf.github.io/FLAIR/FLAIR-HUB/flairhub.html 

Le code source et les modèles sont accessibles sur GitHub : https://github.com/IGNF/FLAIR-HUB

FLAIR-HUB constitue à ce jour l’un des jeux de données  à très haute résolution les plus complets publiés par l’IGN. Les données sont organisées sous forme de patches de 512 × 512 pixels à une résolution spatiale de 20 cm par pixel, correspondant à des emprises d’environ 100 × 100 mètres au sol.  
L’ensemble du jeu de données couvre 2 528 km², répartis sur 74 régions d’intérêt, sélectionnées afin de représenter une grande diversité de contextes urbains, périurbains, agricoles et naturels.

Les annotations s’appuient sur deux nomenclatures principales :
- AERIAL LABEL-COSIA, comprenant 19 classes dont 15 classes principales de couverture du sol
- AERIAL LABEL-LPIS, dédiée aux cultures agricoles.

Sur le plan méthodologique, les expérimentations menées dans FLAIR-HUB reposent sur des architectures récentes de vision par ordinateur. La plupart des modèles associent un Swin Transformer utilisé comme encodeur et UPerNet comme tête de segmentation.  
Comme expliqué précédemment dans ce document, le Swin Transformer permet de capturer le contexte spatial à différentes échelles grâce à un mécanisme d’attention locale glissante, tandis que UPerNet combine les informations issues de plusieurs niveaux de profondeur afin de produire une segmentation pixel par pixel cohérente à l’échelle de la scène.

L’un des atouts majeurs de FLAIR-HUB est dans la mise à disposition de modèles préentraînés, accompagnés de leurs poids, via la plateforme HuggingFace, ainsi que dans la possibilité de réentraîner ces modèles. Cette ouverture méthodologique rend l’approche plus transparente, réplicable et adaptable pour différents projets qui nécessitent une segmentation du territoire.

FLAIR-HUB apparaît ainsi comme un outil méthodologique et expérimental complémentaire de COSIA. Là où COSIA fournit un référentiel national homogène et opérationnel, FLAIR-HUB offre les moyens techniques nécessaires pour analyser, reproduire et améliorer les modèles de segmentation, notamment dans le cadre d’inventaires fins de la végétation urbaine intégrant des données multimodales, en particulier le Lidar.

Pour aller plus loin sur FLAIRHUB nous vous invitons a regarder ce document plus détaillé sur le sujet : [Synthèse FLAIRHUB](Synthese-FLAIRHUB.md)

#### LIDARHD / Myriad 3D / FRACTAL

##### LIDARHD
Le programme LIDAR HD, porté par l’Institut national de l’information géographique et forestière (IGN), a pour objectif de produire et de diffuser un référentiel altimétrique national précis, homogène et librement accessible à l’échelle de l’ensemble du territoire français. Il repose sur des acquisitions LiDAR aériennes à haute densité, avec en moyenne une dizaine d’impulsions par mètre carré, permettant de constituer des nuages de points 3D très détaillés. Ces données sont diffusées en open data sous licence Etalab 2.0, principalement via le portail officiel de l’IGN (https://geoservices.ign.fr/lidarhd), sous forme de dalles de 1 km², dans les systèmes de coordonnées de référence comme le RGF93 / Lambert-93.

À partir de ces nuages de points, stockés aux formats standards LAS et LAZ (version 1.4), et classés sémantiquement selon la nomenclature ASPRS enrichie par l’IGN, plusieurs produits dérivés sont générés. Le Modèle Numérique de Terrain (MNT) décrit la surface du sol nu, le Modèle Numérique de Surface (MNS) intègre le sursol (bâtiments, végétation, infrastructures), et le Modèle Numérique de Hauteur (MNH) permet de caractériser la hauteur relative des objets. Avec une précision altimétrique d’environ 10 cm et des grilles dérivées à une résolution de 50 cm (ainsi qu’une version à 5 m), le LIDAR HD constitue une base essentielle pour de nombreux usages : aménagement du territoire, prévention des risques naturels, études environnementales, gestion forestière et agricole, ou encore modélisation urbaine 3D, tout en s’appuyant sur une documentation détaillée garantissant la qualité, la cohérence et la réutilisation des données.
Certaines composantes du traitement, notamment la segmentation sémantique à grande échelle, s’appuient sur des modèles de deep learning développés dans le cadre de la bibliothèque Myria3D.

Pour aller plus loin sur le LIDAR-HD nous vous invitons a regarder ce document plus détaillé sur le sujet : [Synthèse LiDARHD](Synthese-LidarHD.md)

##### Myria3D
Myria3D est une bibliothèque open source de deep learning développée par l’IGN pour la segmentation sémantique de nuages de points LiDAR à haute densité, initialement conçue pour répondre aux contraintes du programme LIDAR HD. Elle vise à attribuer à chaque point d’un nuage 3D une étiquette sémantique (sol, bâtiment, végétation, véhicule, etc.) dans des contextes très variés, allant des scènes urbaines aux environnements forestiers ou ruraux. Myria3D est pensée pour traiter des volumes de données massifs et des densités élevées, tout en restant réutilisable sur d’autres jeux de données LiDAR. Elle fournit un cadre complet pour l’entraînement, l’évaluation et l’inférence de modèles de segmentation 3D, et s’appuie sur une documentation et un code source librement accessibles (https://ignf.github.io/myria3d/, https://github.com/IGNF/myria3d).

Sur le plan méthodologique, Myria3D repose principalement sur l’architecture RandLA-Net, introduite par Hu et al. (2020) dans “RandLA-Net: Efficient Semantic Segmentation of Large-Scale Point Clouds”. Ce réseau prolonge les travaux fondateurs de PointNet++, proposés par Qi et al. (2017), en adaptant le traitement hiérarchique des voisinages à des nuages de points très volumineux. RandLA-Net combine un échantillonnage aléatoire progressif des points avec un module de Local Feature Aggregation, intégrant l’encodage spatial local, des mécanismes de pondération par attention et l’analyse de voisinages à différentes échelles. Dans Myria3D, cette architecture est intégrée dans un pipeline optimisé incluant un sous-échantillonnage préalable des nuages et une interpolation des prédictions lors de l’inférence.

Pour en savoir plus sur Myria3D vous pouvez consulter le document suivant : [Synthèse Myria3D](Synthese-Myria3D.md)

##### FRACTAL
Le jeu de données FRACTAL (FRench ALS Clouds from TArgeted Landscapes) (https://huggingface.co/datasets/IGNF/FRACTAL) a été construit à partir du programme LiDAR HD. Il regroupe environ 100 000 sous-ensembles de 50 × 50 m, soit plus de neuf milliards de points, couvrant 250 km² répartis sur plusieurs régions françaises.
Chaque sous-ensemble est annoté selon sept classes sémantiques (sol, végétation, bâtiment, eau, pont, structure permanente, autre). Les nuages sont colorisés à partir des orthophotos ORTHO HR (RGB+NIR).
Les annotations de FRACTAL reposent sur la classification du LiDAR HD, vérifiée et corrigée manuellement. Le jeu de données est conçu pour évaluer la robustesse et la généralisabilité spatiale des modèles de segmentation 3D.

#### Données LiDAR
En plus du programme national LIDAR HD porté par l’IGN, certaines collectivités, comme la métropole de Lyon, peuvent commander leurs propres acquisitions afin de disposer de données LiDAR couvrant précisément leur territoire. Ces campagnes locales permettent d’adapter les données aux besoins du territoire.

Cependant les acquisitions LiDAR ne sont pas réalisées en continu. Les vols ont lieu à des moments précis, en fonction des budgets, des priorités locales et des conditions météorologiques. Cette fréquence limitée rend difficile le suivi régulier des changements, notamment pour la végétation basse ou les évolutions rapides du territoire.
De plus comme dis précédemment, les données LiDAR ne fournissent pas de bonnes informations sur la strate herbacée qui est confondu avec le sol. 

#### Données d’Arnaud Bellec (thèse)
Les travaux de thèse d’Arnaud Bellec (2018) fournissent une référence de vérité terrain pour la végétation urbaine dans la métropole de Lyon. Les données produites reposent principalement sur l’utilisation d’orthophotographies à très haute résolution, enrichies ponctuellement par des données complémentaires comme le LiDAR ou des indices de végétation (NDVI). Elles permettent de distinguer finement différentes strates végétales et constituent ainsi un outil précieux pour évaluer et comparer les performances des modèles de segmentation récents, tels que COSIA ou FLAIR-HUB.

La classification issue de la thèse discrimine la végétation en cinq classes principales : herbacées, buissons (<1,5 m), arbustes (1,5–5 m), petits arbres (5–15 m) et grands arbres (>15 m). Ces données sont publiées sous forme raster à très haute résolution (environ 1 m × 1 m par pixel), offrant une représentation précise et cohérente des objets végétaux. Cette granularité permet une analyse détaillée de la végétation urbaine, utile notamment pour des études locales ou pour la validation de modèles d’apprentissage automatique.

Malgré leur qualité élevée, ces données présentent certaines limites. Elles sont disponibles uniquement pour l’année 2018 et ont été générées à l’aide du logiciel eCognition, un outil propriétaire basé sur des méthodes orientées objet. Les paramètres exacts utilisés dans eCognition ne sont pas publiés, ce qui rend la reproduction exacte des résultats difficile. De plus, leur caractère ponctuel empêche un suivi automatique de l’évolution de la végétation dès que de nouvelles orthophotographies sont acquises.

Ces données restent toutefois une référence fiable et localisée pour la validation des modèles de segmentation de végétation. Elles permettent de comparer différentes approches, qu’il s’agisse de méthodes pixel-based classiques ou de techniques orientées objet combinées à des classifieurs modernes comme Random Forest ou SVM. L’usage ponctuel des données LiDAR dans la thèse a renforcé la distinction entre végétation basse et haute, mais n’est pas systématique sur tous les millésimes.

Enfin, la thèse offre un cadre méthodologique solide pour la cartographie de la végétation urbaine à très haute résolution et illustre l’intérêt des approches orientées objet pour gérer la complexité des environnements urbains hétérogènes. Ces données sont accessibles sur le portail DataGrandLyon (https://data.grandlyon.com/portail/fr/jeux-de-donnees/vegetation-stratifiee-2018-metropole-lyon/info).

Vous pouvez retrouvez un document explicitant les travaux d'Arnaud Bellec afin de créer cet inventaire dans ce document: [Synthese These Arnaud Bellec](Synthese-These-Arnaud-Bellec-2018)

### Analyse critique
- **FlairHub & COSIA** 

Les évaluations menées sur FLAIR-HUB, notamment par comparaison avec les données de référence issues du projet Armature 2, mettent en évidence des limites similaires à celles observées pour COSIA. La détection fine des strates végétales intermédiaires reste difficile, en particulier lorsque seules des orthophotographies à très haute résolution spatiale, de l’ordre de 8 cm par pixel, sont utilisées. Ces configurations entraînent également des temps de calcul plus importants et l’apparition d’artefacts locaux.

Les résultats sont en revanche plus stables pour des résolutions de l’ordre de 50 cm par pixel. L’ajout d’informations de hauteur dérivées du Lidar améliore nettement la séparation entre végétation basse, moyenne et haute, ce qui constitue un point clé pour les applications liées à la végétation urbaine.

- **LIDARHD/Myriad 3D** 

Les données issues du programme LiDAR HD offrent une information tridimensionnelle très précise et constituent une référence solide pour décrire la structure verticale du territoire. Elles sont particulièrement pertinentes pour l’analyse de la végétation moyenne et haute, comme les arbres ou les alignements arborés, dont la hauteur et la forme sont bien captées par les nuages de points. En revanche, la végétation basse reste plus difficile à caractériser : l’herbe, les pelouses ou certaines zones de végétation rase sont souvent confondues avec le sol, notamment lorsque la densité de points ou la classification initiale est insuffisante. À cela s’ajoute une contrainte temporelle importante : les acquisitions LiDAR ne sont pas continues et dépendent des campagnes de vol, ce qui limite le suivi régulier de l’évolution de la végétation urbaine.

- **Bellec** 

Les données produites dans le cadre de la thèse d’Arnaud Bellec (2018) constituent une référence de grande qualité pour la végétation urbaine à l’échelle de la métropole de Lyon. La stratification fine de la végétation en plusieurs classes de hauteur permet une analyse détaillée et pertinente, bien adaptée aux études locales et à l’évaluation de modèles de segmentation. Cependant, ces données sont figées dans le temps, limitées à un seul millésime, et reposent sur un traitement réalisé avec un logiciel propriétaire, dont les paramètres exacts ne sont pas entièrement documentés. Cela complique leur réutilisation automatique et leur extension à d’autres territoires ou à des périodes plus récentes.

---

## Perspectives et solutions

Les analyses précédentes montrent que chaque source de données présente des limites structurelles qui empêchent une description complète et fiable de la végétation urbaine lorsqu’elle est utilisée seule. Les approches basées sur l’imagerie, comme FLAIR-HUB ou COSIA, offrent une couverture régulière et homogène, mais peinent à distinguer finement les strates végétales intermédiaires, en particulier à très haute résolution, où apparaissent des artefacts et des coûts de calcul importants. À l’inverse, les données LiDAR fournissent une information verticale précise, indispensable pour caractériser la végétation moyenne et haute, mais elles sont acquises de manière ponctuelle et décrivent mal la végétation herbacée, souvent confondue avec le sol. Enfin, les données issues de la thèse de Bellec constituent une référence locale de grande qualité, mais elles sont ne sont disponibles que pour une seule année et difficiles à reproduire ou à généraliser à d’autres territoires du fait qu'elles soient dépendantes à un logiciel propriétaire. Ces arguements justifient une approche, visant à tirer parti de la complémentarité entre les réultats de FLAIR et du LiDAR afin d’améliorer la création d'un inventaire des strates de végétation urbaine.

### Résumé de la méthodologie

La méthode proposée repose sur une combinaison entre FLAIR-HUB et les données LiDAR, afin de produire une cartographie de la végétation urbaine plus fiable que l’utilisation d’une seule source de données. Le schéma présenté illustre les différentes étapes de cette chaîne de traitement.

Dans un premier temps, les orthophotographies sont préparées pour être compatibles avec les modèles FLAIR-HUB. Elles sont rééchantillonnées à une résolution d’environ 20 cm par pixel, puis utilisées pour réaliser une segmentation avec FLAIR-HUB. Contrairement à une segmentation classique, le modèle produit des cartes de probabilités par classe, ce qui permet de conserver une information plus souple. Les probabilités des classes de végétation sont ensuite renforcées afin de mieux faire ressortir ces classes lors de la sélection finale.

En parallèle, les données LiDAR sont traitées pour obtenir une carte de classes, un modèle numérique de terrain (MNT) et un modèle numérique de surface (MNS). À partir du MNT et du MNS, une carte de hauteur est calculée, fournissant une information essentielle sur la structure verticale de la végétation.

La fusion des deux sources se fait ensuite. Les zones identifiées comme végétation par FLAIR-HUB sont croisées avec les classes issues du LiDAR. Les pixels incertains du LiDAR sont conservés uniquement s’ils sont également classés comme végétation par FLAIR-HUB, ce qui permet de limiter les erreurs. L’information de hauteur est alors utilisée pour séparer les différentes strates de végétation (herbacée, arbustive et arborée).

Enfin, une carte finale de végétation urbaine est produite. Le LiDAR est privilégié lorsqu’il est disponible, car il décrit mieux la hauteur, tandis que FLAIR-HUB assure une classification plus précise de la strate herbacée et vient combler les trous du LiDAR dans les végétation plus hautes. Cette approche permet de tirer parti des forces de chaque source et de produire une cartographie plus adaptée aux besoins d’un inventaire de végétation urbaine.

Vous pouvez voir ce document explicitant la technique plus en détaille : [Méthodologie d'utilisation de Flair](Methodologie-Utilisation-Flair.md)


## Références
- Janhäll (2015), *Review on urban vegetation and particle air pollution – Deposition and dispersion*
- Klinberg et al. (2017), *Influence of urban vegetation on air pollution and noise exposure – A case study in Gothenburg, Sweden*
- Richards and Belcher (2021), *Global evidence for the effects of urban green space on temperature*
- Pettorelli et al. (2005), *Using the satellite-derived NDVI to assess ecological responses to environmental change*
- Özyavuz et al. (2015), *Determination of vegetation changes using NDVI method*
- Hashim et al. (2019), *Urban vegetation classification with NDVI threshold value method with very high resolution (VHR) Pleiades imagery*
- Phiri and Morgenroth (2017), *Developments in Landsat land cover classification methods: A review*
- Immitzer, Vuolo and Atzberger (2016), *First experience with Sentinel-2 data for crop and tree species classifications in Central Europe*
- Baatz and Schäpe (2000), *Multiresolution segmentation: An optimization approach for high quality multi-scale image segmentation*
- Long, Shelhamer and Darrell (2015), *Fully Convolutional Networks for Semantic Segmentation*
- Ronneberger, Fischer and Brox (2015), *U-Net: Convolutional Networks for Biomedical Image Segmentation*
- Vaswani et al. (2017), *Attention Is All You Need*
- Dosovitskiy et al. (2020), *An Image is Worth 16×16 Words: Transformers for Image Recognition at Scale*
- Liu et al. (2021), *Swin Transformer: Hierarchical Vision Transformer Using Shifted Windows*
- Xiao et al. (2018), *Unified Perceptual Parsing for Scene Understanding*
- Qi et al. (2017), *PointNet: Deep Learning on Point Sets for 3D Classification and Segmentation*
- Hu et al. (2020), *RandLA-Net: Efficient Semantic Segmentation of Large-Scale Point Clouds*
- Bellec (2018), *Dynamiques spatiales, temporelles etécologiques de la Métropole de Lyon1984-2015*
