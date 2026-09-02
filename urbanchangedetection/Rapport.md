# Stage Détection de Changements Urbains

# Sommaire

* [1. Introduction](#1-introduction)
* [2. Problématique](#2-problématique)
* [3. Etat de l'art](#3-etat-de-lart)
  * [3.1 Etudes explorées](#31-etudes-explorées)
  * [3.2 Ce que nous retiendrons](#32-ce-que-nous-retiendrons)
* [4. Proposition](#4-proposition)
  * [4.1 Architecture](#41-architecture)
  * [4.2 Implémentation](#42-implémentation)
  * [4.3 Algorithme](#43-algorithme)
  * [4.4 Choix des critères](#44-choix-des-critères)
* [5. Résultats](#5-résultats)
  * [5.1 Préambule](#51-préambule)
  * [5.2 Tests](#52-tests)
    * [5.2.1 La Doua](#521-la-doua)
    * [5.2.2 Part-Dieu](#522-part-dieu)
    * [5.2.3 Sud de Confluences](#523-sud-de-confluences)
    * [5.2.4 7ᵉ arrondissement](#524-7ᵉ-arrondissement)
  * [5.3 Comparaison des approches pour les bâtiments](#53-comparaison-des-approches-pour-les-bâtiments)
    * [5.3.1 Gratte-Ciel](#531-gratte-ciel)
    * [5.3.2 Duvivier](#532-duvivier)
    * [5.3.3 7e arrondissement](#533-7e-arrondissement)
    * [5.3.4 Confluences](#534-confluences)
    * [5.3.5 Tableau récapitulatif pour les bâtiments](#535-tableau-récapitulatif-des-approches-pour-la-détection-de-bâtiments)
  * [5.4 Comparaison des approches pour la végétation](#54-comparaison-des-approches-pour-la-végétation)
    * [5.4.1 Gratte-Ciel](#541-gratte-ciel)
    * [5.4.2 Confluences](#542-confluences)
    * [5.4.3 7e arrondissement](#543-7e-arrondissement)
    * [5.4.4 Tableau récapitulatif pour la végétation](#544-tableau-récapitulatif-des-approches-pour-la-détection-de-la-végétation)
  * [5.5 Limites](#55-limites)
* [6. Conclusion et Perspectives](#6-conclusion-et-perspectives)
  * [6.1 Conclusion](#61-conclusion)
  * [6.2 Perspectives](#62-perspectives)
* [7. Bibliographie](#7-bibliographie)
* [8. Annexe](#8-annexe)


# 1. Introduction

<!-- présentation liris et vcity -->
Dans le cadre des transitions environnementales et sociétales actuelles, la compréhension des dynamiques urbaines est devenue un enjeu stratégique pour les villes. Ce rapport présente les travaux réalisés lors d'un stage de recherche effectué au sein du Laboratoire d'Informatique en Image et Systèmes d'Information (LIRIS). Adossé à plusieurs institutions de la région lyonnaise, le LIRIS est un acteur de premier plan dans la recherche en sciences du numérique [1]. Plus particulièrement, ce travail s'est déroulé au sein du projet VCity, un collectif de recherche dédié au développement de concepts, de méthodes et d'outils pour la création, la gestion et la visualisation de représentations numériques urbaines [2].

<!-- présentation IArbre -->
Ce stage s’inscrit directement dans le contexte du projet IA.rbre, une initiative dédiée à la transition écologique. Porté par un consortium pluridisciplinaire comprenant l'entreprise TelesCoop, la Métropole de Lyon, l'Université Lyon 2 et le laboratoire LIRIS, le projet IA.rbre bénéficie du soutien financier de la Caisse des Dépôts dans le cadre du programme national France 2030. L'ambition majeure de ce projet est de concevoir des outils innovants de visualisation de données territoriales afin d'accompagner la résilience urbaine et d'éclairer l'élaboration des politiques publiques locales [3].

<!-- reformulation du sujet -->
Au cœur de cette démarche, notre sujet de stage se concentre sur la détection automatique de changements urbains. De manière vulgarisée, il s'agit d'analyser l’évolution des quartiers de la ville à partir de séries temporelles d'orthophotographies (des photographies aériennes) couvrant la période de 2018 à 2023. L'objectif consiste à repérer et à caractériser automatiquement les modifications qui affectent deux composantes majeures du paysage urbain : la végétation et le bâti. En s'appuyant sur des méthodes de traitement d'images et d'apprentissage automatique, ce travail vise à extraire et à quantifier ces évolutions spatiales et temporelles pour offrir une compréhension fine des processus d'urbanisation en cours.

# 2. Problématique

<!-- détailler les enjeux du sujet -->
Pour piloter efficacement les politiques d'aménagement et de transition écologique, les décideurs locaux ont aujourd'hui un besoin crucial de représenter la ville numériquement de manière fidèle et dynamique. Cependant, l'espace urbain est par nature en constante évolution : des bâtiments sont construits, démolis ou sont modifiés, et la végétation gagne ou perd du terrain au fil des saisons et des projets d'aménagement. Disposer d'un historique numérique de ces transformations, qui soit à la fois précis et accessible, est indispensable pour mesurer les trajectoires d'urbanisation et anticiper les besoins futurs des villes. Traditionnellement, l'inventaire de ces modifications repose sur des saisies manuelles ou des campagnes de terrain longues et coûteuses. L'enjeu est donc d'automatiser ce processus à partir des données géospatiales existantes.

<!-- enjeux techniques -->
D'un point de vue technique, le problème se formalise par le traitement d'entrées constituées de séries temporelles d'orthophotographies, images pixelisées à haute résolution prises à des années d'intervalle, associées à des nuages de points LiDAR, issus de la campagne LiDAR HD de l'Institut national de l'information géographique et forestière (IGN). Les sorties attendues consistent en une cartographie précise, qualifiée et quantifiée des mutations du territoire. Entre ces deux étapes, la transition implique des méthodes complexes de traitement d'images et d'apprentissage profond pour isoler les objets d'intérêts.

Dès lors, la problématique de ce travail de recherche s'articule autour des questions fondamentales suivantes :

- Quels changements faut-il détecter en priorité pour qu'ils soient réellement porteurs de sens pour les urbanistes et les écologues, et comment s'assurer de la pertinence des critères posés pour cette détection ?

- Quels outils et architectures algorithmiques adopter pour traiter efficacement ces volumes de données et avoir un résultat le plus qualitatif possible ?

- Comment modéliser et représenter ces changements de manière intelligible pour toute personne utilisant notre outil.

<br/>

# 3. Etat de l'art

## 3.1 Etudes explorées


| Source   | Méthode    | Données   | Entrée   | Sortie   | Résultats   |
|-----------|-----------|----------|----------|----------|----------|
|IGN (2025), Détection de changement LiDAR HD [3] | Classification puis vectorisation du nuage de points (outil TerraScan) en volumes 3D bâtiment (toit/mur/emprise), stockage en base 3D, comparaison spatiale des vecteurs entre deux dates via FME | Nuage de points LiDAR HD classé (programme national) | Nuage de points 3D classifié | Vecteurs 3D des créations/suppressions de bâtiments, mise à jour de la couche bâti de la BD TOPO| Ce processus de détection de changement a démontré son efficacité, notamment sur sa capacité à réduire les phases de recherche de création/suppression d'objets, et est déployé de façon opérationnelle en production. Limite : pas de classification sémantique fine par deep learning, LiDAR seul (pas de fusion avec l'image). |
|Sarp et al. (2014), Van Erciş earthquake [5] | Classification d'orthophotos pré/post-événement à très haute résolution combinée à des nuages de points issus de stéréo-corrélation, en trois étapes (classification image, extraction bâti à partir du nuage 3D, comparaison temporelle) | Orthophotos aériennes THR + nuages de points stéréo-photogrammétriques, avant/après séisme de Van (2011)  | Orthophotos + points 3D  |  Cartes des bâtiments détectés et des changements (destructions) post-séisme  |  L'article démontre l'efficacité combinée des images orthophoto et des nuages de points issus de la mise en correspondance stéréo pour la détection automatique de bâtiments et de leurs changements  |
| Synthèse FLAIR-HUB (VCityTeam, 2026) [6] | Segmentation sémantique deep learning (encodeur Swin Transformer + décodeur UPerNet) sur orthophotos multimodales, comparaison avec la vérité terrain Armature 2 (Lyon), puis test d'ajout de la hauteur LiDAR | Orthophotos RVBI 20 cm (FLAIR-HUB/IGN), LiDAR HD Grand Lyon 2018, vérité terrain Armature 2 (Bellec, 2018)  | Orthophotos IRRGB (+ hauteur LiDAR en variante) | Cartes de segmentation par classe (nomenclature COSIA) ou cartes de probabilité par classe  | L'ajout d'une information simple issue du Lidar, notamment la hauteur, permet d'atteindre un niveau de qualité beaucoup plus élevé et rend possible une séparation plus fiable entre les strates de végétation |

## 3.2 Ce que nous retiendrons

- IGN montre que le LiDAR seul (sans image) est déjà opérationnel pour détecter des changements de bâti, mais reste purement géométrique.
- Sarp et al. est une référence historique très important: elle prouve, dès 2014, que combiner orthophoto (texture/spectral) et nuage de points (géométrie/hauteur) améliore la détection de bâtiments et de leurs changements, un cas d'usage très proche du nôtre (bâti + changement urbain).
- La synthèse FLAIR-HUB montre empiriquement, sur notre terrain d'étude (Lyon), que l'ajout de la hauteur LiDAR à un modèle de segmentation d'image résout un problème concret (instabilité sur les strates intermédiaires)

Ces sources nous montrent bien qu'il est cohérent, pour notre objectif, d'utiliser les 2 outils, qui seront FLAIR-HUB et les données LiDAR.

<br/>

# 4. Proposition

## 4.1 Architecture 


```mermaid
flowchart TD

    
    A[Orthophotographies RVB/NIR des 2 années] --> B[Inférence FLAIR-HUB]
    L[Nuage de points LiDAR des 2 années] --> M[Rastérisation LiDAR]
 
    B --> C[Cartes de probabilité par classe]
    C --> D[Masque de la classe recherchée]

    M --> M2[MNT]
    M --> M3[MNS]
    M --> M1[Carte de classes LiDAR]
    

    M2 --> H[Calcul hauteur]
    M3 --> H

    M1 --> R[Fusion FLAIR et LiDAR pour chaque année]
    D --> R
    H --> R

    R --> DC[Comparaison des 2 années]
    DC --> Z[Raster de changement]
```

<br/>

## 4.2 Implémentation

Tout les scripts et le workflow sont codés entièrement en Python.

## 4.3 Algorithme 

### Combinaison FLAIR-HUB et LiDAR <br/>
On applique d'abord un masque FLAIR-HUB avant de le corriger avec le raster de classes LiDAR.

- Construction du masque FLAIR-HUB : En faisant une inférence en **class_prob**, on prend la probabilité de la classe qui nous intéresse, on pose un seuil et on donne la valeur de 1 à tous les pixels de probabilité supérieure à ce seuil, et 0 sinon. L'inférence sera faite soit en 50cm/px soit en 20cm/px (meilleure précision mais calcul plus long). On fait cette inférence pour les 2 années.
- Correction avec LiDAR : Nous partons du masque renvoyé par FLAIR-HUB. Dessus, nous appliquons les règles suivantes aux pixels indiqués à 1 (valides), pour produire un raster final :
    - Si la classe LiDAR d'un pixel valide n'est pas celle recherchée, on invalide ce pixel.
    - Puis on compare les 2 années :
        - Si un pixel est valide la première année mais pas la deuxième, c'est une **destruction**, représentée en **rouge**, avec la valeur **1**.
        - Si un pixel est valide la deuxième année mais pas la première, c'est une **construction**, représentée en **vert**, avec la valeur **2**.
        - Si un pixel est valide les 2 années et qu'il y a un changement de hauteur, c'est une **modification**, représentée en **jaune**, avec la valeur **3**.
        - Si un pixel est valide les 2 années et qu'il n'y a pas de changement de hauteur significatif, il n'y a **pas de changement**, ce qui est représenté en **bleu**, avec la valeur **4**, car cela permet de les distinguer de ce qui est invalide.

<br/>

## 4.4 Choix des critères

- **Pas de hauteur minimale** pour la détection LiDAR : le raster de classe simple de LiDAR étant déjà assez précis, il n'y a pas une grande utilité à mettre une hauteur minimum pour détecter les bâtiments. De plus, comme des tests nous l'ont montré, l'ajout d'un seuil de hauteur élimine certains bâtiments, ce qui n'est évidemment pas l'objectif. Autrement, ce critère est déjà utilisé dans la déduction des classes LiDAR à partir d'une analyse des nuages de points.
- Seuil de modification de hauteur à **2.0 mètres** :  Après plusieurs tests, nous avons vu que si le seuil est trop grand, certaines modifications de hauteurs significatives ne sont pas détectées. Et si ce seuil est trop bas, des petites différences de prises de données entre les 2 années peuvent mener à une détection de modification de hauteur là où il n'y en a pas.
- Seuil de probabilité de l'inférence FLAIRHUB à **20/255** : Ce qui compte ici, c'est que le seuil soit très bas, le but étant d'avoir une zone de détection de bâtiment la plus large possible. Puisque nous allons restreindre cette zone avec le LiDAR par la suite, il est inutile d'avoir une zone trop petite.

<br/>

En outre, il est possible de remarquer, notamment au vocabulaire, mais aussi à certains choix, que nous avons concentré notre travail sur les bâtiments. Notre code est néanmoins appliquable à tous les objets des classes inclues dans FLAIRHUB et LiDAR.

<br/>




# 5. Résultats

Cette section présente les résultats obtenus par notre pipeline de détection de changement urbain, appliqué à plusieurs zones de la métropole de Lyon. Nous procédons en trois temps : une présentation des tests réalisés (démonstrations qualitatives et éléments de validation), puis une discussion des limites observées.

## 5.1 Préambule

Avant de présenter les résultats, quelques conventions et précisions méthodologiques doivent être posées :

- Dans les illustrations qui suivent, les inférences FLAIR-HUB seules sont représentées **en rouge**, tandis que les rasters de classes issus du LiDAR sont représentés **en rose**.
- L'ensemble des tests présentés ici porte exclusivement sur la détection de changement des **bâtiments**, sauf pour la comparaison des approches.
- Le modèle FLAIR-HUB utilisé par défaut est **RGB_swinlarge**, choisi pour son bon compromis entre précision et temps de calcul, dans un contexte où les inférences sont réalisées orthophoto par orthophoto. Dans la dernière partie des tests, nous avons également mobilisé le modèle **IR_swinlarge**, afin d'exploiter l'information de proche infrarouge disponible dans les orthophotos de la métropole.
- La validation des résultats s'appuie principalement sur une comparaison visuelle avec l'orthophoto correspondante. Lorsque celle-ci ne permet pas de lever l'ambiguïté sur la nature réelle d'une structure, nous avons complété l'analyse par une observation au sol via Google Street View, en vérifiant au préalable que le bâtiment concerné n'avait pas évolué entre l'année étudiée et la date des prises de vue Street View.
- Tout au long du stage, nous avons fait des points avec Nicolas Sapay qui travaille pour la métropole de Grand Lyon et qui a déjà effectué des travaux sur des sujets similaires. Ces échanges nous ont permis de poser clairement les besoins et objectifs inhérent à notre sujet.

## 5.2 Tests

### 5.2.1 La Doua

Le premier test porte sur le secteur de La Doua, à proximité du FIMI de l'INSA Lyon, et constitue un cas d'étude représentatif de notre objectif : il présente à la fois une destruction franche en son centre et une construction nouvelle au sud. La figure suivante illustre une détection de changement fondée uniquement sur FLAIR-HUB, à une résolution de 50 cm/px (2018 à gauche, 2023 à droite). À ce stade des tests, la différence de hauteur n'était pas encore intégrée au pipeline ; la couleur jaune indique donc simplement la présence d'un bâtiment sur les deux années.

![Résultat change-detection2](images/change-detection2.png)
![Résultat change-detection2-bis](images/change-detection2-bis.png)

FLAIR-HUB détecte correctement la destruction centrale ainsi que la construction au sud, mais deux limites apparaissent :

- un bâtiment situé à l'est est signalé à tort comme détruit ;
- un débordement est observé au sud, où le modèle détecte une construction inexistante.

Plus généralement, les inférences manquent de précision : on observe de nombreux débordements ainsi que des interprétations divergentes d'un même bâtiment selon l'année considérée. Cette instabilité pourrait s'expliquer par la différence de luminosité entre les prises de vue : la teinte apparente d'une surface variant avec l'intensité et l'inclinaison du rayonnement solaire, deux orthophotos de la même journée prises à des heures différentes pourraient déjà produire des interprétations distinctes par FLAIR-HUB.

À titre de comparaison, voici le résultat d'une détection de changement fondée uniquement sur les données LiDAR :

![Résultat change-detection-lidar1](images/change-detection-lidar1.png)
![Résultat change-detection-lidar1-bis](images/change-detection-lidar1-bis.png)

Cet exemple met en évidence les atouts du LiDAR :

- l'ensemble des constructions est correctement détecté ;
- les contours des bâtiments sont précis, sans débordement comparable à celui observé avec FLAIR-HUB ;
- aucune erreur de détection n'est constatée.

Sur ce cas simple, le LiDAR seul semble donc suffire à produire une détection de changement qualitative, d'autant qu'il apporte une information de hauteur dont FLAIR-HUB seul est dépourvu. Les exemples suivants montrent cependant que la situation est plus nuancée.

### 5.2.2 Part-Dieu

Le secteur de Part-Dieu constitue un cas plus complexe. Les deux inférences FLAIR-HUB suivantes, à 50 cm/px, illustrent plusieurs imperfections :

![Inférence 1 Part-Dieu](images/flair1-2018-part-dieu.png)
![Inférence 2 Part-Dieu](images/flair1-2023-part-dieu.png)

- en 2023, une large partie du centre commercial Westfield (le grand bâtiment central) n'est pas reconnue comme un bâtiment mais classée comme sol ;
- les ombres portées créent des variations de teinte que le modèle interprète à tort comme des transitions bâtiment/sol ;
- le modèle a tendance à classer les cours intérieures des bâtiments comme des bâtiments à part entière, ce qui n'est pas souhaité.

La détection de changement fondée uniquement sur les classes LiDAR donne le résultat suivant :

![Résultat change-detection-lidar2](images/change-detection-lidar2.png)
![Résultat change-detection-lidar2-bis](images/change-detection-lidar2-bis.png)

Ce cas révèle une première limite du LiDAR seul : une destruction est détectée à l'arrière du Westfield, à un endroit où aucun bâtiment n'a en réalité disparu. Cette erreur s'explique par la présence d'un sol surélevé, que le LiDAR interprète comme un bâtiment, uniquement pour l'année 2018, et non pour 2023. Cette incohérence illustre l'intérêt complémentaire de FLAIR-HUB, qui identifie correctement cette zone comme un sol.

Un second problème, plus structurel, concerne le raster de Modèle Numérique de Terrain (MNT), utilisé directement pour calculer la différence de hauteur entre années. Les deux rasters ci-dessous (2018 et 2023) sont produits par interpolation linéaire puis lissage par filtre gaussien ; à noter que le raster 2023 obtenu par notre méthode est identique à celui fourni par la Métropole de Lyon (seule référence externe disponible, pour 2023 uniquement) :

![MNT pour Part-Dieu 2018](images/mnt-part-dieu-2018.png)
![MNT pour Part-Dieu 2023](images/mnt-part-dieu-2023.png)

Ces deux rasters diffèrent sensiblement, pour plusieurs raisons :

- le sol surélevé étant classé comme bâtiment en 2018, les pixels de cette zone sont interpolés à partir des points environnants, situés au niveau du sol réel, ce qui sous-estime la hauteur effective du terrain à cet endroit ;
- cette même surélévation est en revanche correctement détectée en 2023, ce qui accentue l'écart entre les deux MNT et pose problème lors du calcul de la différence de hauteur ;
- la présence de tunnels dans la zone entraîne une interpolation erronée du sol environnant, entre la hauteur du tunnel et celle du sol réel, produisant un MNT artificiellement « en pente ». Ce biais est difficile à corriger sans intervention manuelle pour exclure les zones concernées de l'interpolation, et peut conduire à des différences de hauteur erronées.

Ce cas particulier a fait l'objet d'un échange avec Nicolas Sapay. Il a été convenu que le Westfield constituait un bâtiment atypique et peu représentatif du cas général visé par cette étude, et qu'il n'était donc pas pertinent d'y consacrer davantage de temps. Cet échange a néanmoins permis d'identifier d'autres secteurs de la ville présentant des particularités intéressantes, sur lesquels tester l'intégration de la différence de hauteur dans la détection de changement. Il a également été question de l'intérêt potentiel du proche infrarouge disponible dans les orthophotos de la Métropole, dont l'exploitation nécessite un modèle FLAIR-HUB dédié.

### 5.2.3 Sud de Confluences

Le secteur sud de Confluences présente un autre cas de figure intéressant : celui des **structures temporaires**. Un cirque y est en effet présent en 2018, mais absent de l'orthophoto de 2023.

La figure suivante montre la sortie `class_prob` de FLAIR-HUB, seuillée à 20/255 (en rouge), superposée aux données LiDAR (en rose), pour l'année 2018 :

![flair en class_prob superposé à lidar ](images/flair3-conf-seuil20.png)

Deux observations se dégagent :

- le cirque est détecté comme un bâtiment par le LiDAR, mais pas par FLAIR-HUB, de même que la station-service au nord-est, que l'on ne souhaite pas non plus considérer comme un bâtiment ;
- l'utilisation de `class_prob` avec un seuil bas permet un débordement volontaire au-delà des contours réels des bâtiments, ce qui est recherché puisque ce débordement est ensuite corrigé par le LiDAR, qui impose des contours de bâtiments précis.

La combinaison des deux sources produit le résultat suivant en détection de changement :

![change detection Confluences 2018](images/change-detection1-conf-2018.png)
![change detection Confluences 2023](images/change-detection1-conf-2023.png)

Le résultat est globalement satisfaisant : la structure temporaire est correctement éliminée de l'analyse, et les constructions/destructions sont bien identifiées. Deux limites subsistent néanmoins :

- une modification de hauteur est détectée par le LiDAR à un endroit où elle n'existe pas en réalité, probablement en raison de la topographie montante et de la proximité de l'eau, propice aux imprécisions ;
- FLAIR-HUB ne détecte pas systématiquement un bâtiment dans son intégralité.

### 5.2.4 7ᵉ arrondissement

Le test suivant porte sur le 7ᵉ arrondissement de Lyon, avec une détection de changement présentée directement pour l'année 2023, à 20 cm/px pour FLAIR-HUB :

![détection de changement sur 2023 en 20cm/px pour FLAIRHUB](images/change-detection-7e-2023-20cm-2.png)

Le résultat global est de bonne qualité :

- les constructions et destructions détectées correspondent à des changements réels, et sont correctement délimitées grâce au LiDAR ;
- certains changements de hauteur, ainsi que des cas de destruction-reconstruction, sont correctement identifiés comme des modifications.

Plusieurs imperfections notables persistent toutefois :

- sur les zones de chantier, le LiDAR détecte les grues de construction, ce qui génère un changement de hauteur artificiel ou une coupure dans le bâtiment ; ce cas, trop spécifique, ne constitue pas une priorité de résolution ;
- dans les données LiDAR de 2018, certains points au sommet de bâtiments aujourd'hui détruits sont manquants ;
- un store situé au nord est détecté à tort comme une construction nouvelle ;
- certains contours de bâtiments sont signalés comme ayant changé de hauteur, ce qui s'explique probablement par une différence de qualité d'acquisition entre les campagnes LiDAR de 2018 et de 2023.

De manière générale, une différence de qualité en défaveur de l'année 2018 est observée par rapport à 2023 ; elle se traduit également par un temps de traitement plus long des tuiles LiDAR 2023, révélateur d'une densité de points supérieure.

## 5.3 Comparaison des approches pour les bâtiments

Ces derniers tests visent à déterminer la configuration de FLAIR-HUB minimisant les erreurs de détection. Quatre configurations ont été comparées :

- orthophoto RGB, résolution 50 cm/px ;
- orthophoto RGB, résolution 20 cm/px ;
- orthophoto IR, résolution 50 cm/px ;
- orthophoto IR, résolution 20 cm/px.

Nous allons tester plusieurs endroits afin d'en ressortir un tableau récapitulatif de la meilleure approche, soit en 20cm/px, 50cm/px, et orthophoto RGB ou IR.
Cela permettra d'avoir un résultat fondé sur un maximum de tests. <br/>
Nous devrons à chaque fois faire les 4 tests, à part si un résultat est clairement moins bien que les autres. Mis à part ça, il n'y a pas de raison évidente de dire qu'une approche est meilleure qu'une autre. Par exemple, les résultats de 50cm RGB peuvent potentiellement être meilleures qu'en 20cm RGB, malgré une précision plus grande en 20cm.

Les orthophotos IR combinent le proche infrarouge, le rouge et le vert. La résolution de 20 cm/px est *a priori* la plus performante, bien que plus coûteuse en temps de calcul, car c'est à cette résolution que le modèle swinlarge de FLAIR-HUB a été entraîné.

Pour voir les résultats des détections de changement réalisées, les illustrations seront montrées en Annexe. Certaines analyses plus précises seront données sur des images.

### 5.3.1 Gratte-ciel

Ce test est mené sur un secteur à l'ouest du quartier Gratte-Ciel à Villeurbanne.

Après analyse des résultats, nous pouvons remarquer que globalement la qualité est assez constante entre les différentes approches, mais la configuration 20cm/px IR est celle qui a donner le meilleur résultat. En effet, il y a quelques erreurs, mais c'est celle qui en a le moins et l'ensemble des bâtiments est détecté, les constructions et destructions apparaissent clairement, et les modifications de hauteur sont correctement repérées.

Autrement, à l'issue d'un dernier échange avec Nicolas Sapay, il a été convenu que ces résultats étaient globalement satisfaisants, et que nous approchions de la limite de qualité atteignable par la seule combinaison de FLAIR-HUB et du LiDAR dans leur configuration actuelle. Cet échange a ouvert la discussion sur d'autres pistes méthodologiques permettant d'aller au-delà de cette limite, développées en section 6.2.

### 5.3.2 Duvivier

Ce test est mené sur le secteur autour de la rue Paul Duvivier.

Après analyse des résultats, les détections de changement en 20cm sont les meilleures, elles détectent bien tous les bâtiments et ont peu d'erreurs, mais les erreurs sont différentes. La détection en 50cm RGB est satisfaisante, mais il y a quand même des manques sur les petits bâtiments. Enfin, la détection en 50cm en infrarouge a beaucoup de lacunes.

### 5.3.3 7e arrondissement

Ce test est mené dans une partie du 7e arrondissement de Lyon où il y avait pas mal de travaux, constructions et destructions entre 2018 et 2023. Un store est aussi présent à un endroit.

Au final, toutes les approches ont détectées le store. la détection en 50cm IR est toujours bien moins bonne que les autres. Les détections en 20cm sont ici à peu près équivalentes, le résultat est le même mis-à-part quelques détails négligeables. <br/>
En outre, cette fois-ci, la détection en 50cm RGB a toujours quelques lacunes, mais a détecté un bâtiment non détecté par les configurations en 20cm/px. Le résultat reste moins bien, mais très proche de celui en 20cm/px.

### 5.3.4 Confluences

Ce test était principalement fait pour tester la capacité des différentes approches à détecter la présence de structures temporaires. Dans notre cas c'est un cirque.
- Les détections en 50cm ne l'ont pas détecté, ce qui est positif
- La détection en 20cm RGB la détecte un peu, ce qui n'est pas très satisfaisant
- La détection en 20cm IR la détecte entièrement, ce qui est incorrecte

### 5.3.5 Tableau récapitulatif des approches pour la détection de bâtiments

<br/>

| Approche   | Avantages | Inconvenients   |
|------------|-----------|----------|
| 50cm IR    | - Exécution rapide <br/> - Structure temporaire non détectée  | - De nombreux bâtiments non détectés <br/> - Manque de précision  |
| 50cm RGB   | - Exécution rapide <br/> - Structure temporaire non détectée <br/> - Est parfois plus précise qu'en 20cm  | - Certains bâtiments non détectés <br/> - Manque de précision  |
| 20cm RGB   | - Grande précision <br/> - Tous les bâtiments détectés | - Exécution lente <br/> - Structure temporaire détectée <br/> - Quelques erreurs de précision  |
| 20cm IR    | - Grande précision, parfois meilleure qu'en RGB <br/> - Tous les bâtiments détectés | - Exécution lente <br/> - Structure temporaire détectée <br/> - Quelques erreurs de précision  |

<br/>

Pour conclure, voici les choix à faire en fonction de ce qu'on cherche :

- Pour une exécution rapide, pour l'exécution du workflow sur des échantillons assez grands, l'approche 50cm RGB est l'approche à privilégiée, car rapide et avec une précision assez satisfaisante
- Sur un échantillon plus restreint, les approches 20cm sont à peu près de la même qualité, bien que différentes. Sur nos quelques tests, l'approche en 20cm IR semble être un peu meilleure, sauf quand il y a des structures temporaires. 

<br/>

## 5.4 Comparaison des approches pour la végétation

Cette fois-ci, l'objectif va être de dresser un tableau comparatif des approches, comme pour les bâtiments, mais pour la végétation.

En prévention, la comparaison de végétation entre 2018 et 2023 est peu révélatrice parfois, puisque les données n'ont pas été prises pendant la même période de l'année. Les données 2018 dates d'avril et les données 203 d'août et septembre. Les abres sont donc plus feuillus en 2023 qu'en 2018.
On va donc ici seulement rechercher s'il y a de la végétation non détectée, et avec quelle précision elle est détectée.

Enfin, on peut voir que les résultats sont beaucoup moins exploitables que ceux sur les bâtiments, non pas que par rapport à la différence de période dans l'année, mais aussi de l'adaptation directe du code appliqué à la végétation. Il y aura beaucoup d'endroits avec des constructions, destructions, changements de hauteurs et non changements que s'entrelasseront. Cela est dû notamment à la structure moins régulière de l'arbre.

### 5.4.1 Gratte-Ciel

- Toutes les approches ont des lacunes sur certains arbres qui ne sont pas détectés
- Les approches de même résolutions ont presque exactement le même résultat
- Les approches en 20cm/px sont un peu plus qualitatives qu'en 50cm/px, bien que globalement similaires.

### 5.4.2 Confluences

Ce qui est intéressant à affirmer en premier lieu, c'est que tous les résultats ont été assez correctes, peu importe l'approche. L'endroit sur lequel nous nous sommes focalisé est bien le seul endroit où il y a avait des imperfections.

Ce qu'on voit :

- Toutes les approches détectent cet objet blanc (peut-être un réservoir) présent sur les 2 années, comme un arbre qui n'a pas changé. Ce n'est évidemment pas le cas. C'est assez étonnant que FLAIRHUB détecte un arbre en cet endroi pour les 2 années. Il y a seulement une hésitation pour la détection en 2023 pour l'apporche 50cm RGB.
- Une partie du bateau est détectée comme de la végétation par les 2 approches en 50cm.

Les approches 20cm sont donc un peu meilleures sur cette orthophoto, même si ce n'est pas une différence flagrante.

### 5.4.3 7e arrondissement

- Cet exemple montre encore que les approches 20cm sont meilleures que les 50cm, pour ce qui est de la végétation. En effet, l'absence de détection sur certains arbres en est une des preuves, puisque ces arbres sont bien détectés dans les 2 approches 20cm/px.
- Il était intéressant de comparer les approches 20cm. Bien quelles soient extrêmement similaires, sur l'exemple choisi, on observe que l'approche IR détecte mieux la végétation dans les parties ombragées. 

### 5.4.4 Tableau récapitulatif des approches pour la détection de la végétation

| Approche   | Avantages | Inconvenients   |
|------------|-----------|----------|
| 50cm IR    | - Exécution rapide <br/> - Précision globalement assez bonne  | - Certaines végétations non détectés <br/> - Fait quelques erreurs |
| 50cm RGB   | - Exécution rapide <br/> - Précision globalement assez bonne  | - Certaines végétations non détectés <br/> - Fait quelques erreurs |
| 20cm RGB   | - Grande précision <br/> - Moins de fausses détections qu'en 20cm | - Exécution lente <br/> - Quelques fausses détections <br/> - Détecte mal la végétation ombragée  |
| 20cm IR    | - Grande précision <br/> - Moins de fausses détections qu'en 20cm <br/> - Détecte la végétation même ombragées | - Exécution lente <br/> - Quelques fausses détections  |

Pour conclure, ce tableau montre bien que les approches en 20cm sont meilleures qu'en 50cm, avec l'approche 20cm IR étant de peu la meilleure. Néanmoins les résultats sont en réalité assez proche, donc pour une exécution bien plus rapide les approches en 50cm sont totalement satisfaisantes. 


## 5.5 Limites

Plusieurs limites transversales se dégagent de l'ensemble de ces tests :

- **Pas de vérité terrain.** En effet, comme précisé précédemment, notre seul moyen d'évaluation des résultats a été le moyen visuel, soit directement sur l'orthophoto ou avec google street view. Bien que cette méthode nous permette d'interpréter les résultats avec une efficacité suffisante, nous n'avons pas pu quantifier qualitativement toutes les différentes erreurs présentes dans les tests, puisqu'il aurait fallu utiliser une vérité terrain, afin de comparer notre détection avec la réalité.
- **Imperfections résiduelles de la détection.** Certains bâtiments ne sont pas détectés alors qu'ils devraient l'être, et des changements parasites sont régulièrement détectés en bordure de bâtiments, ce qui nuit à la propreté des contours obtenus.
- **Cas particuliers non traités.** Notre méthode ne traite pas les cas atypiques (bâtiments aux formes ou couleurs complexes, zones de chantier, structures temporaires en dehors du cas testé, etc.). Une ville étant en constante évolution, ignorer ces zones spécifiques empêche d'obtenir un historique complet et continu de son évolution bâtie.
- **Résultat difficilement exploitable en l'état.** La détection produit un ensemble de pixels contigus, et non des objets « bâtiment » individualisés. Une étape de nettoyage supplémentaire serait nécessaire avant d'envisager une vectorisation exploitable des résultats.
- **Périmètre volontairement restreint au bâti.** Faute de temps, nos travaux se sont concentrés exclusivement sur la détection de changement des bâtiments, alors que le projet IArbre, dans lequel s'inscrit ce travail, porte avant tout sur le suivi de la végétation. La typologie de changement retenue ici (destruction, construction, modification de hauteur, absence de changement) mériterait d'être affinée, par exemple en intégrant une notion de modification en largeur, ou en raisonnant en termes de changement de classe de végétation plutôt que de simple variation de hauteur, cette dernière étant par nature une caractéristique évolutive de la végétation. La différence de hauteur conserve néanmoins un intérêt propre, puisqu'elle permet de suivre dans le temps la croissance de la végétation.

<br/>

# 6. Conclusion et Perspectives

## 6.1 Conclusion

Les travaux menés dans le cadre de ce stage ont permis de construire un pipeline complet de détection de changement urbain, combinant les inférences sémantiques de FLAIR-HUB sur orthophotos et les informations géométriques et altimétriques extraites de nuages de points LiDAR. Les tests conduits sur plusieurs secteurs représentatifs de la Métropole de Lyon (La Doua, Part-Dieu, Sud de Confluences, 7ᵉ arrondissement, Gratte-Ciel) ont permis de caractériser les forces et les faiblesses complémentaires de ces deux sources de données.

FLAIR-HUB apporte une information sémantique précieuse, en particulier sa capacité à distinguer un sol surélevé ou une structure temporaire d'un véritable bâtiment, mais souffre d'un manque de précision dans le tracé des contours et d'une sensibilité aux conditions d'acquisition (luminosité, ombres portées). Le LiDAR, à l'inverse, offre des contours de bâtiments nets et une information de hauteur essentielle à la détection de changement, mais peut être mis en défaut par des structures ambiguës (sol surélevé, tunnels, grues de chantier) qu'il interprète à tort comme du bâti, avec des répercussions directes sur la qualité du Modèle Numérique de Terrain et donc sur le calcul des différences de hauteur.

La combinaison des deux approches, en particulier via l'usage de sorties `class_prob` de FLAIR-HUB à seuil bas, permettant un débordement volontaire ensuite corrigé par les contours précis du LiDAR, constitue la configuration la plus robuste testée au cours de ce stage. Les meilleurs résultats pour les bâtiments ont été obtenus avec le modèle swinlarge en résolution 20 cm/px, combiné aux classes et aux hauteurs LiDAR, aboutissant à une détection qualitative des constructions, destructions et modifications de hauteur sur la majorité des cas testés. Néanmoins, pour une exécution plus rapide, la configuration en 50cm/px RGB renvoie aussi des résultats de bonne qualité. <br/> 
En ce qui concerne la végétation, toutes les approches sont à peu près équivalentes. Il vaudrait mieux privilégier les configurations en 20cm/px pour une meilleure précision. La différence avec le 50cm/px n'est tout de même pas énorme, donc utiliser les approches avec cette résolution est tout à fait convenable.

Ce travail a néanmoins permis d'identifier une forme de plafond de qualité atteignable avec la combinaison actuelle de FLAIR-HUB et du LiDAR : au-delà des réglages de configuration (résolution, bande spectrale, seuillage), les erreurs résiduelles semblent davantage liées à des limites structurelles des données et des modèles employés qu'à des paramètres encore optimisables. C'est cette observation qui motive les pistes d'amélioration présentées ci-dessous.

## 6.2 Perspectives

Plusieurs pistes se dégagent pour prolonger ce travail et dépasser les limites identifiées.

**Recours à un modèle dédié au traitement de nuages de points, tel que Myria3D.** Plutôt que de dériver des rasters (MNS, MNT, hauteur, classification) du nuage de points LiDAR puis de les combiner avec les inférences FLAIR-HUB, l'utilisation d'un modèle de segmentation sémantique directement appliqué au nuage de points 3D, comme Myria3D, permettrait potentiellement une classification plus fine et plus robuste des points (bâtiment, sol, végétation, structures temporaires). Cette approche pourrait réduire les erreurs de mauvaise classification à la source (sol surélevé confondu avec du bâti, points manquants au sommet de bâtiments détruits) plutôt que de chercher à les corriger en aval. Cet outil est notamment utilisé par l'IGN pour la production de la donnée BD TOPO [7].

**Amélioration de la robustesse aux conditions d'acquisition.** Une piste complémentaire consisterait à travailler sur la normalisation ou l'harmonisation radiométrique des orthophotos entre les différentes années, afin de limiter la sensibilité de FLAIR-HUB aux variations de luminosité et d'ombrage, qui constituent une source récurrente d'erreurs dans les résultats présentés.

**Passage d'une détection pixellaire à une détection par objets.** Afin de rendre les résultats réellement exploitables (par exemple pour un suivi statistique de l'évolution du bâti ou de la végétation à l'échelle de la ville), une étape de post-traitement visant à regrouper les pixels détectés en objets « bâtiment » individualisés, via une vectorisation combinée à un nettoyage morphologique, devrait être envisagée. Pour cela, on pourrait par exemple utiliser les données BD TOPO, qui présentent déjà des objets bâtiments assez précis. Le problème avec cette solution, c'est que les données anciennes, comme pour 2018 et 2023, ne sont pas faciles d'accès. C'est pour cela que je n'ai pas orienté plus mes tests sur l'utilisation de BD TOPO. Cela reste tout de même une piste d'amélioration du résultat.

**Extension de la typologie de changement à la végétation.** Le projet IArbre visant avant tout le suivi de la végétation urbaine, une prochaine étape naturelle consisterait à adapter le pipeline développé pour le bâti à la détection de changement de végétation, en enrichissant la typologie actuelle (destruction, construction, modification de hauteur, absence de changement) d'une notion de changement de classe de végétation, plus pertinente qu'une simple variation de hauteur pour caractériser l'évolution du couvert végétal dans le temps.




# 7. Bibliographie

- [1] https://liris.cnrs.fr/liris
- [2] https://projet.liris.cnrs.fr/vcity/
- [3] https://iarbre.fr/
- [4] « Détecter les changements grâce aux données 3D LiDAR HD - Portail IGN - IGN ». 24 janvier 2025. https://www.ign.fr/lidar-hd-detection-changement.
- [5] Sarp, Gulcan, Arzu Erener, Sebnem Duzgun, et Kemal Sahin. « An approach for detection of buildings and changes in buildings using orthophotos and point clouds: A case study of Van Erriş earthquake ». European Journal of Remote Sensing 47, nᵒ 1 (2014): 627‑42. https://doi.org/10.5721/EuJRS20144735.
- [6] Arthur Villarroya-Palau, « Synthèse FLAIR-HUB sur la segmentation de la végétation urbaine ». VCityTeam, 8 janvier 2026, https://github.com/VCityTeam/UD-IArbre-Research/blob/master/vegetalisation/Synthese-FLAIRHUB.md
- [7] « Open Source Software for Massive Lidar Data Classification - The French LidarHD Use Case FOSS4G Europe 2025 ». 16 juillet 2025. http://talks.osgeo.org/foss4g-europe-2025/talk/GLBELU/.


<br/>

# 8. Annexe

## 8.1 Gratte-Ciel

Ce test est mené sur un secteur à l'ouest du quartier Gratte-Ciel à Villeurbanne.
**RGB, 50 cm/px** :

![détection de changement sur 2018](images/change-detection-gc-2018.png)
![détection de changement sur 2023](images/change-detection-gc-2023.png)

Ce résultat est globalement satisfaisant : constructions, destructions et changements de hauteur sont correctement détectés, ces derniers s'apparentant toutefois davantage à des cas de destruction-reconstruction. Le LiDAR conserve sa faiblesse habituelle sur les zones de chantier, mais les imperfections restent globalement limitées. FLAIR-HUB obtient également de bons résultats, quoique avec quelques « trous » dans la détection.

**RGB, 20 cm/px** :

![détection de changement sur 2018](images/change-detection-gc-2018-20cm.png)
![détection de changement sur 2023](images/change-detection-gc-2023-20cm.png)

Cette configuration apporte davantage de précision à certains endroits et comble certains trous, mais en crée de nouveaux ailleurs. Le passage à 20 cm/px ne résout donc pas l'ensemble des problèmes et en introduit même de nouveaux, sans, bien sûr, corriger les limites propres au LiDAR, ce qui n'était pas l'objectif de ce test. L'un des inconvénients de la résolution 20 cm/px est que le modèle produit des probabilités plus tranchées, proches de 0 ou de 1. Ce manque de nuance implique que lorsqu'un bâtiment n'est pas détecté avec une forte probabilité, il n'est simplement pas détecté du tout. Abaisser le seuil permet alors d'élargir les contours détectés, ce qui est nécessaire mais insuffisant pour compenser ce défaut.

**IR, 50 cm/px** :

![détection de changement sur 2018](images/change-detection-gc-2018-IR.png)
![détection de changement sur 2023](images/change-detection-gc-2023-IR.png)

Une légère amélioration est observée, sans être décisive : par rapport au RGB simple, deux erreurs sont corrigées, mais deux nouvelles apparaissent.

**IR, 20 cm/px** :

![détection de changement sur 2018](images/change-detection-gc-2018-IR-20cm.png)
![détection de changement sur 2023](images/change-detection-gc-2023-IR-20cm.png)

Cette dernière configuration constitue le meilleur compromis en nombre d'erreurs, sans toutefois représenter une amélioration majeure par rapport aux autres configurations testées.

## 8.2 Du Vivier

**50cm IR** :

![](images/change-detection-duv-2018-ir.png)
![](images/change-detection-duv-2023-ir.png)

On remarque que pas mal de bâtiments ne sont pas détectés. Le résultat n'est pas très satisfaisant.

**50cm** :

![](images/change-detection-duv-2018.png)
![](images/change-detection-duv-2023.png)

Le résultat est encore bien meilleur. Les bâtiments non détectés par l'ir apparaissent bien cette fois-ci. Il reste toujours quelques petits bâtiments non détectés, mais ça reste satisfaisant.

**20cm** :

![](images/change-detection-duv-2018-20cm.png)
![](images/change-detection-duv-2023-20cm.png)

Encore une fois, le résultat est encore meilleur. Les petits bâtiments non détectés en 50cm le sont bien ici, bien que parfois pas dans leur totalité. Il y a bien les destructions, constructions, changements de hauteur et absences de changements. Le résultat est presque parfait.

**20cm IR** :

![](images/change-detection-duv-2018-20cm-ir.png)
![](images/change-detection-duv-2023-20cm-ir.png)

Comme pour le 20cm RGB, tous les bâtiments sont bien détectés, même les petits. Cette fois, même les petits sont détectés en entier. Il y a néanmoins une erreur sur une part de grand bâtiment non détectée sur une année par rapport à une autre.

## 8.3 7e arrondissement

**50cm IR** :

![](images/change-detection-7e-2018-ir.png)
![](images/change-detection-7e-2023-ir.png)

**50cm** :

![](images/change-detection-7e-2018-2.png)
![](images/change-detection-7e-2023-2.png)

**20cm** :

![](images/change-detection-7e-2018-20cm-2.png)
![](images/change-detection-7e-2023-20cm-2.png)

**20cm IR** :

![](images/change-detection-7e-2018-20cm-ir.png)
![](images/change-detection-7e-2023-20cm-ir.png)

## 8.4 Confluences

**50cm IR** :

![](images/change-detection-conf-2018-ir.png)
![](images/change-detection-conf-2023-ir.png)

**50cm** :

![](images/change-detection-conf-2018.png)
![](images/change-detection-conf-2023.png)

**20cm** :

![](images/change-detection-conf-2018-20cm.png)
![](images/change-detection-conf-2023-20cm.png)

**20cm IR** :

![](images/change-detection-conf-2018-20cm-ir.png)
![](images/change-detection-conf-2023-20cm-ir.png)

## 8.5 Gratte-ciel végétation

**50cm IR** :

![](images/change-detection-veg-gc-2018-ir.png)
![](images/change-detection-veg-gc-2023-ir.png)

**50cm** :

![](images/change-detection-veg-gc-2018.png)
![](images/change-detection-veg-gc-2023.png)

**20cm** :

![](images/change-detection-veg-gc-2018-20cm.png)
![](images/change-detection-veg-gc-2023-20cm.png)

**20cm IR** :

![](images/change-detection-veg-gc-2018-20cm-ir.png)
![](images/change-detection-veg-gc-2023-20cm-ir.png)

## 8.6 Confluences végétation

**50cm IR** :

![](images/change-detection-veg-conf-2018-ir.png)
![](images/change-detection-veg-conf-2023-ir.png)

**50cm** :

![](images/change-detection-veg-conf-2018.png)
![](images/change-detection-veg-conf-2023.png)

**20cm** :

![](images/change-detection-veg-conf-2018-20cm.png)
![](images/change-detection-veg-conf-2023-20cm.png)

**20cm IR** :

![](images/change-detection-veg-conf-2018-20cm-ir.png)
![](images/change-detection-veg-conf-2023-20cm-ir.png)

## 8.7 7e arrondissement végétation

**50cm IR et 50cm** :

![](images/change-detection-veg-7e.png)

**20cm** et **20cm IR**:

![](images/change-detection-veg-7e-20cm.png)
![](images/change-detection-veg-7e-20cm-ir.png)

