# Synthèse de COSIA 

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
    - Mika Inisan
  - **Date de création** : 2026-01-08
  - **Date de dernière mise à jour** : 2026-01-08
  - **Version** : 1.0.0
  - **Classification documentaire** : Interne
  - **Langue** : Français
  - **Statut** : Brouillon
  - **Licence** : GNU LGPL v2.1

---

**COSIA (Couverture du Sol par Intelligence Artificielle)** (https://geoservices.ign.fr/cosia) est une base nationale de classification du territoire français, produite par l’IGN dans le cadre du programme **OCS GE** (https://geoservices.ign.fr/ocsge). Elle repose sur une chaîne de traitement automatique combinant orthophotos (BD ORTHO®) (https://geoservices.ign.fr/bdortho), données altimétriques (MNT RGE ALTI®, MNS) et annotations manuelles couvrant plus de 2 500 km² répartis sur 63 départements. Les données sont produites à une résolution de 20 cm/pixel et mises à jour après chaque campagne aérienne, ce qui garantit une continuité temporelle avec un ensemble de jeu de données classées sur plusieurs années disponibles.  

COSIA fournit une classification en **quinze classes principales de couverture du sol**, telles que définies dans le document de référence [1]. Ces classes incluent le **bâti**, les **zones imperméabilisées**, les **zones agricoles**, les **surfaces herbacées**, les **formations ligneuses basses** (arbustes, broussailles), les **forêts de feuillus et de conifères**, ainsi que les **zones humides et plans d’eau**. Ce système de classes vise à fournir une couverture homogène et exploitable sur tout le territoire. 

La description de chaque classe vous est présentée en annexe du document. 

Le modèle utilisé dans COSIA repose sur des techniques de deep learning appliquées à l’analyse d’images, telles que des Vision Transformers (ViT). Il estime, pour chaque pixel, la probabilité d’appartenance à une classe donnée (par exemple : arbre, pelouse, buisson, sol nu, etc.). Les performances du modèle sont évaluées à l’aide d’indicateurs standards de la classification supervisée : la précision (proportion de pixels correctement identifiés parmi ceux prédits comme appartenant à une classe), le rappel (proportion de pixels correctement détectés parmi tous ceux réellement appartenant à cette classe), et l’Intersection over Union (IoU), qui mesure le recouvrement entre les zones prédites et les zones de référence. Sur les millésimes 2017–2023, le modèle COSIA atteint une précision moyenne de 79,5 %, un rappel de 81 % et un IoU moyen de 68,1 %. Bien que performant à grande échelle, la base présente certaines limites : plusieurs ateliers métiers qui avaient pour but d'évaluer plusieurs données d'occupation de terrain, ont relevé des difficultés dans la distinction des strates végétales intermédiaires (par exemple les arbustes), ce qui peut limiter son usage dans des contextes nécessitant une stratification fine de la végétation.

Cependant, il faut garder à l’esprit que nous n’avons aucun moyen de connaître le type de modèle utilisé précisément par l’IGN pour produire les données COSIA. Cela peut poser certains soucis de réplicablité et de disponibilité des données. Cependant, comme nous allons le voir dans la suite, le projet FLAIR-HUB dont COSIA est la continuité, permet de déduire le type de modèle utilisé par l'IGN pour produire COSIA.


## Références
[1] Documentation CoSIA (version 1.0, 2025), https://geoservices.ign.fr/sites/default/files/2025-06/DC_CoSIA_1-0.pdf