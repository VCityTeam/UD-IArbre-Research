# Contexte

Le changement climatique ammène un réchauffement de l'air ambient, et surtout des milieux urbains à cause des surfaces artificielles tel que le beton ou le goudron. Ces surfaces sont aussi imperméables et empechent l'eau de s'infiltrer dans le sol lors des intempéries, amenant à la création de bassins d'eau statique pouvant géner la circulation ou même des inondations locales. La désimperméabilisation des sols, qui vise à remplacer les sols imperméables par des alternatives laissant l'eau s'infiltrer, est une possibilité pour palier à ces problèmes.

Afin de procéder à la désimperméabilisation du sol sur les zones les plus pertinentes pour la ville en terme de refroidissement de l'air urbain, et afin de mieux gérer l'écoulement de l'eau dans les villes, il est possible de construire une chaine de traitement partant de la donnée jusqu'a une représentation pour indiquer les zones les plus propices ou ayant le plusbesoin d'être désimperméabilisées.

# Méthode

Pour celà, nous procéderons à une analyse des terrains que l'on veut étudier, plus précisement des cartes de modèles numériques de terrain (MNT) sous forme de données raster et des cartes contenant l'information sur l'occupation du sol, sous forme de données vectorielles.

En fonction de l'analyse voulue, la résolution des données ainsi que l'analyse à faire seront différentes: pour l'analyse de l'écoulement de l'eau au niveau de la rue, il sera préférable d'avoir un MNT avec une résolution par pixel s'approchant du centimètre, alors que pour une analyse au niveau d'un bloc d'habitations ou d'un quartier, une résolution d'un metre par pixel peut être acceptable. Une analyse sur une zone plus grande, comme une ville, ne serait pas pertinente du point de vue de cette étude, la cible étant le ruisselement de l'eau après dépot par des pluies fines et non des indondations.

Nous supposerons aussi que le sol a un coefficient d'infiltration constant, et ne prendrons pas en compte pour l'instant l'effet d'une pluie passée sur le sol (l'imbibant d'eau) ou la présence de matériaux souterrains empechant l'infiltration de l'eau à une certaine zone.

Une première approche permettant l'identification potentielle de zones d'interet peut se faire grâce à l'indice de Beven-Kirkby. Ce indice prend en compte la surface drainante et la pente locale sur les zones analysées. Cet indicateur indique la tendance d'une zone à pouvoir accumuler de l'eau dans le cas d'un pluie uniforme sur la zone.

Une autre méthode est la méthode des casiers, consistant à découper la zone analysée en un grand nombre de casier de même aire (souvent de fore carrée) et à regarder comment l'eau se dépose dans ces casier, puis comment elle se déplace vers les autre casiers pour voir où elle finira par stagner et en quelle quantité.

Certaines études de cas ont aussi été menées, particulièrement par le CEREMA (méthode EPODES) et l'agglomération Esterel côte d'Azur. 
Ces cas d'études établissent dans un premier temps une carte des contraintes locales (remontées de nappes, argiles, pentes...). Une cartographie de l'infiltrabilité des sols est aussi faite afin de pouvoir localiser les les zones ayant besoin de désimperméabilisation. Un croisement avec une carte d'imperméabilité et une catégorisation sur la zone est ensuite fait afin de surligner les zones les plus impèméables. Enfin, en fonction des résultats précedent, un score est donné pour chaque critère et un carte de potentiel de désimperméabilisation (EPODES), ou une carte de potentiel d'action (Esterel Côte d'Azur) est obtenue permettant de cibler ensuite les zones les plus perinentes.
