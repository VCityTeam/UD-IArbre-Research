# Mise en application des _data pipelines_ dans une plateforme analytique _Big data_ de valorisation de données spatiales dédiée à l’aide à la décision en matière de végétalisation

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
    - Mika Inisan (LIRIS, mika.inisan _at_ liris.cnrs.fr)
  - **Relecteur(s)** :
    - Arthur Villarroya-Palau (LIRIS, arthur.villarroya-palau _at_ liris.cnrs.fr)
    - John Samuel (LIRIS, john.samuel _at_ liris.cnrs.fr)
    - Gilles Gesquère (LIRIS, gilles.gesquiere _at_ liris.cnrs.fr)
  - **Date de création** : 2025-05-20
  - **Date de dernière mise à jour** : 2025-11-13
  - **Version** : 1.0.2
  - **Classification documentaire** : Public
  - **Langue** : Français
  - **Statut** : Final
  - **Licence** : [LICENSE.md](./LICENSE.md) (inclut les conditions relatives aux images)

---

- 1. Avant-propos
- 2. Introduction
- 3. Présentation de IA.rbre
  - 3.1 Contexte et objectifs généraux
  - 3.2 Objectifs techniques
- 4. Ébauche d'une _data pipeline_
  - 4.1. Profilage (semi-automatisable)
  - 4.2. Acquisition et ingestion (automatisable)
  - 4.3. Prétraitement et intégration (semi-automatisable)
  - 4.4. Analyse et modélisation (automatisable)
  - 4.5. Restitution et visualisation
  - 4.6. Gouvernance, qualité et reproductibilité
- 5. Références
- Annexe A. Fondations des chaînes de traitement de données
- Annexe B. Types de traitement de données, systèmes de stockage et de gestion de données

## 1. Avant-propos

Ce document est le fruit d'une première itération, volontairement limitée à une durée de 20 jours ouvrés, d'un travail de recherche sur les chaînes de traitement de données. Il vise à fournir une vue d’ensemble du sujet et à servir de point de départ aux échanges avec les parties prenantes. Un approfondissement ciblé pourra ensuite être mené dans une seconde itération (e.g., proposition d'une architecture complète, _proof of concept_ pour le projet IA.rbre), en fonction des retours recueillis et des besoins effectifs identifiés.

Voici quelques limites de ce document par rapport à un article scientifique exemplaire :
- Appui non exclusivement sur la littérature scientifique avec de la littérature grise (blogs, magazines, sites Web…).
- État de l'art ni exhaustif ni systématique, induisant un manque de représentativité avec la diversité et la qualité des références.

## 2. Introduction

La complexification progressive de la gestion, de l’analyse et de la valorisation des données, couplée à l'explosion du volume de données disponibles et exploitables, issues d’une diversité croissante de sources et marquées par une grande hétérogénéité, a ouvert l’ère du _Big data_. Ce phénomène est souvent résumé par les « 5 V » (volume, vélocité, variété, véracité et valeur) auxquels il est possible d'ajouter d'autres V comme variabilité [77].

Bien que des avancées majeures aient été réalisées, certains défis persistent. Entre autres :
- Disponibilité et accessibilité des données de sources variées, dont les services et l'infrastructure sous-jacente.
- Complétude des données (toutes les données sont accessibles ainsi que leurs dépendances).
- Qualité des données initiales et conservation de la qualité le long du cycle de vie.
- Intégration de données aux formats hétérogènes de sources différentes et leur gestion dans tous les traitements.
- Performance, scalabilité adaptée au volume de données.
- Flexibilité, adaptabilité et évolution des traitements au format de données et aux besoins métiers.
- Standardisation, uniformisation, normalisation, structuration des traitements.
- Valorisation des données, réutilisabilité, reproductibilité, réplicabilité.
- Complexité, maintenance, dépannage, débogage, tolérance à l'erreur, fiabilité et coûts élevés associés.
- Erreur humaine, tâches manuelles fastidieuses et coûts élevés associés.
- Conformité légale (RGPD…) dont la traçabilité, gestion du risque et sécurité.
- Interdisciplinarité.
- Dissémination, diffusion, partage, compréhension.

Les chaînes de traitement de données, ou _data pipelines_, occupent une place de plus en plus centrale au sein des systèmes de gestion de données contemporains grâce à leur rôle structurant. Elles permettent d’automatiser l’intégration, le nettoyage et la transformation de volumes importants de données provenant de sources hétérogènes. En remplaçant des opérations manuelles susceptibles d’introduire des erreurs par des processus systématiques et reproductibles, elles favorisent la qualité des données, leur traçabilité et leur actualisation régulière. Cette approche contribue à améliorer la cohérence des systèmes d’information, à réduire la fragmentation des données et à faciliter un accès en temps réel à l’information, conditions nécessaires à une prise de décision plus fiable et à une meilleure efficacité organisationnelle.

Le projet IA.rbre, plateforme analytique _Big data_ dédiée à la valorisation de données géospatiales pour l’aide à la décision en matière de végétalisation urbaine, illustre concrètement les défis caractéristiques du _Big data_.

Ce document présente, dans un premier temps, le contexte et les objectifs du projet IA.rbre, puis propose, dans un second temps, une ébauche de _data pipeline_ conçue pour en structurer la réponse, en s’appuyant sur les fondements issus de la littérature scientifique (Annexes A et B). Il investigue la problématique : comment les _data pipelines_ permettent de répondre à une partie des défis majeurs de la _data science_ et du _Big data_, tout en fournissant une structure qui facilite la résolution des autres ?

## 3. Présentation de IA.rbre

### 3.1 Contexte et objectifs généraux

Le projet IA.rbre est un des 12 lauréats de l'appel à projets d'innovation « Démonstrateurs d’IA frugale au service de la transition écologique des territoires » (DIAT) lancé par la Banque des territoires dans le cadre du plan d'investissement « France 2030 », de la stratégie « Ville durable et bâtiments innovants » et de la stratégie nationale pour l'intelligence artificielle.

Financé sur 36 mois, il consiste au co-développement, avec un grand nombre d'acteurs terrains en charge de la gestion du territoire, et selon une démarche itérative de prise en compte de leurs besoins communs, d'une plateforme Web analytique _Big data_ interservices interopérables de données territoriales en vue de localiser des zones sur lesquelles il est possible de planter des arbres, sur lesquelles il serait optimal de planter, de végétaliser, de densifier la végétalisation, et d'aider à la décision.

Le projet s'inscrit dans la continuité de travaux initiés par plusieurs des acteurs dont certains du projet comme le projet « Calque de plantabilité » qui a abouti sur une première version d'un calque dédié à la végétation haute, qui constitue la base de travail pour IA.rbre.

Un prérequis important est l'identification, l'inventaire, la collecte et l'intégration des données (données de réseaux, chantiers, inventaire du végétal urbain existant dont le patrimoine végétal notamment avec le projet iPAVÉ [74], données d'occupation des sols…) des différents services publics (comme DataGrandLyon ou IGN) et acteurs privés (e.g., données issues d'une démarche de Déclaration d’Intention de Commencement de Travaux (DICT) [75] auprès d'exploitant des réseaux locaux comme TCL [76], ENEDIS, SFR…) permettant l'amélioration de la connaissance du territoire.

**Où peut-on planter ?** Grâce au croisement de ces données, il est possible d'identifier, de manière précise et à grande échelle, les zones les plus favorables à la végétalisation via le calcul d'une multitude de facteurs de faisabilité (indépendants de l'usage) pondérés (présence d'un parking, présence d'un giratoire, présence d'une voie ferrée, présence d'un réseau de gaz…) permettant de générer un calque de plantabilité où chaque pixel, de résolution dépendante des données, est coloré en fonction de la probabilité qu'il soit possible d'implanter un arbre ou de la végétation à cet endroit.

**Où devrions-nous planter ?** L'usage est ensuite pris en compte et les enjeux croisés, grâce à différents calques thématiques (potentiel de désimperméabilisation, habitabilité incluant les Zones Climatiques Locales et l'étude de la vulnérabilité des populations…) pour permettre la plantation plus efficace avec la maximisation de l'utilisation des services écosystémiques des arbres (réduction de la pollution sonore, qualité de l'air dont la séquestration de particules de CO2, protection solaire et rafraîchissement, lutte contre le ruissellement…)

La première étape du projet concerne le territoire et les données de la Métropole de Lyon mais la plateforme doit être réplicable sur les autres communes, en fonction des données disponibles, pour permettre un passage à l'échelle. Cela nécessite une grande adaptabilité à ces données. L'absence des données peut parfois être palliée par la transférabilité des modèles.

Un regard doit être porté sur la cybersécurité pour s'assurer de la confidentialité des données sensibles en conformité avec le RGPD.

Quelques mots pour caractériser le projet : frugal, responsable, données FAIR, _open data_, _open source_ (code, documentation, méthodologie de développement logiciel, méthodologie de réponse à des objectifs d'intérêt général, résultats, choix, limites…), _open innovation_, _open science_, reproductibilité, bien commun, souveraineté européenne (outils, données, infrastructure, acteurs impliqués…), transparence, explicabilité, interprétabilité.

### 3.2 Objectifs techniques

IA.rbre vient complémenter le calque de plantabilité pour pallier ses limites et répondre aux besoins des acteurs métiers. Il a comme objectifs techniques principaux :
- D'ajouter de nouvelles données (modèles, données territoriales, vérité terrain, données issues de modèles) qui n’étaient pas disponibles, de qualité insuffisante, sensibles ou jusqu’alors inconnues, la génération de nouvelles données à partir de celles existantes, l'amélioration des données par croisement pour permettre les objectifs suivants. Cela nécessite le développement de connecteurs, le contrôle de la qualité, des heuristiques de réconciliation de données…
- De transformer les données ingérées en indices intermédiaires à l'échelle de la maille.
- De reproduire et améliorer la qualité, la fiabilité et l'explicabilité de l'indice de plantabilité (e.g, par IA), basé sur les indices intermédiaires, grâce à une maille plus fine pour les calculs, la prise en compte de facteurs qui n'avaient pas pu être pris en compte à cause de l'absence de données ou de la qualité, des indices intermédiaires et une pondération automatique, via des méthodes d'analyse, des facteurs de faisabilité et d'usage à l'échelle de la maille, modéliser la marge d'erreur.
- Développer un modèle des différentes strates de végétation pour la prédiction.
- D'étendre l'analyse aux enjeux d'usage de la végétation (désimperméabilisation, rafraîchissement des villes, Zones Climatiques Locales (ZCL)…) grâce à la création de calques thématiques pouvant être utilisés pour croiser les enjeux.
- D'améliorer la réutilisabilité, l'intégrabilité.
- D'améliorer la plateforme de visualisation (calques, indices…) et de fournir plusieurs outils d'analyse et d'aide à la prise de décision (module d'annotation des cartes, changement de la pondération des facteurs…).
- De gérer les données dans le temps pour la reproductibilité et pour ensuite fournir un outil d'exploration de scénario et de l'évolution temporelle des villes.

Pour sous-tendre chacun de ces objectifs, un objectif majeur du projet est la conception d'une _data pipeline_.

## 4. Ébauche d'une _data pipeline_

Émergentes des besoins, des objectifs, des contraintes et des défis (directement issus des défis du _Big data_ appliqué à la science de l'information géospatiale) du projet, les étapes et tâches principales sont identifiables. Voici une première ébauche de la _data pipeline_ du projet IA.rbre permettant d'apporter des pistes de réflexion et des bribes de solutions. Elle reste non exhaustive et ne prétend pas résoudre ou lever l’ensemble des problématiques qui devront être traitées pour assurer l’alignement avec les objectifs du projet et son implémentation. Les prérequis comme le profilage sont inclus comme étapes de la _data pipeline_ étant donné que certains aspects sont automatisables.

Les annexes peuvent être consultées au besoin : l’annexe A pour les principes des _data pipelines_, et l’annexe B pour les types de traitements de données et les solutions de gestion et de stockage.

### 4.1. Profilage (semi-automatisable)

- Identification, inventaire, documentation et sélection des sources de données et données candidates (données territoriales, données d'entraînements, modèles, vérités terrain, métadonnées) parmi un large éventail (données issues d'une infrastructure de données spatiales urbaines, bases de données géospatiales, données générées ponctuellement par un service à l'issue de la commande d'une étude, données issues d'une démarche DICT, données issues d'autres projets…) et en incluant le niveau de protection des données.
- Vérification de l’accessibilité, de la disponibilité et de la pérennité de chaque source (format, licence, provenance, fréquence de mise à jour…) en lien avec le principe FAIR.
- Analyses préliminaires de la qualité et statistique (résolution spatiale, précision géométrique et topologique, représentativité temporelle, exhaustivité, présence de biais, exactitude, complétude, cohérence logique, cohérence topologique, cohérence interdonnées, actualisation, volume, détection de duplications et d'erreurs, fiabilité…).
- Évaluation de la contribution marginale de chaque jeu de données et de la redondance (e.g., par réduction de dimensions, analyses factorielles, tests de redondance).
- Sélection finale en prenant en compte la substituabilité (prévoir des alternatives lorsqu’une donnée est manquante ou coûteuse à intégrer).
- Élaboration d'une stratégie d'ingestion incluant les étapes nécessitant l'intervention humaine, la gestion des accès.

Points d’attention :
- Certaines données sont sensibles, clivantes ou difficiles à obtenir (réseaux enterrés, fréquentation de rues, données privées), ces résistances vont au‑delà des défis techniques classiques des _data pipelines_.
- Devant la difficulté d'automatisation de certaines tâches, le recours à l'humain est nécessaire à cette étape.

### 4.2. Acquisition et ingestion (automatisable)

- Vérification de la disponibilité et de l'accessibilité des sources de données, données et substituts.
- Ingestion, dont stockage, par exemple, dans un _data lake_ (Annexe B, section B.2.3.) ou un _data lakehouse_ (Annexe B, section B.2.4.), avec contrôle de versions de données pour la comparabilité, la reproductibilité et la scénarisation.

Prérequis :
- Développement de connecteurs pour automatiser la collecte, notamment via API, téléchargement ou import manuel, selon la disponibilité des sources.

Points d’attention :
- La fréquence d'actualisation des sources de données et leur disponibilité est hétérogène. Il est possible de mettre en place des mécanismes de vérification d'actualisation et de disponibilité qui redéclenchent automatiquement une partie de l'ingestion.
- Le volume peut rapidement exploser, une ingestion incrémentale (ne récupérer que les changements) peut être mise en place.

### 4.3. Prétraitement et intégration (semi-automatisable)

Il peut y avoir des différences de types de données (données territoriales, données d'entraînements, modèles, vérités terrain, métadonnées ; imagerie, nuage de points, cartes ; _rasters_, données vectorielles ; bâtiments, cours d'eau ; GeoTiff, CityGML…), d'actualisation, temporelles, spatiales comme la résolution et le système de projection, de qualité et de pratiques (car obtenu par des méthodes différentes, de disciplines différentes, servant des objectifs différents).

Le but de cette étape est de réconcilier les données territoriales dans un format homogène FAIR permettant la comparabilité en résolvant les discordances, et, selon l'approche frugale, d'organiser les données pour maximiser la réutilisabilité et leur minimalité.

Cela inclut : le nettoyage, le dédoublonnage, la réduction de dimensions quand nécessaire, l'allègement, la factorisation, le reformatage, la normalisation, la correction d'erreurs, la complétion et l'amélioration.

À la manière de l'architecture médaillon (Annexe A, section A.5.2.6.), si l'ingestion a été faite dans un _data lake_ (Annexe B, section B.2.3.), le résultat peut être stocké dans un _data warehouse_ (Annexe B, section B.2.1.). Si l'ingestion a été faite dans un _data lakehouse_, le résultat est composé de vues sur ce même _data lakehouse_ (Annexe B, section B.2.4.).

Prérequis :
- Développement d'adaptateurs, qui peuvent être basés sur des heuristiques, pour harmoniser les données dans un format comparable.
- Ici encore, devant la difficulté d'automatisation de certaines tâches, le recours à l'humain peut être nécessaire.

Point d’attention :
- La réconciliation multi-sources géospatiales est un défi majeur et peut être l'étape la plus fastidieuse.

### 4.4. Analyse et modélisation (automatisable)

Les données prétraitées alimentent différentes briques analytiques :
- Calcul des facteurs de faisabilité (présence d’un réseau, type de sol, contrainte urbaine) et d'autres facteurs, notamment d'usage, (identification des strates, prédiction de la possibilité de densification des pieds d'arbre, potentiel de désimperméabilisation des sols, estimation du niveau d'anthropisation des sols, prédire les zones climatiques locales ZCL permettant de prédire les zones où le potentiel de rafraîchissement urbain est le plus grand, modèle d'habitabilité) simples, avec heuristiques (normes pour les réseaux enterrés, pour les réseaux végétaux…) sur les données initiales, ou nécessitant du _machine learning_, y compris l'entraînement de modèles (uniquement si aucune autre méthode ou résultats n'existent), avec possibilité de substitution de données en cas d'indisponibilité. Nous pouvons envisager trois chemins pour la _data pipeline_, à l’image de la Figure 4 de l'annexe A :
  - Entraîner les modèles sur des données prétraitées, puis stocker les _artifacts_ afin de prédire les facteurs à partir de ceux-ci. Cette approche s’applique notamment lorsque les données sont très précises et abondantes, comme c’est le cas pour la Métropole de Lyon.
  - Réutiliser directement des modèles déjà entraînés sur une autre ville pour effectuer des prédictions. Cette stratégie convient lorsque les données locales sont incomplètes, de faible qualité ou insuffisantes pour concevoir un modèle performant, situation typique des métropoles plus petites ou moins avancées en matière de gouvernance de la donnée.
  - Surentraîner des modèles existants sur les données locales, puis effectuer la prédiction. Cette option correspond à un cas intermédiaire entre les deux précédents.
- Production d’indices intermédiaires (e.g. indice d’imperméabilisation, indice d’anthropisation).
- Génération de calques thématiques croisant faisabilité et usages (désimperméabilisation, Zones Climatiques Locales, habitabilité).
- Génération de données et modèles intermédiaires, via les données initiales, et finaux, via les données initiales, modèles, vérité terrain et données intermédiaires (prédiction de plantabilité, modélisation des strates végétales, amélioration de la pondération de facteurs via apprentissage).
- Calcul de l'évolution des données pour l'appréhension de la transformation des villes dans le temps (évolution temporelle) et prédiction pour la simulation et la scénarisation (simuler l'évolution de la canopée sur les Zones Climatiques Locales…).
- Calcul de l'indice de plantabilité par pondération des facteurs.
- _A/B testing_.

Nous pouvons imaginer réaliser des calculs sur plusieurs données à la même temporalité ou sur une donnée et ses évolutions (notamment pour l'aspect scénarisation du projet).

Les résultats sont stockés pour être utilisés dans les étapes suivantes.

Points d’attention :
- Nécessité de mesurer la valeur ajoutée de chacun de ces modèles (gain lié à l’intégration d’une donnée supplémentaire, niveau d’incertitude, marge d’erreur…). Cela demande des protocoles d’évaluation spécifiques.
- Possibilité de faire de l'apprentissage et de l'amélioration continus en déclenchant automatiquement cette étape après évolution des données initiales.
- La comparabilité est fondamentale pour la fiabilité, l'explicabilité, la transparence, pouvoir suivre l'évolution de la ville… Elle est également nécessaire pour faire de l'_A/B testing_ et des _benchmarks_ de modèles, de méthodes de calcul… Par exemple, un indice de plantabilité calculé sur des données à un instant _t1_ avec _n1_ facteurs n'est pas comparable avec celui calculé sur les mêmes données à un instant _t2_ avec _n2_ facteurs. Les performances d'un modèle entraîné et validé par une vérité terrain ne sont pas comparables avec celles d'un modèle entraîné et validé par une nouvelle version de la vérité terrain.

### 4.5. Restitution et visualisation

La visualisation ne fait pas partie de la _data pipeline_ mais est possible grâce aux résultats stockés des étapes précédentes. Ces résultats doivent permettre différents modes de représentation et de visualisation multiéchelle pour les publics cibles variés (citoyens, services de la métropole, élus). Nous pouvons imaginer des outils spécifiques pour décideurs (vue agrégée, explicabilité des résultats, traçabilité des choix et compromis…).

Pour la partie scénarisation et outils interactifs du projet, il est possible de stocker une copie spécialisée des données prétraitées, par exemple, dans un _data mart_ (Annexe B, section B.2.2.), qui pourra être modifiée à la volée par le _back-end_ après une interaction sur les outils de visualisation _front-end_. Cela redéclenchera l'exécution de la partie analytique de la _data pipeline_ spécifiquement sur ces données, résultant sur une mise à jour de l'affichage.

### 4.6. Gouvernance, qualité et reproductibilité

La _data pipeline_ n’est pas uniquement un flux technique, mais une chaîne de confiance et de responsabilité. À chaque étape, il est nécessaire :
- D'assurer la traçabilité des compromis et choix (quelles données ont été retenues, quels arbitrages ont été appliqués…) pour l'explicabilité et la reproductibilité.
- De prévoir des politiques d’erreur et d’alternatives documentées (que faire en cas de données manquantes ou incohérentes…).
- De faire de la validation continue de la qualité et de la fiabilité (géométrique, statistique, topologique…) via des mécanismes de contrôle.
- De faire de la surveillance (_monitoring_) des performances pour détecter les erreurs, les biais dans les modèles, une augmentation de la latence… La latence faible, l'efficacité sont nécessaires pour l'outil d'aide à la décision. Cela pose la question de la faisabilité : comment vérifier la qualité d'une vérité terrain ? D'un modèle ?

## 5. Références

[1] S. Biswas, M. Wardat, and H. Rajan, “The art and practice of data science pipelines,” _Proceedings of the 44th International Conference on Software Engineering_, pp. 2091–2103, May 2022, doi: 10.1145/3510003.3510057. Available: [https://doi.org/10.1145/3510003.3510057](https://doi.org/10.1145/3510003.3510057)

[2] A. Raj, J. Bosch, H. H. Olsson, and T. J. Wang, “Modelling Data Pipelines,” _46th Euromicro Conference on Software Engineering and Advanced Applications (SEAA)_, pp. 13–20, Aug. 2020, doi: 10.1109/seaa51224.2020.00014. Available: [https://doi.org/10.1109/seaa51224.2020.00014](https://doi.org/10.1109/seaa51224.2020.00014)

[3] D. Sculley _et al._, “Hidden technical debt in Machine learning systems,” _Neural Information Processing Systems_, vol. 28, pp. 2503–2511, Dec. 2015, Available: https://papers.nips.cc/paper/2015/file/86df7dcfd896fcaf2674f757a2463eba-Paper.pdf

[4] A. Lima, L. Monteiro, and A. Furtado, “MLOps: Practices, Maturity Models, Roles, Tools, and Challenges – A Systematic Literature Review,” _24th International Conference on Enterprise Information Systems (ICEIS 2022)_, Jan. 2022, doi: 10.5220/0010997300003179. Available: [https://doi.org/10.5220/0010997300003179](https://doi.org/10.5220/0010997300003179)

[5] D. Sculley _et al._, “Machine Learning: The High Interest Credit Card of Technical Debt,” _NIPS 2014 Workshop_, Jan. 2014, Available: https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/43146.pdf

[6] A. P. Woźniak, M. Milczarek, and J. Woźniak, “MLOPs Components, Tools, Process and Metrics - A Systematic Literature review,” _IEEE Access_, p. 1, Jan. 2025, doi: 10.1109/access.2025.3534990. Available: [https://doi.org/10.1109/access.2025.3534990](https://doi.org/10.1109/access.2025.3534990)

[7] A. Serban, K. Van Der Blom, H. Hoos, and J. Visser, “Adoption and Effects of Software Engineering Best Practices in Machine Learning,” _Proceedings of the 14th ACM/IEEE International Symposium on Empirical Software Engineering and Measurement (ESEM)_, pp. 1–12, Oct. 2020, doi: 10.1145/3382494.3410681. Available: [https://doi.org/10.1145/3382494.3410681](https://doi.org/10.1145/3382494.3410681)

[8] Wikipedia contributors, “Online transaction processing,” _Wikipedia_, Apr. 28, 2025. Available: https://en.wikipedia.org/wiki/Online_transaction_processing

[9] Wikipedia contributors, “Online analytical processing,” _Wikipedia_, Jun. 06, 2025. Available: https://en.wikipedia.org/wiki/Online_analytical_processing

[10] Wikipedia contributors, “Data warehouse,” _Wikipedia_, May 24, 2025. Available: https://en.wikipedia.org/wiki/Data_warehouse

[11] Wikipedia contributors, “Data mart,” _Wikipedia_, Dec. 22, 2024. Available: https://en.wikipedia.org/wiki/Data_mart

[12] Wikipedia contributors, “Data lake,” _Wikipedia_, Mar. 14, 2025. Available: https://en.wikipedia.org/wiki/Data_lake

[13] “What is a Data Lakehouse? | Databricks,” _Databricks_. Available: https://www.databricks.com/glossary/data-lakehouse

[14] Wikipedia contributors, “Lambda architecture,” _Wikipedia_, Feb. 11, 2025. Available: https://en.wikipedia.org/wiki/Lambda_architecture

[15] “Data Pipelines: All the answers you need | DataBricks,” _Databricks_. Available: https://www.databricks.com/glossary/data-pipelines

[16] “Data Warehouse | DataBricks,” _Databricks_. Available: https://www.databricks.com/discover/data-warehouse

[17] “Extract Transform Load (ETL) | DataBricks,” _Databricks_. Available: https://www.databricks.com/discover/etl

[18] “Lambda Architecture Basics | DataBricks,” _Databricks_. Available: https://www.databricks.com/glossary/lambda-architecture

[19] “What is a Medallion Architecture?,” _Databricks_. Available: https://www.databricks.com/glossary/medallion-architecture

[20] “What is a Data Mart? Definition | Databricks,” _Databricks_. Available: https://www.databricks.com/glossary/data-mart

[21] “ACID Transactions in Databases | DataBricks,” _Databricks_. Available: https://www.databricks.com/glossary/acid-transactions

[22] “Ml-ops.org,” Mar. 24, 2025. Available: https://ml-ops.org/

[23] “Kappa Architecture - Data Engineering Wiki.” Available: https://dataengineering.wiki/Concepts/Data+Architecture/Kappa+Architecture

[24] “About Copernicus | Copernicus.” Available: https://www.copernicus.eu/en/about-copernicus

[25] Wikipedia contributors, “Extract, transform, load,” _Wikipedia_, Jun. 04, 2025. Available: https://en.wikipedia.org/wiki/Extract,_transform,_load

[26] Wikipedia contributors, “Extract, load, transform,” _Wikipedia_, May 06, 2025. Available: https://en.wikipedia.org/wiki/Extract,_load,_transform

[27] “How lakehouses solve common issues with data warehouses,” _Databricks_, Feb. 04, 2021. Available: https://www.databricks.com/blog/2021/02/04/how-data-lakehouses-solve-common-issues-with-data-warehouses.html

[28] “Dags — Airflow 3.1.0 documentation.” https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html#dags

[29] “Home,” Apache Airflow. https://airflow.apache.org/

[30] V. Arora, “Exploring real-world challenges in MLOps implementation: a case study approach to design effective data pipelines,” M.S. thesis, Inst. Soft. Eng., Univ. Stuttgart, Stuttgart, Germany, 2024.

[31] K. Shivashankar and A. Martini, “Maintainability Challenges in ML: A Systematic Literature Review,” 2022 48th Euromicro Conference on Software Engineering and Advanced Applications (SEAA), Gran Canaria, Spain, 2022, pp. 60-67, doi: 10.1109/SEAA56994.2022.00018

[32] Z. S. Rad and M. Ghobaei-Arani, “Data pipeline approaches in serverless computing: a taxonomy, review, and research trends,” Journal of Big Data, vol. 11, no. 1, Jun. 2024, doi: 10.1186/s40537-024-00939-0.

[33] A. R. Munappy, J. Bosch, and H. H. Olsson, “Data Pipeline Management in Practice: Challenges and opportunities,” in Lecture notes in computer science, 2020, pp. 168–184. doi: 10.1007/978-3-030-64148-1_11.

[34] C. K. Dehury, P. Jakovits, S. N. Srirama, G. Giotis, and G. Garg, “TOSCAdata: Modeling data pipeline applications in TOSCA,” Journal of Systems and Software, vol. 186, p. 111164, Dec. 2021, doi: 10.1016/j.jss.2021.111164.

[35] H. Foidl, V. Golendukhina, R. Ramler, and M. Felderer, “Data pipeline quality: Influencing factors, root causes of data-related issues, and processing problem areas for developers,” Journal of Systems and Software, vol. 207, p. 111855, Sep. 2023, doi: 10.1016/j.jss.2023.111855.

[36] S. R. Poojara, C. K. Dehury, P. Jakovits, and S. N. Srirama, “Serverless data pipeline approaches for IoT data in fog and cloud computing,” Future Generation Computer Systems, vol. 130, pp. 91–105, Dec. 2021, doi: 10.1016/j.future.2021.12.012.

[37] M. Matskin, S. Tahmasebi, A. Layegh, A. Payberah, A. Thomas, N. Nikolov, and D. Roman, “A Survey of Big Data Pipeline Orchestration Tools from the Perspective of the DataCloud Project,” in Suppl. Proc. DAMDID/RCDL, 2021, pp. 63-78.

[38] S. N. Mitchell et al., “FAIR data pipeline: provenance-driven data management for traceable scientific workflows,” Philosophical Transactions of the Royal Society a Mathematical Physical and Engineering Sciences, vol. 380, no. 2233, Aug. 2022, doi: 10.1098/rsta.2021.0300.

[39] I. Lipovac and M. B. Babac, “Developing a data pipeline solution for big data processing,” International Journal of Data Mining Modelling and Management, vol. 16, no. 1, pp. 1–22, Jan. 2024, doi: 10.1504/ijdmmm.2024.136221.

[40] S. Stoudt, V. N. Vásquez, and C. C. Martinez, “Principles for data analysis workflows,” PLoS Computational Biology, vol. 17, no. 3, p. e1008770, Mar. 2021, doi: 10.1371/journal.pcbi.1008770.

[41] K. Raman, A. Swaminathan, J. Gehrke, and T. Joachims, “Beyond myopic inference in big data pipelines,” Proceedings of the 19th ACM SIGKDD International Conference on Knowledge Discovery and Data Mining, Aug. 2013, doi: 10.1145/2487575.2487588.

[42] S. Agostinelli, D. Benvenuti, F. De Luzi, and A. Marrella, “Big Data Pipeline Discovery through Process Mining: Challenges and Research Directions,” in CEUR Workshop Proc., 2021, pp. 50-55.

[43] O. Oleghe and K. Salonitis, “A framework for designing data pipelines for manufacturing systems,” Procedia CIRP, vol. 93, pp. 724–729, Jan. 2020, doi: 10.1016/j.procir.2020.04.016.

[44] “Apache Kafka,” Apache Kafka. https://kafka.apache.org/

[45] “The 2024 MAD (ML, AI & Data) Landscape,” FirstMark. https://mad.firstmark.com/

[46] “Production-Grade Container orchestration,” Kubernetes. https://kubernetes.io/

[47] “Apache Flink® — Stateful Computations over Data Streams,” Apache Flink. https://flink.apache.org/

[48] Wikipedia contributors, “Change data capture,” Wikipedia, Aug. 15, 2025. https://en.wikipedia.org/wiki/Change_data_capture

[49] “The Snowflake platform,” Snowflake. https://www.snowflake.com/en/product/platform/

[50] “D’un entrepôt de données à une plate-forme de données et d’IA autonome,” Google BigQuery. https://cloud.google.com/bigquery?hl=fr

[51] “Amazon S3 – Stockage d’objets dans le cloud – AWS,” Amazon Web Services, Inc. https://aws.amazon.com/fr/s3/

[52] “Apache Hudi | An open source Data Lake platform | Apache Hudi.” https://hudi.apache.org/

[53] “Home | Delta Lake,” Delta Lake. https://delta.io/

[54] “Apache Iceberg - Apache IcebergTM.” https://iceberg.apache.org/

[55] “Apache SparkTM - Unified Engine for large-scale data analytics.” https://spark.apache.org/

[56] “Accelerate data workflows with the dbt Fusion engine | dbt Labs,” Dbt Labs. https://www.getdbt.com/product/fusion

[57] “Distributed SQL query engine for big data.” https://trino.io/

[58] “Pythonic, modern workflow orchestration for resilient data platforms | Prefect.” https://www.prefect.io/

[59] Dagster, “Modern Data Orchestrator Platform | Dagster,” Dagster. https://dagster.io/

[60] S. Ratliff, “Docker: Accelerated Container Application Development,” Docker, Aug. 25, 2025. https://www.docker.com/

[61] “Terraform,” HashiCorp Cloud Platform. https://developer.hashicorp.com/terraform

[62] “Helm | Helm.” https://helm.sh/

[63] A. Community, “Ansible documentation.” https://docs.ansible.com/

[64] “AI & Data Production | Data Governance Compliance,” Datahub. https://datahub.com/products/data-governance/

[65] “Amundsen, the leading open source data catalog.” https://www.amundsen.io/

[66] “Collibra Data Governance software | Data Governance tool | Collibra,” Collibra. https://www.collibra.com/products/data-governance

[67] “Great Expectations: have confidence in your data, no matter what.” https://greatexpectations.io/

[68] “Soda Data quality.” https://www.soda.io/

[69] “Tableau,” Tableau From Salesforce. https://www.tableau.com/fr-fr/products/tableau

[70] “Power BI – Visualisation des données | Microsoft Power Platform.” https://www.microsoft.com/fr-fr/power-platform/products/power-bi

[71] “GraphQL | A query language for your API.” https://graphql.org/

[72] “Project Jupyter,” Home. https://jupyter.org/

[73] “AFNOR SPEC 2314,” Afnor EDITIONS. https://www.boutique.afnor.org/fr-fr/norme/afnor-spec-2314/referentiel-general-pour-lia-frugale-mesurer-et-reduire-limpact-environneme/fa208976/421140

[74] “🌿 iPAVÉ : du vert dans les données !,” IA.rbre. https://iarbre.fr/actualites/-ipav%C3%A9-du-vert-dans-les-donn%C3%A9es/

[75] “Déclaration de travaux à proximité de réseaux (DT-DICT),” Entreprendre.Service-Public.fr, Apr. 09, 2025. https://entreprendre.service-public.gouv.fr/vosdroits/F23491?profil=tout

[76] “TCL - Transports en commun,” TCL - Transports En Commun. https://www.tcl.fr/

[77] M. Al-Mekhlal and A. Ali Khwaja, “A Synthesis of Big Data Definition and Characteristics,” 2019 IEEE International Conference on Computational Science and Engineering (CSE) and IEEE International Conference on Embedded and Ubiquitous Computing (EUC), New York, NY, USA, 2019, pp. 314-322, doi: 10.1109/CSE/EUC.2019.00067.

[78] Ibm, “Data mesh,” IBM, May 28, 2025. https://www.ibm.com/fr-fr/think/topics/data-mesh

[79] A. Jonker and T. Krantz, “Data fabric,” IBM, Aug. 22, 2025. https://www.ibm.com/fr-fr/think/topics/data-fabric

---

## Annexe A. Fondations des chaînes de traitement de données

- A.1. Notions de base
  - A.1.1. Étapes
  - A.1.2. Propriétés
  - A.1.3. Formats et sources des données, destinations des résultats
  - A.1.4. Modes de déclenchement, d'ingestion, d'exécution
- A.2. Rôles et objectifs
- A.3. Différences avec _data workflow_
- A.4. Représentation
- A.5. Architectures
  - A.5.1. Architecture générale
  - A.5.2. Architectures spécifiques
    - A.5.2.1. _Machine learning_
    - A.5.2.2. ETL (_Extract-Transform-Load_)
    - A.5.2.3. ELT (_Extract-Load-Transform_)
    - A.5.2.4. Architecture Lambda
      - A.5.2.4.1. Objectifs
      - A.5.2.4.2. Limites
    - A.5.2.5. Architecture Kappa
      - A.5.2.5.1. Objectifs
      - A.5.2.5.2. Limites
    - A.5.2.6. Architecture médaillon
      - A.5.2.6.1. Objectifs
      - A.5.2.6.2. Limites
    - A.5.2.7. Architecture 2-tiers (_data lake_ et _data warehouse_)
      - A.5.2.7.1. Objectifs
      - A.5.2.7.2. Limites
- A.6. Infrastructures
- A.7. Opérationnalisation
- A.8. Implémentation moderne
- A.9. Meilleures pratiques
  - A.9.1. Architecture générale
  - A.9.2. Infrastructure
- A.10. Compétences requises

La littérature scientifique regorge d'utilisations du terme « _data pipeline_ ». Le concept est encore à un stade précoce de développement et ne bénéficie pas d'une standardisation et d'une terminologie largement acceptée [1].

Cette annexe investigue la problématique : qu'est-ce qu'une chaîne de traitement de données, selon la littérature scientifique, d'un point de vue conceptuel et pratique ?

Elle fournit une vue d’ensemble des fondations, en couvrant leurs notions de base, rôles et objectifs, différences avec _data workflow_, représentation, architectures et infrastructures, opérationnalisation, implémentation moderne, meilleures pratiques et compétences techniques associées.

### A.1. Notions de base

Le terme _data pipeline_ désigne généralement une structure logicielle qui permet le déplacement et la manipulation systématiques de données provenant de sources potentiellement multiples [31, 32, 35] et hétérogènes, vers des destinations (stockages ou autres) [2, 30, 31, 32, 33, 35, 39, 40].

Les définitions varient quant au niveau d'abstraction : certaines la décrivent comme une partie de logiciel [33], un service unique [36], un graphe orienté acyclique (DAG) [35], tandis que d'autres étendent le concept et la décrivent comme un système logiciel complet avec un écosystème comprenant plusieurs technologies et outils interconnectés [35, 43].

Un consensus général se dégage quant au fait qu’une _data pipeline_ se compose d’une série [2, 30, 32, 40] (chaîne [2, 33, 41, 42], séquence [31, 32, 34, 35], ensemble [36, 39]) d'étapes [32] (processus [30, 32, 33, 36, 39, 40, 41], fonctions [32], opérations [2, 31], outils [39], nœuds [33, 35], éléments de traitement [42], blocs de _data pipeline_ [34], activités [2]) interconnectées à travers lesquelles les données passent de manière séquentielle, la sortie d'une étape servant d'entrée pour la suivante [2, 32, 34, 35, 36, 41, 42].

#### A.1.1. Étapes

Les étapes consistent en un ensemble de traitements spécifiques appliqués aux données, allant de l'extraction, la transformation et le chargement (ETL), au filtrage, à la fusion ou à d’autres formes de manipulation [2, 31, 32, 33, 34, 35, 36, 39, 42]. Voici quelques étapes fréquemment mentionnées : sélection, ingestion, acquisition, extraction, exploration, chargement, traitement, agrégation, gestion, transformation, validation, fusion, filtrage, enrichissement, stockage, analyse, visualisation.

Des divergences apparaissent quant au nombre, à la terminologie et à l'organisation des étapes. Certains travaux adoptent une vision simplifiée, définissant quatre étapes principales (acquisition, intégration, analyse et application concrète) [39], tandis que d'autres mettent l'accent sur les 3 étapes de l'architecture ETL (extraction, transformation et chargement) [34]. Dans les contextes impliquant l'apprentissage automatique, il peut y avoir des étapes spécifiques comme la préparation des « _features_ » et le développement de modèles avec l'entraînement et l'évaluation [30].

Dans [1], les auteurs s'appuient sur l’analyse de _data pipelines_ issues de la littérature scientifique, de la plateforme _Kaggle_, ainsi que de projets de _data science_ d’envergure publiés sur _GitHub_ (_Autopilot_, _CNN-Text-Classification_, _Darkflow_, _Deep ANPR_…) afin de conduire une étude empirique visant, entre autres, à unifier leurs étapes et tâches. L'étude s'intéresse plus particulièrement aux _data pipelines_ de science des données comme une extension des _data pipelines_ classiques, intégrant des étapes propres à l’analyse, à l’entraînement, à l’évaluation et au déploiement des modèles.

Le Tableau 1 présente le résultat de cette unification avec le nom des étapes retenues et une description pour chacune. Les étapes sont organisées dans l'ordre de l'architecture que nous présentons dans la section A.5.1., de l'acquisition de données, au déploiement de la solution, en passant par la préparation des données, la modélisation, l'entraînement, l'évaluation ou encore la prédiction.

![Tableau 1. Description des étapes dans une _data pipeline_ de science des données selon [1].](images/description-of-the-stages-in-ds-pipeline-s-biswas-m-wardat-h-rajan.png)

**Tableau 1. Description des étapes dans une _data pipeline_ de science des données selon [1].**

Certains auteurs notent que les étapes peuvent posséder plusieurs capacités, exécuter plusieurs tâches de traitement [1, 41].

Les étapes d'extraction, acquisition, peuvent porter sur toutes les données d'origine ou une partie, par exemple les données actualisées ; et il est imaginable de les remplacer par des étapes de génération de données.

La préparation des données, autrement appelée transformation des données, est une étape qui permet de réconcilier les divergences et de garantir que celles-ci se trouvent dans un format adapté pour le traitement suivant, tout en garantissant leur qualité. Elle inclut en fonction des besoins les tâches de purge, vérification de l'intégrité, agrégation, fusion, encodage, synchronisation, changement de format et standardisation, normalisation, nettoyage, filtrage, enrichissement, échantillonnage, réduction des dimensions, validation (s'assurer de la qualité notamment que les données sont complètes et justes). Les données transformées peuvent être placées dans un stockage temporaire afin de permettre un retour en arrière rapide en cas de problème.

#### A.1.2. Propriétés

Certaines définitions précisent que les _data pipelines_ doivent présenter des propriétés telles que la modularité, la capacité de déploiement indépendant, la scalabilité et la portabilité dans les environnements _Cloud_, ce qui se manifeste à la conception et à l'implémentation des différentes étapes [34].

De même, certaines définitions décrivent les _data pipelines_ comme entièrement automatisées [30] avec des étapes pouvant être implémentées par programmation [40], tandis que d'autres considèrent l'automatisation comme un objectif de conception plutôt que comme une condition absolue, visant à réduire, mais pas nécessairement à éliminer, l'intervention humaine à chaque étape [33].

#### A.1.3. Formats et sources des données, destinations des résultats

Les _data pipelines_ sont conçues pour traiter des données dans des formats variés (non structurés, semi-structurés, structurés) et provenant de sources hétérogènes, locales ou distantes, centralisées ou distribuées (API, fichiers, base de données, origines d'un _crawling_ ou _scraping_…). De nombreuses implémentations garantissent la compatibilité avec pratiquement toutes les sources de données [30, 33].

La littérature souligne les différences dans les destinations prévues des résultats des _data pipelines_. Alors que certaines étapes aboutissent au stockage, d'autres fournissent des données à des applications telles que des outils de visualisation, d'autres _data pipelines_, des modèles d'apprentissage automatique ou des modèles d'apprentissage profond [2, 33].

#### A.1.4. Modes de déclenchement, d'ingestion, d'exécution

Les modes de déclenchement des chaînes de traitement de données varient. L'exécution peut être déclenchée manuellement ou de manière programmée, ponctuellement, de manière récurrente (par exemple, quotidiennement, hebdomadairement), ou en réponse à des stimuli basés sur des événements, tels que l'arrivée de nouvelles données dans un système de stockage [40].

Les _data pipelines_ prennent en charge différents modes d'ingestion de données, notamment le traitement par lots (« _batch_ ») et continu (« _streaming_ ») [2, 33, 35, 38]. En mode _batch_, les données sont ingérées à intervalles fixes ou lors de déclenchements spécifiques. En revanche, en mode _streaming_, elles consomment et traitent les données en continu dès qu'elles sont disponibles [35, 38]. Il existe certaines approches hybrides, comme les architectures Lambda (section A.5.2.4.), qui intègrent les deux paradigmes d'ingestion et permettent de traiter simultanément plusieurs flux de données [33, 35].

Les traitements peuvent être exécutés séquentiellement ou parallèlement, et de manière centralisée ou distribuée.

### A.2. Rôles et objectifs

Les _data pipelines_ constituent la fondation des activités de traitement, d’analyse et de prise de décision [33, 35] grâce à leur capacité à gérer des flux de données en quantités toujours croissantes. Par exemple, dans le cadre du _machine learning_, elles permettent de les mettre sous une forme appropriée pour l'entraînement des modèles [35].

Un second objectif majeur consiste à réduire la latence dans le développement des produits de données [2, 33]. À cet égard, ces structures permettent de contrôler toutes les opérations liées aux données et d'orchestrer l'ensemble du flux de manière rationalisée de la source à la destination [33, 35]. Elles favorisent leur traitement, leur transfert et leur stockage efficaces et fiables [32, 35], possiblement automatisés, éliminant les erreurs [33]. Par ailleurs, elles contribuent à atténuer les goulots d’étranglement et les délais [33]. Cela augmente la vitesse de bout en bout [2, 33].

Enfin, les _data pipelines_ visent à simplifier la conception et le déploiement des services de traitement des données [36]. Dans cette perspective, elles décomposent les analyses complexes de grands ensembles de données en une série de tâches plus simples [33]. Les propriétés des implémentations des différentes étapes visent à encourager la réutilisation, la composition flexible et la configurabilité pour des usages spécifiques [34, 41]. Elles contribuent également à renforcer la scalabilité, car des étapes peuvent être ajoutées ou supprimées en fonction de la charge de travail et des exigences de traitement [32].

En complément de ces objectifs principaux, les _data pipelines_ poursuivent des objectifs secondaires tels que la reproductibilité [38], la traçabilité et la tolérance aux pannes [33].

### A.3. Différences avec _data workflow_

Tout comme « _data pipeline_ », le concept de « _data workflow_ » est encore à un stade précoce de développement et ne bénéficie pas d'une standardisation et d'une terminologie largement acceptée.

La littérature présente différentes relations entre les termes « _data workflow_ » et « _data pipeline_ », souvent dépendantes du contexte [40].

Dans certains travaux, les deux termes sont considérés comme synonymes [37], par exemple, dans le contexte du développement et de l'ingénierie logiciels [40].

Dans le contexte du _Big Data_, les _data pipelines_ sont parfois définis comme des cas particuliers des _data workflows_, où ces seconds sont davantage orientés vers les utilisateurs finaux, sans expliciter cette notion [37].

« _Data workflow_ » semble souvent désigner un concept, dont les limites sont floues, d'ensemble des flux et traitements de la donnée englobant la logique métier, le cycle de vie, les mécanismes d'orchestration pour gérer l’exécution de tâches (et leurs dépendances) pouvant inclure des étapes humaines (comme d'intervention et de prise de décision), le « _monitoring_ », la gestion des erreurs et la validation des données, la gouvernance, l'opérationnalisation. Tandis que « _data pipeline_ » désigne l'implémentation technique. Par exemple :
- Dans [35], les _data pipelines_ sont décrites comme des _data workflows_ numérisés composés de scripts programmés ou d'outils logiciels simples.
- Dans [32], il est dit « _efficient and reliable data pipelines provides a compelling approach to creating efficient, scalable, and cost-effective data processing workflow_ ».
- Dans [2], il est dit « _The conceptual data pipeline model proposed in this paper has nodes and connectors which perform the activities in the data workflow_ ».

D’autres sources, toutefois, établissent une distinction plus nette entre les deux notions. Certains auteurs utilisent le terme « _data pipeline_ » pour désigner principalement ce qu'un ordinateur exécute, comme l'exécution automatisée d'une série de scripts, tandis que le terme « _data workflow_ » est utilisé pour englober l'ensemble plus large des activités humaines et informatiques qu'un chercheur entreprend pour faire avancer l'investigation scientifique, notamment l'élaboration d'hypothèses, le prétraitement des données, l'écriture de code et l'interprétation des résultats. Dans cette perspective, les _data workflows_ peuvent produire des résultats diversifiés au-delà des logiciels ou des publications académiques, tels que de nouveaux jeux de données, des approches méthodologiques ou du matériel pédagogique [40].

Il existe beaucoup d'autres différences que nous n'expliciterons pas ici, comme la linéarité des _data pipelines_ contre la non-linéarité des _data workflows_ [40].

### A.4. Représentation

La représentation classique, le métamodèle des _data pipelines_, est un diagramme de flux de données qui peut prendre plusieurs formes dont la plus commune est un graphe orienté acyclique (_Directed acyclic graph_, DAG) ou généralement, mais plus rarement, un graphe orienté si des boucles sont autorisées.

Le DAG est d'ailleurs la représentation qui est utilisée dans la plupart des outils d'orchestrateur de _data pipelines_ comme _Apache Airflow_ [28].

### A.5. Architectures

#### A.5.1. Architecture générale

Bien qu'il n'existe pas de normalisation des _data pipelines_, certaines études cherchent à déterminer la structure (étapes, organisation, variations…) et les pratiques typiques.

C’est notamment le cas de [2], qui propose un modèle conceptuel de référence (Figure 1) pour une _data pipeline_ de bout en bout, entièrement automatisée et tolérante aux défaillances grâce à des mécanismes de _monitoring_ automatique, de détection, d’atténuation et d’alerte. Ce modèle assure également la traçabilité des données et prend en compte les défis de gestion de données que les auteurs ont préalablement identifiés. Il a été élaboré grâce à l'analyse de _data pipelines_ existantes et à la conduite puis la synthèse d'entretiens et de réunions, ayant pour sujet plusieurs études de cas d'une grande entreprise de télécommunication, qui ont permis de récolter des données qualitatives. Il a ensuite été validé au moyen d’entretiens qualitatifs auprès de trois grandes entreprises issues des secteurs des télécommunications, de l’automobile et de la fabrication.

Le modèle conceptuel est associé à un métamodèle (Figure 2). Des précisions telles que le lien entre ces deux éléments et la description des étapes sont disponibles dans le papier initial.

![Figure 1. Modèle conceptuel de la _data pipeline_ selon [2].](images/conceptual-model-of-data-pipeline-a-raj-j-bosch-h-h-olsson-t-j-wang.svg)

**Figure 1. Modèle conceptuel de la _data pipeline_ selon [2].**

![Figure 2. Métamodèle pour la construction d'une _data pipeline_ selon [2].](images/meta-model-for-building-data-pipeline-a-raj-j-bosch-h-h-olsson-t-j-wang.svg)

**Figure 2. Métamodèle pour la construction d'une _data pipeline_ selon [2].**

C'est aussi un autre des objectifs de [1] que nous avons présentée précédemment. En effet, en plus d'unifier les étapes et tâches, les auteurs proposent 3 architectures représentatives des exemples respectivement issus de la littérature scientifique (Figure 3), de la plateforme _Kaggle_ et de projets de _data science_ d’envergure publiés sur _GitHub_ (Figure 4).

Il est supposé que le processus de création de _data pipelines_ est souvent ad hoc.

La Figure 3 présente 11 étapes séparées en 3 couches. Les sous-tâches sont énumérées sous chaque étape. Les étapes sont reliées par des boucles de rétroaction indiquées par des flèches. Les flèches pleines sont toujours présentes dans le cycle de vie, tandis que les flèches en pointillé sont facultatives. Des boucles de rétroaction éloignées (par exemple, du déploiement à l'acquisition de données) sont également possibles par le biais d'étapes intermédiaires.

![Figure 3. Architecture de _data pipeline_ de science des données représentative de la littérature scientifique selon [1].](images/concepts-in-a-data-science-pipeline-s-biswas-m-wardat-h-rajan.png)

**Figure 3. Architecture de _data pipeline_ de science des données représentative de la littérature scientifique selon [1].**

#### A.5.2. Architectures spécifiques

##### A.5.2.1. _Machine learning_

![Figure 4. Architecture de _data pipeline_ de science des données représentative des projets d'envergure selon [1].](images/ds-pipeline-in-the-large-s-biswas-m-wardat-h-rajan.png)

**Figure 4. Architecture de _data pipeline_ de science des données représentative des projets d'envergure selon [1].**

[1] qualifie les « projets d'envergure » comme des projets qui tentent de résoudre des problèmes généraux potentiellement liés à plusieurs jeux de données.

L'architecture de la _data pipeline_ (Figure 4) présente deux phases : une phase de développement et une phase post-développement. La phase de développement (en haut en rose) se déroule pendant la construction du modèle et la phase de post-développement (en bas en orange) se déroule pour faire des prédictions. Des précisions sur le fonctionnement complet sont disponibles dans le papier initial.

##### A.5.2.2. ETL (_Extract-Transform-Load_)

Désigne les _data pipelines_ comportant les processus d’extraction de données à partir d’une ou plusieurs sources, de leur transformation dans un format exploitable, puis de leur chargement dans un ou plusieurs environnements de destination (tels qu’une base de données ou directement à l'entrée du traitement ultérieur) afin de permettre leur exploitation [17, 25]. Elles peuvent être une sous-partie d'une _data pipeline_ plus grande, majoritairement au début, mais possiblement ailleurs quand des changements de format sont nécessaires.

Cela correspond partiellement à la partie « _Pre-processing Layer_ » dans le modèle de [1].

##### A.5.2.3. ELT (_Extract-Load-Transform_)

Désigne des _data pipelines_ similaires aux ETL à la différence que les étapes de transformation et de chargement sont inversées, de sorte que les données sont stockées dans un format brut avant transformation, ce qui améliore la vitesse d'extraction [26]. Le terme ingestion de données est souvent utilisé pour caractériser une extraction et un chargement sans transformation préalable.

##### A.5.2.4. Architecture Lambda

L’architecture Lambda est conçue pour traiter à la fois des données historiques (_batch_) et des données en temps réel (_streaming_) au sein d’une même infrastructure reposant sur deux couches parallèles.

###### A.5.2.4.1. Objectifs

L’approche Lambda vise à allier précision (fournie par le traitement _batch_) et rapidité (permise par le traitement en temps réel), afin de proposer des systèmes réactifs tout en maintenant un haut niveau de fiabilité analytique.

###### A.5.2.4.2. Limites

L’architecture Lambda est souvent critiquée pour sa complexité opérationnelle, notamment parce qu’elle exige de maintenir deux chemins de traitement distincts, souvent redondants, ce qui rend le débogage, la maintenance et la synchronisation plus difficiles. Cela a conduit à l’émergence d’approches alternatives comme l’architecture Kappa.

Pour aller plus loin : [14, 18].

##### A.5.2.5. Architecture Kappa

L’architecture Kappa [14, 23] est une évolution de l’architecture Lambda qui cherche à simplifier l’infrastructure. Elle propose de traiter toutes les données sous forme de flux, même les historiques, en rejouant les événements à partir d’un journal d’événements persisté (comme _Apache Kafka_ [44]). Il n’y a donc qu’un seul chemin de traitement (_steam-only_).

###### A.5.2.5.1. Objectifs

Kappa est pensée pour les cas où le temps réel est primordial et où la gestion de deux couches parallèles (comme dans Lambda) est trop coûteuse. Elle permet aussi une plus grande uniformité de code et facilite le retraitement des données simplement en rediffusant le flux initial.

###### A.5.2.5.2. Limites

Kappa suppose que tout peut être modélisé comme un flux d’événements, ce qui n’est pas toujours adapté, notamment pour des traitements analytiques complexes sur de très gros volumes historiques. Le modèle reste donc plus approprié dans des cas d’usage spécifiques, comme la détection d’événements ou les systèmes de recommandation en temps réel.

Pour aller plus loin : [14, 23].

##### A.5.2.6. Architecture médaillon

L'architecture médaillon (Figure 5) pour les _data pipelines_ consiste en une succession d'étapes de stockage et de transformation [19].

![Figure 5. Architecture médaillon d'une _data pipeline_ selon [19].](images/medallion-architecture-databricks.svg)

**Figure 5. Architecture médaillon d'une _data pipeline_ selon [19].**

Dans un premier temps, les données sont ingérées, sans traitement, avec des informations d'historique et de traçabilité. Ensuite, des transformations simples sont appliquées comme le nettoyage, la déduplication, l’harmonisation des formats, la validation des schémas et parfois des jointures pour obtenir une vue consolidée et fiable des données, mais sans logique métier avancée. Enfin, les données des transformations avancées, souvent spécifiques au métier, sont appliquées comme des agrégations, des enrichissements, des calculs complexes, des modélisations dimensionnelles, pour produire des jeux de données directement exploitables.

###### A.5.2.6.1. Objectifs

L'architecture médaillon est apparue dans la littérature grise en réponse à un besoin opérationnel lié aux défis du _Big data_ et suite à une formalisation progressive par la communauté des _data engineers_. L'objectif est d'améliorer successivement la qualité et la structure des données jusqu'à les rendre exploitables pour des traitements finaux qui requièrent une grande qualité comme l'analyse (par exemple, les modèles de _machine learning_), le _reporting_… Cette architecture favorise également la traçabilité, la sécurité et la gouvernance.

###### A.5.2.6.2. Limites

Cette architecture implique la conservation de toutes les données, y compris leurs formes intermédiaires, ce qui peut entraîner une consommation importante d’espace de stockage et poser des enjeux de sécurité.

##### A.5.2.7. Architecture 2-tiers (_data lake_ et _data warehouse_)

L'architecture 2-tiers [27] désigne une _data pipeline_ composée d'une étape d'ingestion de données dans un _data lake_ (Annexe B, section B.2.3.), suivi d'un ETL dont la destination est un _data warehouse_ (Annexe B, section B.2.1.).

###### A.5.2.7.1. Objectifs

L'objectif est de tenter de combiner les avantages du _data lake_ (Annexe B, section B.2.3.) et du _data warehouse_ (Annexe B, section B.2.1.).

###### A.5.2.7.2. Limites

La mise en place d'un tel système est tout autant difficile que sa maintenance, en ajoutant des éléments additionnels d'infrastructure, des coûts associés de mise en place et d'exploitation et de nouveaux défis de sécurité. Cela crée une duplication des données et des difficultés à garder les données actualisées dans le _data warehouse_ (Annexe B, section B.2.1.).

### A.6. Infrastructures

L’écosystème autour de la donnée connaît aujourd’hui un essor sans précédent, porté par l’explosion des volumes de données, la diversification des usages et l’arrivée continue de nouveaux acteurs et de solutions technologiques. Cet univers dynamique regroupe une multitude d’acteurs : fournisseurs de données, plateformes, intégrateurs, spécialistes de la gouvernance, experts en intelligence artificielle, startups innovantes et grandes entreprises, chacun apportant ses propres outils, services et expertises. Face à cette richesse et cette complexité, il serait impossible de détailler ici l’ensemble des solutions existantes, tant le paysage évolue rapidement et s’enrichit chaque année de nouvelles ressources. Pour en donner une idée concrète et illustrer la diversité ainsi que la vitalité de cet écosystème en pleine expansion, M. Turck (FirstMark) propose un aperçu visuel non exhaustif [45].

### A.7. Opérationnalisation

La mise en production des traitements et modèles de _machine learning_ et d'intelligence artificielle, l'opérationnalisation des chaînes de traitement de données, soulèvent des problèmes spécifiques non présents dans le cycle de vie du développement logiciel traditionnel. Le développement de logiciels traditionnels repose sur un ensemble d'exigences bien définies (déterministe), tandis que les solutions de _machine learning_ sont basées sur l'expérimentation avec un ensemble de données, des bibliothèques et des paramètres constamment nouveaux afin d'améliorer la précision du modèle (probabiliste), ce qui rend l'opérationnalisation plus difficile [4].

Cette spécificité a vu la naissance récente de disciplines comme _DataOps_, _MLOps_, _AIOps_ comme adaptation du _DevOps_, avec pour but de réduire le cycle de vie entre le développement et l'opérationnalisation grâce à l'intégration et le déploiement continu, et fondé sur des pratiques, des principes, des environnements, des outils. Parmi ces pratiques, sont étudiés l'implémentation et le développement, la maintenance, le versionnement, le _monitoring_ de l'efficacité et de la fiabilité pour éviter la dégradation dans le temps, le test des modèles…

Pour en savoir plus sur les disciplines et les objectifs, les principes, les pratiques, les outils, les métriques et autres : [4, 6, 7, 22].

### A.8. Implémentation moderne

La gestion moderne des données repose sur une architecture complexe et modulaire, conçue pour répondre aux exigences croissantes de volume, de diversité et de vitesse d’analyse des données. L’approche actuelle se structure autour d’une _data stack_, combinant plusieurs couches logiques, interconnectées par des _data pipelines_ robustes, avec une infrastructure capable de s’adapter tant aux environnements _Cloud_ qu’aux déploiements hybrides ou _on-premise_, souvent orchestrés à l’aide de technologies comme _Kubernetes_ [46].

Au cœur de cette architecture se trouve la séparation logique des responsabilités en plusieurs couches fonctionnelles : ingestion, transformation, stockage, orchestration, gouvernance et consommation. Chacune de ces couches repose sur des composants spécialisés, intégrés de manière cohérente afin d’assurer la fluidité, la fiabilité et la traçabilité des flux de données.

Le processus débute par l’ingestion des données, où des outils comme _Apache Kafka_ [44], _Flink_ [47], ou les connecteurs CDC (_Change Data Capture_) [48] collectent les données depuis diverses sources : bases de données transactionnelles, API, capteurs IoT, fichiers etc. Ces données sont généralement normalisées et transférées en temps réel ou en mode _batch_ vers une zone de transit ou de préparation, souvent appelée _landing zone_.

La transformation des données, souvent gérée dans des _data warehouses_ (Annexe B, section B.2.1.) comme _Snowflake_ [49] et _BigQuery_ [50], _data lakes_ (Annexe B, section B.2.3.) comme _Amazon S3_ [51] et _Apache Hudi_ [52], ou des _data lakehouses_ (Annexe B, section B.2.4.) modernes comme _Delta Lake_ [53] ou _Apache Iceberg_[54] ; implique un traitement qui peut être réalisé par des moteurs comme _Apache Spark_ [55], _dbt_ [56] ou _Trino_ [57]. Ces processus visent à nettoyer, enrichir et modéliser les données selon des logiques métiers précises, tout en respectant les principes d’architecture en couches telles que le modèle médallion (bronze, argent, or).

Les _data pipelines_, qu’elles soient _batch_ ou en flux continu, sont orchestrées par des outils comme _Apache Airflow_ [29], _Prefect_ [58] ou _Dagster_ [59]. Cette couche permet de définir les dépendances, l'ordre, la surveillance et la reprise automatique des tâches. Dans un contexte distribué ou hybride, cette orchestration peut être encapsulée dans des conteneurs _Docker_ [60], exécutés et gérés par des plateformes comme _Kubernetes_ [46], permettant ainsi une scalabilité horizontale et une tolérance aux pannes accrue.

L’infrastructure sous-jacente repose de plus en plus sur une logique déclarative et une automatisation complète du cycle de vie des composants. _Kubernetes_ joue ici un rôle central : il offre une couche d’abstraction sur les ressources matérielles et permet le déploiement cohérent de microservices de données, tout en assurant l’équilibrage de charge, la reprise après incident et la mise à l’échelle automatique. L’infrastructure est souvent définie par le biais d’approches _Infrastructure as Code_ (IaC), utilisant des outils comme _Terraform_ [61], _Helm_ [62] ou _Ansible_ [63].

Un pilier essentiel de la _data stack_ moderne est la gouvernance. Cela comprend le catalogage des données (avec des outils comme _DataHub_ [64], _Amundsen_ [65] ou _Collibra_ [66]), la traçabilité (_data lineage_), la gestion des métadonnées, la conformité aux réglementations (RGPD, HIPAA), ainsi que le contrôle d'accès et le chiffrement. La qualité des données est quant à elle monitorée par des _frameworks_ tels que _Great Expectations_ [67] ou _Soda_ [68], intégrés directement dans les _data pipelines_.

Enfin, la couche de consommation met à disposition les données transformées pour divers usages analytiques, opérationnels ou de science des données. Les utilisateurs finaux peuvent y accéder via des outils de _Business Intelligence_ (_Tableau_ [69], _Power BI_ [70]), des API REST ou _GraphQL_ [71] exposées par des _data services_, ou encore via des _notebooks Jupyter_ [72] pour les analystes et les _data scientists_.

### A.9. Meilleures pratiques

#### A.9.1. Architecture générale

Pour garantir l'évolutivité, la flexibilité et la robustesse de la _data pipeline_, il faut s'assurer de la concevoir de manière modulaire, de séparer, d'isoler clairement les responsabilités au sein des étapes, de minimiser les redondances. Une attention particulière doit être portée sur l'interface entre les étapes. Cette modularité apporte de nombreux avantages, notamment en lien avec le contrôle de la dette technique [3, 5] : facilitation de la maintenance, de l'entretien et du dépannage, meilleure testabilité, meilleure sécurisabilité, meilleure reproductibilité, meilleure réutilisabilité [1], facilitation de la documentation… L'utilisation du métamodèle peut simplifier la phase de conception.

Il est important de maximiser l'automatisation des étapes et tâches pour minimiser l'erreur humaine, la charge de travail et les coûts liés au volume, la vélocité et la variété des données. Cela contribue à recentrer l'effort sur des missions à plus forte valeur ajoutée que celles de gestion de données. Une stratégie peut être de commencer par l'automatisation des étapes, voyant les multiplications du nombre et de la fréquence d'interventions humaines.

La _data pipeline_ doit maximiser sa tolérance aux défaillances avec son recouvrement, qu'elles soient matérielles, algorithmiques ou liées à la qualité des données et aux erreurs métier. À cet effet, il est conseillé de mettre en place un _monitoring_ (latence, vitesse de transfert, taux d'erreur ; _logs_ d'erreurs, rapports, tableaux de bord…), une détection de défaillance, des règles de validation et de réjection entière ou partielle, des stratégies d'atténuation [2], la levée d'alerte et d'événements automatiques. Un exemple de cette pratique est la mise en place d'un espace de _staging_ avant validation et stockage des données pour permettre de revenir en arrière en cas d'erreur et de ne pas altérer la qualité de la destination pendant l'opération. Un autre exemple peut être le versionnement (données, _data pipeline_…).

Si les ressources pour le stockage sont suffisantes, il est conseillé de stocker les données initiales et les données intermédiaires issues de transformation en plus des données finales pour permettre la reproductibilité, la traçabilité et laisser la possibilité d'appliquer des traitements différents à l'avenir, par exemple, pour comparer les performances. Cela évite également de refaire l'intégralité des traitements en cas d'erreur ou de problème de qualité intermédiaire. Il est également conseillé de séparer les stockages initiaux, intermédiaires et finaux, et de minimiser la duplication qui amène à des complexités de gestion et une incertitude sur l'état des données comme l'actualisation.

L'acquisition de données pouvant être une étape critique de la _data pipeline_, il est conseillé de faire du _data profiling_ pour choisir précisément les données à intégrer, par exemple, les données les plus récentes par rapport à celles déjà intégrées (intégration incrémentale). L'intégration de toutes données peut amener à l'explosion des temps d'exécution, de l'espace de stockage pour des données qui sont potentiellement inutilisables, inutiles. Il est aussi préconisé de faire un suivi rigoureux de l'origine des données pour garantir la traçabilité.

Il est préférable de choisir une architecture qui privilégie une compatibilité et une évolution automatique du schéma pour être flexible à la variété des données (formats, structures…).

#### A.9.2. Infrastructure

Une bonne pratique est de choisir des outils pérennes dans le temps, faciles d'utilisation, faciles à l'intégration, offrant un bon _monitoring_, sécurisés et scalables.

### A.10. Compétences requises

La construction d'une chaîne de traitement de données complète nécessite des compétences dans les disciplines à l'intersection des mathématiques, de l'informatique et du domaine métier, telles que l'infrastructure, la _data science_, l'ingénierie de données, le _machine learning_, l'algorithmie, l'opérationnalisation, la visualisation de données…

---

## Annexe B. Types de traitement de données, systèmes de stockage et de gestion de données

- B.1. Types de traitement de données
  - B.1.1. Traitement transactionnel en ligne (OLTP, _Online Transaction Processing_)
  - B.1.2. Traitement analytique en ligne (OLAP, _Online Analytical Processing_)
  - B.1.3. Autres
- B.2. Systèmes de stockage et de gestion de données
  - B.2.1. _Data warehouse_
    - B.2.1.1. Objectifs
    - B.2.1.2. Limites
  - B.2.2. _Data mart_
    - B.2.2.1. Objectifs
    - B.2.2.2. Limites
    - B.2.2.3. Meilleures pratiques
  - B.2.3. _Data lake_
    - B.2.3.1. Objectifs
    - B.2.3.2. Limites
    - B.2.3.3. Meilleures pratiques
  - B.2.4. _Data lakehouse_
    - B.2.4.1. Objectifs
    - B.2.4.2. Limites
  - B.2.5. Autres

---

Historiquement, les systèmes de gestion de bases de données relationnelles (SGBDR) ont constitué le socle des systèmes d’information. Ils offrent des garanties élevées en matière de qualité, d’intégrité référentielle et de cohérence transactionnelle (propriétés ACID [21]). Toutefois, ils présentent des inconvénients face à la diversité et au volume massif des données caractéristiques du _Big data_, comme la difficulté à évoluer horizontalement et le manque de flexibilité, notamment à cause de leur schéma rigide qui nécessite de structurer et de normaliser l’ensemble des données avant leur intégration.

Si ces approches demeurent pertinentes pour les besoins transactionnels classiques, l’essor du _Big data_ a favorisé l’émergence de nouveaux types de traitement de données et de nouveaux systèmes de stockage et de gestion de données mieux adaptés.

Le type de traitement souhaité, qu’il s’agisse de la rapidité d’exécution des transactions (OLTP), de l’agrégation analytique massive (OLAP) ou de la capture en flux continu (OLEP), conditionne fortement le choix du système de stockage et de gestion de données et, inversement, les capacités d’un tel système influencent les types de traitement envisageables.

La présente annexe décrit les principaux types de traitement de données et les systèmes de stockage et de gestion de données.

### B.1. Types de traitement de données

#### B.1.1. Traitement transactionnel en ligne (OLTP, _Online Transaction Processing_)

Type de traitement orienté vers la gestion de transactions (changement atomique d'état) en temps réel, caractérisé par des temps de réponse très courts, une forte concurrence (nombreuses transactions simultanées), une grande disponibilité et fiabilité, et des requêtes généralement simples (insertion, mise à jour, suppression) [8].

#### B.1.2. Traitement analytique en ligne (OLAP, _Online Analytical Processing_)

Type de traitement conçu pour l’analyse multidimensionnelle de grandes quantités de données, permettant d’exécuter rapidement des requêtes complexes, souvent agrégatives [9].

#### B.1.3. Autres

Il en existe d'autres comme le traitement événementiel en ligne (OLEP, _Online Event Processing_) pour la gestion des _logs_ d'événements distribués.

### B.2. Systèmes de stockage et de gestion de données

#### B.2.1. _Data warehouse_

Le _data warehouse_ [10, 16] est dédié à l’analyse, en lecture seule pour les utilisateurs, qui agrège et historise des données issues de multiples sources. Les données y sont intégrées régulièrement, nettoyées, transformées et organisées de manière à optimiser les analyses décisionnelles et le _reporting_.

Le _data warehouse_ intègre également des métadonnées (notamment sur la récence des données) et des processus de gouvernance et de qualité.

##### B.2.1.1. Objectifs

Il permet de décharger les bases de données opérationnelles, optimisées pour la rapidité et l’intégrité transactionnelle (parfois au détriment de l’historique), et ayant une structure inefficace pour des requêtes d'analyse, vers des bases de données dédiées et optimisées à cet effet. Cela contribue à réduire les problèmes de verrouillage liés aux requêtes analytiques lourdes et permet d'avoir une « _single source of truth_ » car il centralise généralement toutes les données de manière organisée.

##### B.2.1.2. Limites

La normalisation et la transformation des données peuvent être longues, coûteuses et difficiles à concevoir, surtout si la valeur ajoutée n’est pas clairement identifiée. De plus, la rigidité du schéma peut freiner l’innovation et l’adaptation rapide à de nouveaux besoins.

#### B.2.2. _Data mart_

Le _data mart_ [11, 20] est un sous-ensemble spécialisé du _data warehouse_, centré sur un domaine métier ou une thématique précise (là où le _data warehouse_ centralise généralement toutes les données). Il peut être alimenté directement à partir des sources (l'ensemble des _data marts_ forment le _data warehouse_), via un _data warehouse_ central ou une combinaison des deux.

##### B.2.2.1. Objectifs

Les _data marts_ facilitent la performance analytique pour des besoins ciblés et favorisent l’isolation des usages, tout en maintenant une « _single source of truth_ » si une gouvernance claire est définie. Leur construction est plus facile qu'un _data warehouse_ complet et nécessite moins d'espace de stockage.

##### B.2.2.2. Limites

Les _data marts_, s’ils ne sont pas accompagnés d’une gouvernance rigoureuse, présentent le risque de créer des silos de données : chaque domaine métier peut alors développer ses propres référentiels et définitions, ce qui complique la collaboration, limite la visibilité globale et favorise la duplication ou l’incohérence des informations.

##### B.2.2.3. Meilleures pratiques

Comme pour les _data lakes_, il est essentiel de définir une politique globale claire, des règles de gouvernance de création et de gestion des _data marts_ pour éviter la duplication inutile de données, un état incohérent des données, un _data mart_ non aligné sur un besoin métier.

#### B.2.3. _Data lake_

Le _data lake_ [12] permet d’ingérer tout type de données (structurées, semi-structurées, non structurées) dans leur format natif, sans schéma défini à l'avance (« _schema-on-read_ »).

L'intégration se fait sans aucune préparation des données donc sans aucune vérification de la qualité (propreté, fiabilité, complétude, utilité…).

##### B.2.3.1. Objectifs

Il répond en partie à la problématique des silos de données en favorisant un stockage rapide, peu coûteux et évolutif, adapté aux volumes massifs et à la diversité des formats, sans tenir compte des futurs usages, ce qui contribue à la réduction du temps et des coûts d’intégration. Il a également pour objectif l'historisation et la facilitation des mises à jour et du partage des données.

##### B.2.3.2. Limites

En l’absence de gestion et de gouvernance adéquates, il existe un risque important de désorganisation, menant à la formation d’un « _data swamp_ » où les données deviennent difficilement exploitables.

De plus, la maintenance et l’administration d’un _data lake_ s’avèrent souvent délicates, notamment en raison de la diversité et du volume des données stockées. Les enjeux de gouvernance sont également accrus, avec des difficultés à assurer la traçabilité, la sécurité et la conformité réglementaire (par exemple, vis-à-vis du RGPD). Enfin, l’absence de préparation des données initiales peut générer des temps de préparation supplémentaires dans les traitements utilisant les _data lakes_.

##### B.2.3.3. Meilleures pratiques

Pour éviter qu'un _data lake_ ne devienne un _data swamp_, il est nécessaire de mettre en place des règles de gouvernance claires et rigoureuses, incluant la gestion des métadonnées, la qualité des données, la sécurité, ainsi que des processus de catalogage et de traçabilité permettant de garantir l'accessibilité, la compréhension et la fiabilité, la conformité des données stockées.

#### B.2.4. _Data lakehouse_

Le _data lakehouse_ [12, 13] est une avancée du _data lake_ et du _data warehouse_ combinée au sein d'un seul système. À la manière d'un _data lake_, il permet le stockage rapide, peu coûteux et évolutif de tout type de données dans leur format natif (en réalité, le format est semi-natif ouvert), sans schéma défini à l'avance. Une couche de métadonnées est superposée, ce qui permet le support des transactions ACID, des schémas et le versionnement de données à la manière d'un _data warehouse_. Les ressources de calcul pour le requêtage et l'analyse sont séparées et optimisées, et peuvent être mises à l'échelle indépendamment, ce qui permet d'optimiser les performances et les coûts. Les fonctionnalités de gestion de données et les outils offrent des fonctions de gouvernance (contrôle d'accès, traçabilité, auditabilité) pour garantir la qualité et la conformité des données.

##### B.2.4.1. Objectifs

Réduire les inconvénients des _data lakes_, des _data warehouses_ et de l'architecture 2-tiers (Annexe A, section A.5.2.7.).

##### B.2.4.2. Limites

La performance d'un _data lakehouse_ reste légèrement inférieure à celle d'un _data warehouse_ et son intégration à des systèmes existants est complexe au même titre que la gouvernance.

#### B.2.5. Autres

Il existe d'autres concepts comme _Data mesh_ [78] ou _Data fabric_ [79].
