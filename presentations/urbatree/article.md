# Cartographie stratifiée de la végétation urbaine à Lyon par fusion d’orthophotos et de LiDAR

## Résumé

Cet article propose une synthèse critique des ressources aujourd’hui disponibles pour cartographier la végétation urbaine dans la métropole de Lyon, ainsi qu’un workflow reproductible combinant orthophotos aériennes et LiDAR. L’objectif n’est pas seulement de détecter la végétation, mais de distinguer de multiples strates utiles à l’analyse urbaine : herbacé, arbuste, arbre, etc. Cette stratification est importante pour les études de climat urbain, de biodiversité et de planification, car les fonctions écologiques et microclimatiques diffèrent fortement selon la structure de la végétation.

Le point de départ de l’article est qu’aucune source ne suffit seule. Les orthophotos décrivent finement les surfaces et permettent une bonne délimitation planimétrique, mais elles renseignent mal la verticalité. Le LiDAR apporte au contraire une information de hauteur indispensable pour séparer les strates, mais il est moins fréquent dans le temps, plus lourd à traiter, et est pas suffisamment fiable pour les surfaces de végétation basses. L’article défend donc une approche de fusion entre information spectrale et information altimétrique.

## 1. Problématique

La végétation urbaine remplit des fonctions multiples, mais ces fonctions dépendent de sa structure. Une canopée arborée n’a pas le même effet qu’une haie ou qu’une surface herbacée. En milieu urbain dense, la difficulté vient du caractère hétérogène des scènes : ombres, sols minéraux, matériaux artificiels, végétation basse, arbustes et arbres coexistent à très courte distance. Parmi ces classes, la strate arbustive constitue le principal verrou méthodologique, car elle occupe une position intermédiaire à la fois sur le plan spectral et sur le plan morphologique.

L’article montre ainsi que la question n’est pas simplement de produire une carte de végétation, mais une carte stratifiée, explicable et réutilisable dans un cadre opérationnel.

## 2. Ressources et données mobilisables

L’étude replace le cas lyonnais dans le contexte français des données ouvertes. Les images satellitaires publiques, comme Landsat ou Sentinel-2, sont utiles pour le suivi temporel, mais trop grossières pour une stratification urbaine fine. Les orthophotos aériennes, en particulier la BD ORTHO et/ou les données ouvertes de la métropole de Lyon, offrent en revanche une finesse spatiale adaptée aux objets urbains. FLAIR-HUB constitue ici une ressource majeure, car il fournit à la fois des données, des modèles pré-entraînés et un cadre expérimental reproductible pour la segmentation d’images aériennes.

Le LiDAR HD apporte la composante verticale qui manque à l’imagerie optique. Il permet de distinguer végétation basse, intermédiaire et haute, même si cette distinction dépend aussi d’autres attributs comme l’intensité du retour ou la structure des échos. Du côté des produits existants, COSIA fournit une cartographie nationale cohérente, mais peu adaptée à une stratification végétale fine. Enfin, la base Armature 2 / Bellec constitue une référence locale très utile pour Lyon, bien qu’elle repose sur une chaîne de production antérieure, partiellement propriétaire, et qu’elle ne puisse donc pas être assimilée à une vérité terrain absolue.

L’analyse conduite dans l’article conduit à un constat simple : les orthophotos sont fortes pour la délimitation des surfaces végétales, le LiDAR est fort pour la discrimination verticale, et leur combinaison est donc une solution cohérente.

## 3. Principe méthodologique

Le workflow proposé repose sur une chaîne Docker reproductible. La branche optique mobilise le modèle `IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet`, appliqué à des orthophotos RGB rééchantillonnées de 5 cm à 50 cm, avec une sortie exportée sur une grille à 1 m. La branche LiDAR rasterise directement le nuage de points classé sur la même grille de travail afin de produire un modèle de surface, un modèle de terrain et une carte d'hauteur.
Dans notre cas, nous avons fait le choix de définir trois strates de végétation.

La fusion repose ensuite sur des règles explicites. Lorsque le LiDAR fournit une information végétale exploitable, celle-ci est privilégiée pour attribuer les strates. Lorsque le LiDAR est absent ou invalide, le système se replie sur la sortie optique. Les seuils utilisés dans le workflow sont de 0,30 m et 5,0 m pour la branche LiDAR, et de 0,3 m et 5,0 m pour le repli optique. L’intérêt de cette approche est de rester interprétable : la fusion n’est pas opaque, elle repose sur une articulation claire entre segmentation d’image et information de hauteur.

![Exemple de résultat de fusion à partir des orthophotos et du LiDAR.](../3DGeoInfo%202026/LyonVegetation/figures/exemple_low_reweight.png)

## 4. Résultats

L’évaluation est menée par comparaison avec une référence issue d’Armature 2 / Bellec, remappée en quatre classes : herbacé, arbuste, arbre et autre. Trois configurations sont comparées : sans repondération, avec repondération faible, et avec repondération forte des classes de végétation basse.

Le résultat le plus net concerne les arbres, qui sont correctement détectés dans toutes les configurations. La végétation herbacée s’améliore lorsque l’on introduit une repondération, et la classe arbustive progresse également en rappel. En revanche, cette amélioration se traduit par une baisse de précision : plus la repondération est forte, plus la détection des végétations basses augmente, mais plus les confusions avec les classes voisines s’accentuent. L’article montre ainsi que la variante `low reweight` constitue le meilleur compromis, tandis que la variante `hard reweight` est plus agressive et plus orientée vers le rappel.

Au niveau agrégé, les gains d’IoU moyen restent modestes, mais l’intérêt du workflow réside surtout dans la différenciation des classes. Les arbres sont déjà bien captés, la végétation herbacée progresse, tandis que les arbustes demeurent la classe la plus difficile. Ce résultat confirme que la strate intermédiaire constitue toujours le principal enjeu scientifique de la cartographie urbaine stratifiée.

![Matrice de confusion d’une configuration fusionnée.](../3DGeoInfo%202026/LyonVegetation/figures/confusion_matrix_percent_low_reweight.png)

## 5. Limites de l’évaluation

L’article insiste à juste titre sur les limites de la référence utilisée. La base Armature est indispensable pour l’évaluation locale, mais elle peut contenir elle-même des artefacts, des zones peu plausibles ou des erreurs liées aux ombres et aux difficultés d’interprétation. Une partie des désaccords entre prédiction et référence peut donc provenir de la référence elle-même. Au moment de la rédaction de cet article, dans le cadre du projet IA.rbre dans lequel cette méthode s'inscrit, ces données et cette méthode sont en cours de validation par les experts métiers de la métropole de Lyon. 

Par ailleurs, les résultats présentés restent descriptifs. Les sorties disponibles correspondent à des exécutions déterministes uniques et à des synthèses agrégées ; elles ne permettent pas de calculer rigoureusement des intervalles de confiance ou des tests de significativité. Enfin, la robustesse spatio-temporelle du workflow reste à confirmer sur d’autres secteurs et d’autres années.

## 6. Apports et perspectives

L’apport principal de l’article est de proposer, dans le contexte français, une articulation claire entre ressources nationales ouvertes et référence locale pour produire une cartographie stratifiée de la végétation urbaine. Le travail montre que ni l’orthophoto ni le LiDAR ne suffisent seuls, mais que leur combinaison permet une cartographie plus cohérente, plus explicable et plus reproductible.

Pour la communauté scientifique francophone, l’intérêt du papier tient autant à son positionnement méthodologique qu’à ses résultats. Il explicite une chaîne de traitement réellement discutable, replace FLAIR-HUB, LiDAR HD, COSIA et Armature dans un même cadre d’analyse, et confirme que la classe arbustive reste le point le plus sensible. En ce sens, il constitue moins une solution définitive qu’une base solide pour des travaux futurs sur la généralisation, l’adaptation locale et l’évaluation multi-temporelle des cartes de végétation urbaine.
