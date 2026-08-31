# Notes sur les modifications

Je vais reprendre ici le code de vegetation/code disponible sur https://github.com/VCityTeam/UD-IArbre-Research
Ce code permet d'extraire une map de vegetation à partir des données d'entrées liDAR et FLAIR-HUB. Naturellement, je peux la réutiliser en la simplifier pour ne retourner que les bâtiments.


## Modifications à apporter

### Fichiers à garder tels quels :

- **extract_nuage** : permet d'extraire les données de nuage de points à partir d'un fichier LAZ (données LiDAR)
- **ortho_extract** : permet d'extraire les données d'une orthophoto
- **ortho_fusion** : fusion des tuiles orthophoto.
- **fusion_nuage** : fusion des nuages de points LiDAR.
- **fusion_flair** : je ne sais pas trop ce que ça fait
- **lidarCorrection** : ne fait ressortir que les bâtiments

### Fichiers à modifier :

- **fusion_lidar_flair** : j'ai juste besoin de retenir la classe des bâtiment sur FLAIR. La logique de fusion de LiDAR est la même, mais il faut changer la partie sur la stratification par hauteur végétale. LiDAR sera utile pour invalider certains pixels bâtiments de FLAIR.
- (pas sûr) **flair_probs_reweight** : peut-être changer le poids des probas des bâtiments 
- (pas sûr) **fusionBetweenFlairAndLidar** : voir ce qui change avec celui d'avant

### Fichier inutile : 

- **calculateVegetationFromLidar** : Je vais l'enlever, mais potentiellement reprendre son code pour faire la même chose avec les bâtiments


## Modifications 1

### Fusion LiDAR Flair

J'ai commencé par **fusion_lidar_flair.py**, où il y avait des logiques importantes à modifier.s
Le plus gros était présent dans create_vegetation_map, que j'ai changé en create_building_map. Le but de cette méthode est de produire une carte des bâtiments.
Pour cela, au lieu de partir des données LiDAR et de corriger avec flair, comme ce qui a été fait pour la végétation, j'ai utilisé une autre logique. On commence avec les pixels où FLAIR détecte un bâtiment, puis on invalide ceux où le LiDAR détecte un sol, donc soit avec la hauteur < 1 mètre, soit si LiDAR a détecté un sol (en classe). 
Cela simplifie fortement la méthode, et j'ai pu supprimer d'autres méthodes devenues inutiles, comme fuse_maps, qui fusionnait une carte LiDAR et FLAIR, sauf que maintenant je n'ai qu'une carte FLAIR. 
Maintenant, le main de fusion_lidar_flair.py enregistre building_map.tif, crée avec create_building_map, à partir de 4 rasters : class_map, height_map, build_mask, second_map (flair_build).

### Configurations

En parallèle, j'ai du modifier **configs.yml**, pour qu'il s'adapte à mes besoins. En gros, j'ai enlevé les attributs de classes concernant la végétation, et j'ai simplement remplacé par un attribut concernant les buildings. Cela revenait donc encore une fois à simplifier le tout.

Pour l'instant, tout ce que je fais est de la simplification, et ça risque d'être toujours le cas car la tâche est moins complexe que de détecter les différentes strates de végétation.

### Pondération

Après cela, je n'ai pas eu à changer flair_probs_reweight.py pour la pondération, mais toujours **configs.yml**. La seule chose à changer était le poids de la classe 0 (building) pour le mettre à 2. Peut-être qu'en faisant des tests, je me rendrais compte qu'il n'y a même pas besoin de changer la pondération ? On verra. J'ai aussi modifier le mapping, car maintenant seule la classe 0 va dans la sortie 0, le reste n'étant pas pris en compte. 


## Tests

Avant de faire la vectorisation, on va d'abord tester si notre code marche !

### Les données du test

Pour cela je vais utiliser une orthophoto de résolution 50cm pour éviter une trop grande durée d'execution. Avec cela, je dois récupérer les données liDAR mns, mnt et mnh à partir des 2 derniers. Pour cela, dans qgis, j'importe les layer mns et mnt à partir des liens que fourni data grandlyon. Après, pour ne récupérer que la surface de l'orthophoto, je fais raster -> extraction -> clip raster by extent. Dedans, j'y mets les coordonnées de l'orthophotographie.
Ensuite je calcule **lidar_height.tif**, en faisant mns - mnt à partir de **lidar_mns.tif** et **lidar_mnt.tif**.
J'avais toujours des mns et mnt gigantesque, donc j'ai du ajouter des options de créations dans l'extraction de raster :
COMPRESS -> LZW
PREDICTOR -> 2
TILED -> YES

## Modifications 2



### Fusion nuage

Il reste des modifications à apporter aux jeux de données. En effet, un problème que l'on retrouve dans la détection de bâtiment et qu'on ne retrouve pas beaucoup dans la végétalisation est le problème des stores. En effet, ce qui nous intéressent est le bâtiment, or le store n'en fait pas partie. A la place, on veut détecter le sol d'en dessous. 
Pour cela, il faut modifier comment on génère lidar_height.tif et lidar_class.tif à partir des MNS et MNT.
Ce que fait fusion_nuage.py :
- Commence par récupérer les coordonnées x, y, z des points, ainsi que leur classe.
- Le MNS conserve le point le plus haut de chaque pixel 
- Le MNT conserve le point le plus bas de chaque pixel, sauf si ce point appartient aux classes de végétation
- clean_mnt_mns() remplit les pixels vides, et fait la moyenne des voisins pour le NaN.

Essayons déjà de comprendre comment sont pris les mns et mnt. 
Puisqu'on a dit qu'on prenant "le point le plus bas" de chaque pixel, ce qui induit qu'il y a plus points pour chaque pixel. Mais donc ici, on dit qu'on ne fait pas de mnt où on détecte de la végétation, car on veut pas dire que le sol est à la hauteur de la végétation. Mais ce qui est étonnant, ce qu'on n'exclue **que** la végétation, et pas d'autres classes, comme les building par exemple. Cela est probablement dû au fait que le travail initial n'était porté que sur la végétation. 
On pourrait donc se dire qu'on va rajouter la classe bâtiment dans **GROUND_EXCLUDED_CLASSES**. Mais cela ne règle pas le problème, puisque les stores sont détectés comme des bâtiments. Donc en fin de compte, on fera l'interpolation, puis mns - mnt et la hauteur est la même puisque MNS est en haut du store.
Deuxième solution : peut-être qu'on peut essayer d'exploiter le fait que les stores sont détectés comme des bâtiments. En effet, si on prend tous les pixels et on prend leur MNS et MNT, alors on observe que MNS = MNT sur les toits, et MNS > MNT sur les stores. La solution serait donc la suivante :

```text
# Avant l'interpolation

Pour tous les pixels dont la classe est BUILDING :
    Si MNT < MNS:
        MNS = MNT
```

Mais donc, cela reviens à changer le calcul de MNS, puisqu'on peut simplement faire :

```text
# Dans la récupération de MNS

Si le pixel fait partie de la classe BUILDING
    MNS = MNT
Sinon
    MNS = point le plus haut
```

Bon. Même avant de faire ça, je me suis rendu compte que sans rien changer, lidar_height ne détectait pas la hauteur des bâtiments, seulement celle des arbres. Or ici on s'intéresse aux bâtiments. J'ai donc rajouté la classe 6 dans GROUND_EXCLUDED_CLASSES, et les bâtiments sont bien détectés. Maintenant, on a un problème, puisque on interpole tous les MNT sur les pixels des bâtiments. Ce qu'il se passe, c'est qu'on veut avoir les MNT en **dessous des stores**, mais **pas sur les toits des bâtiments**, car à ces endroits on fera l'interpolation. Ainsi, il est donc nécessaire de distinguer les stores des bâtiments avant l'interpolation.
On va donc préciser la première partie de l'ancienne solution, qui était :

```text
# Avant l'interpolation

Pour tous les pixels dont la classe est BUILDING :
    Si MNT < MNS (avec une marge de 0.5 mètres):
        MNS = MNT
```

Pour cela, on peut modifier ce qui est déjà présent. On va d'abord enlever intentionnellement la classe BUILDING de GROUND_EXCLUDED_CLASSES, puis on va rajouter un parcours des points comme suit :

```text
Pour tous points de coordonnées x, y : 

    Si le point à une valeur de mns et mnt && le point fait partie de la classe BUILDING_CLASS :
        Si mns[x, y] - mnt[x, y] > 0.5 mètres :
            mns[x, y] <- mnt[x, y]
        Sinon
            mnt[x, y] <- None (NaN)
```

Le problème, c'est qu'on boucle une deuxième fois, ce qui peut être assez lourd. En même temps, le problème c'est que la boucle précédente recalcul les mns et mnt pour chaque point, et vu qu'il peut y avoir plusieurs points par pixel, les mns et mnt d'une même coordonnée peut changer.

Pour la limite j'ai testé :

- 0.5 : Une grande partie de la surface des toits des bâtiments avait une hauteur de 0
- 1 : Cette partie est moins grande
- 1.5 : C'est mieux, mais il ya toujours des bâtiments entiers qui ont toujours la hauteur 0.

La seule raison pour laquelle height pourrait avoir une hauteur nulle, c'est si liDAR détecte un point à 0, donc mnt = 0 et mns devient aussi 0. 
Pour savoir où MNT devient 0, on ne va pas faire l'interpolation, comme ça on va voir quelle hauteur voit MNT. J'ai peur en effet qu'à cause des baies vitrées et fenêtre, il y ait des points à l'intérieur des bâtiments. Alors, pour différencer l'intérieur d'un bâtiment d'un store, l'histoire se complique... 

Ok bon. MNT détecte bien les bâtiments. Alors je ne vois pas comment certains bâtiments sont détectés àa la hauteur 0.
Je vais essayer de voir que le MNS.

Alors MNS va bien, mais c'est MNT qui a un problème quand on fait la modif. Le problème peut venir du fait qui si on détecte une différence grande à un mauvais endroit, par exemple sur un toit, alors le problème sera aggravé par le clean, qui va, en calculant la moyenne sur les zones proches de ce point, détecter un sol un peut trop haut. Le truc c'est qu'ici, ce qu'il se passe, c'est qu'il y a un bâtiment entier où le MNT a été conservé, donc où la différence MNS MNT était au-delà de 2m, ce qui n'est pas un simple bug isolé. 

Ce que je viens de remarquer est peut-être la source du problème. Même très probablement. 


### Potentiel problème de la détection de stores

En fait, au dessus des toits des bâtiments où il y a ce problème, on peut voir qu'il y a tout un tas de tuyauterie, ventilation ou je ne sais quels dispositifs de bâtiment. Ce qu'il y a, c'est que si c'est dispositifs sont en hauteur, alors des points liDAR sont potentiellement situés en dessous. Les points MNT sont donc sur le toit et MNS au dessus des dispositifs, ce qui mène à une différence de hauteur non négligeable entre MNT et MNS. Ainsi, notre code y détecte un store.
Le problème, c'est qu'on ne peut pas distinguer simplement ces dispositifs de réels stores. En effet, on ne peut pas juste augmenter le seuil (que j'ai augmenté jusqu'à 3m), car ce seuil pourrait dépasser non seulement la hauteur des dispositifs au-dessus des bâtiment, mais aussi celle des stores, et ça n'a donc plus de sens.

### Est-ce qu'on peut résoudre ce problème ?

Une des solutions par d'un raisonnement assez simple : pour l'instant, la seule chose que l'on a observer et qui nous pose problème sont ces tuyauteries au-dessus des bâtiments. Ce qui fait réellement qu'on ne peut pas dire que ce sont des stores, c'est le fait qu'ils soient au-dessus du toit. En d'autres termes, le point liDAR le plus bas pour ces tuyauteries est sur le toit, alors que pour les stores il est sur le sol. Donc le problème devient : peut-on distinguer un sol d'un toit quand la classe liDAR détecte un bâtiment ? 
Pour répondre à cela, on voit bien qu'il nous faudrait une **référence de sol**, car en analysant seules les données d'un bâtiment, on ne peut pas savoir à quelle hauteur est le sol. Pour reformuler le problème que l'on a eût précédemment : les points les plus bas d'un bâtiment peuvent être le sol (si on est en dessous d'un store) ou un toit (cas normal ou cas où on est en dessous de la tuyauterie). 
La solution est donc de déterminer où est le sol d'un bâtiment avant le clean.
Pour faire cela, on peut :

- Exclure la classe des bâtiments des calculs de MNT, qu'on va appeler MNT1, ou bien n'inclure que la classe du sol.
- Faire un clean pour que le MNT1 ait la hauteur du sol même au niveau des bâtiments.
- Refaire un deuxième calcul de MNT, qu'on va appeler MNT2, mais cette fois seulement sur la classe bâtiment. 
- Si MNT1 = MNT2, alors il y a un store à cet endroit (donc on met MNS <- MNT à cet endroit, à voir si on peut faire autrement).

### Amélioration

Pour réfléchir sur la solution, on peut se demander si on aurait des cas où MNT1 = MNT2 mais où ce ne pas vraiment un store :
- Si on a une passerelle en hauteur entre 2 bâtiments par exemple
- Si le bâtiment a une structure spéciale, et qu'il y a une partie au-dessus du sol 

![Villa méditerranée à Marseille](../images/villa_mediterranee.png)

Dans ces 2 cas, *a priori*, on veut détecter un bâtiment. En effet, on pourrait vouloir connaître toutes les surfaces de sol, même en-dessous des bâtiments. Mais dans notre cas, on travaille sur la détection de changements urbains en se focalisant seulement sur les bâtiments, donc ça nous intéresse plus de dire que c'est un bâtiment. 
Ce qu'on peut faire pour distinguer ces deux cas d'un store, c'est qu'on peut considérer qu'un store n'est pas très haut, et qu'alors le point le plus haut, que l'on met dans le MNS, est en-dessous d'un certain seuil. (je n'ai pas fait cette distinction dans mon code pour simplifier les choses)

### Limite

En faisant cela, j'ai vu que les points les plus bas au niveau des stores n'était en fait pas au niveau du sol mais au-dessus des stores. Par exemple dans cette illustration, l'endroit entouré doit être considéré comme un store, mais on voit que les points MNT sont bien au-dessus du sol, d'environ 3 mètres.

![Problème lidar](../images/limite_lidar.png)

Le problème ne peut pas venir du code, puisqu'on ne fait que prendre la donnée liDAR la plus basse sur un pixel pour calculer le MNT.
Ainsi, la seule source du problème doit être qu'il n'y a pas vraiment de points en-dessous des stores.
Cela n'invalide pas ce qu'on avait dit sur la tuyauterie, qui elle est beaucoup moins large, et il faut donc un angle moins important pour détecter un point en-dessous. 
Le problème peut donc venir de là : **l'angle avec lequel les données liDAR ont été prises n'est pas assez grand, donc on a pas de points en-dessous des stores**

En lisant des papiers, j'ai vu qu'effectivement, le problème vient du fait que les rayons projetés ont typiquement un rayon de balayage entre 10° et 45°, avec un max pouvant aller à 75°, cela autour de l'axe, donc entre 5° et 22,5°, avec un max à 37,5°.

[Joinville, Olivier de, Sébastien Saur, et Frédéric Bretar. B.3 Le levé laser aéroporté : techniques, applications et recherche. s. d.](https://www-igm.univ-mlv.fr/~riazano/enseignement/SR-TIG-COURS/SR-SIG-COURS_AO_B03_74_JOINVILLE.pdf)

On voit alors qu'avec un angle max aussi faible, le nombre de points en dessous des stores va être fortement limité. 
Il y a donc des techniques plus avancées qui ont été utilisées pour permettre de détecter ce genre de phénomène, mais elles sont évidemment bien trop complexe pour que je l'aborde pendant mon stage, dont ce n'est d'ailleurs pas l'objectif.

### Ce qu'on garde

Au final, on va juste ajouter la classe 6, la BUILDING_CLASS, dans GROUND_EXCLUDED_CLASS. Cela fait que MNT sera interpolé au niveau des bâtiments. Ca donne cela :

![liDAR height](../images/lidar_height.png)

Le résultat est assez satisfaisant pour ce qu'on veut faire.


## Tests

### Reglage de problème

Le problème fan in fan out venait de versions de bibliothèques incompatibles que j'ai dû réinstaller.
J'ai aussi régler un problème où fusion_lidar_flair.py appelait des attributs de la configs qui n'existaient pas.

### Encore un problème

Le lancement s'est cette fois-ci bien lancé, mais le résultat n'est pas satisfaisant. 
- building_map.tif n'a pris en compte que le coin aux gauche de l'orthophotos, et détecte des bâtiments sur des arbres
- flair_vegetation_reweighted détecte les bâtiments, mais aussi des arbres de la forêt ainsi que du sol autour de certains bâtiment. Ce vient probablement du fait que j'ai trop changé le poids de la classes bâtiment, que je vais remettre à 1 pour l'instant

Il me reste donc à revoir le code de run_workflow et fusion_lidar_flair pour régler cela.
Un bon point est que j'ai bien un retour qui n'affiche que les endroits détectés comme bâtiment. Le reste est bien vide.

Je pense que tout était un problème de résolution, que j'ai forcé avec : --ortho-output-resolution 0.5

```text
python run_workflow.py `  --run-name test_batiments `  --xmin-start 1845500 `  --xmin-end 1846000 `  --ymin-start 5177500 `  --ymin-end 5178000 `  --skip-download `  --ortho-output-resolution 0.5 ` --skip-reweight
```

Cette fois building_map.tif fait la taille de toute la map, mais ne distingue que les arbres...
J'ai juste changé cette ligne 

```text
str(probability_raster)
# Que j'ai remplacé par
str(reweighted_raster if reweighted_raster.exists() else probability_raster)
```

### Test 1 et 2

Pour le test 1, j'avais

```text
img_pixels_detection: 512
margin: 128

resolution = 0.5
```

Les bâtiments étaient détectés, mais ils y avaient de gros artefacts, donc j'ai essayé de mettre cette config pour le test 2 :

```text
img_pixels_detection: 1024
margin: 256

resolution = 0.5
```

Cette fois j'ai moins d'artefacts, mais j'en ai encore un peu à certains endroits. Ces artefacts s'observent par des coupure nettes de la zone de détection en plein milieu d'un bâtiment (comme dans l'illustration suivante).
Globalement, on peut voir que la détection est mieux qu'avec flair tout seul. En effet, flair détectait certains sols comme des bâtiments, ce qui est maintenant corrigé par liDAR. Néanmoins, les contours des bâtiments sont toujours un peu vague. Il faut donc peut-être augmenter l'impact des classes liDAR.

### Test 3

Je vais tenter d'augmenter la résolution, et les 2 autres paramètres pour que ça soit ainsi :

```text
img_pixels_detection: 2048
margin: 512

resolution = 0.2
```

J'ai aussi remplacé l'orthophoto de 50cm par celle de résolution 5cm.

Bon c'est trop long alors je vais régler ça pour voir si ça va plus vite :

```text
img_pixels_detection: 512
margin: 128
```

En fait ça bloque à la création des 4 rasters liDAR. Je vais essayer de remettre l'orthophoto de résolution 20cm.

Bon je vais tout repasser en résolution 0.5.

Le résultat est bien mieux. Le vrai problème était que les résolutions des configs étaient à 1 au lieu de 0.5. Il y a malheureusement encore des artefacts en plein mileiu des zones des bâtiments, mais moins grandes qu'avant.

### Test 4

je vais changer ces 2 paramètres pour voir. Les rasters liDAR déjà créés, cela va prendre moins de temps, bien que la précision sera meilleure.

```text
img_pixels_detection: 1024
margin: 256
```

Il y a encore des artefacts, je tente comme ça

```text
img_pixels_detection: 2048
margin: 512
```

J'ai essayé de changer la normalization en scaling et l'inférence a détecté beaucoup d'arbres en plus.
J'ai recalculé les means/stds de la config avec le code means/stds_def.py. On va voir ce que ça va donner avec cette nouvelle normalization.

Bon, j'ai toujours des coupures, et les inférences FLAIRHUB ne sont pas assez bonnes. Je vais essayer d'augmenter la résolution.

### Test 5

Je met la résolution à 20cm/px, seulement dans la config et dans la ligne de commande, pour voir si ça impact nos données liDAR.
Je vais aussi garder l'orthophoto à 0.5 de résolution pour voir si ça pose un problème aussi :
- les rasters liDAR n'ont pas été recalculés
- Mais ça pose un problème avec l'orthophoto qui n'est pas à la bonne résolution.

Je vais tenter d'abord de remplacer l'orthophoto par celle de 0.2 de résolution.
Il y a encore un gros problème, qui montre qu'il faut recalculer les rasters liDAR.

### Test 6

Ca a été long, mais les rasters liDAR ont été créés, et l'inférence a bien marchée. Voici le résultat.

![Détection de bâtiments grâce à liDAR + FLAIR](../images/flair_lidar_20cm_1.png)

- En **rouge**, c'est building_map.tif, le résultat de la fusion lidar + flair
- En **bleu**, c'est l'inférence de flair avant la fusion

On peut voir par certaines traces bleues, qui liDAR a bien permit de corriger les endroits où flair détectait des bâtiments sur du sol. 
Il reste deux problèmes provenant de l'inférence flair :
- les parties de bâtiments en-dessous des arbres sont complètement ignorées par flair et n'apparaissent pas dans le résultat
- les parties de bâtiments à l'ombre sont aussi parfois ignorées par flair

Un dernier problème est simplement la lenteur de l'éxécution. Je vais revenir sur une résolution à 0.5 pour le reste de mes tests, en tout cas au moins jusqu'à la résolution des artefacts.

Pour changer la résolution, je dois :
- supprimer tous les rasters liDAR avec l'ancienne résolution (ils ne sont pas remplacés automatiquement, pratique pour ne pas tout recalculer à chaque fois)
- changer d'orthophoto, mettre celle à la bonne résolution, et l'appeler orthophoto_mosaic.tif
- changer la résolution dans config_zonal_detection.yaml. 
- préciser la bonne résolution dans les paramètres de l'éxécution de run_workflow.py

### Test 7


