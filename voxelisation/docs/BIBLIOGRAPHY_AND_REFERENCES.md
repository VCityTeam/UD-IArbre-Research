# Bibliography and References - IA.rbre Voxelizer

*The single collection of every source used on this project: 118 entries
in all. The internship report draws its citations from this list, and its
own bibliography is the subset actually cited (46 of them, numbered here as
in the report's bibliography). Those 46 numbers follow the order of first
citation in the report's reading order.
Assembled from the report's bibliography and the project's 86-entry master
source list, which this file reproduces in full.*

**Terminology used here:** **References** are sources actually cited in the
internship report; **Bibliography** is everything consulted during the work
but not cited. The distinction and this file's two-part structure follow
that convention.

This file is the reference of record, and one of three companion documents
behind the report - with `DESIGN_DECISIONS.md` (why each design choice was
made, alternatives and costs) and `ProblemSolving.md` (every problem
hit and how it was solved). Every entry carries where it came
from and, for cited entries, which report sections use it, so a claim in the
report can be traced back to its source and back again. The 86-entry master
source list is reproduced here in full: 26 of its entries carry a report
citation and appear in Part 1 as `Origin: master [n]` tags, the remaining
uncited masters are listed in Part 2.1 under their original numbers, and
master [43] appears in both. Sources were added to the master list first,
then cited in the report.

---

## Part 1 - References (cited in the internship report)

Numbering is the report's own. "Used in" gives the report sections where the
citation appears; "Master" links back to the 86-entry list where the entry
originated. Twenty entries carry no master number because none of them is in
that list: five added during report curation ([6], [19], [25], [27] and [28]),
the eleven solar position and canopy extinction sources behind Section 4.4
([35] to [45]), [46], added with the encoder-validation metric, [29],
added when the sunlight-computation discussion of Section 3.3 was
fact-checked, [8], the LAS 1.2 specification the Section 2.2 and Appendix E
input claims come from, and [24], the segmentation code behind the LiDAR HD
classes, both added while fact-checking those claims at their sources. Two
further entries were promoted from the master list at the same time:
[17] from master [14] and [1] from master [54]. A third, [30] from master
[55], was promoted when Section 3.3 gained the 2-D and 2.5-D exemplar.

[1] VCity Team, "pySunlight: Light pre-calculation based on real data (urban
    data and sun position) with 3D Tiles," GitHub, 2023. [Online]. Available:
    https://github.com/VCityTeam/pySunlight. [Accessed: Aug. 2026].
    - Used in: 1.2; 3.3, where the sentence now separates the two VCity
      tools: py3DTilers assembles the tilesets and pySunlight computes
      sunlight over them, reading them through py3DTilers and delegating the
      computation to the Sunlight C++ library. Origin: master [54], whose
      entry names the tool by its older UD-SV-Sunlight repository.

[2] TelesCoop and IA.rbre Consortium, "IA.rbre: Intelligence artificielle pour l'arbre - Plateforme web Big Data d'aide a la decision pour la vegetalisation urbaine," France 2030 project, 2025. [Online]. Available: https://iarbre.fr. [Accessed: Jul. 2026].
    - Used in: 1.3. Origin: master [85].

[3] Banque des Territoires, "Demonstrateurs d'IA frugale au service de la transition ecologique des territoires (DIAT) - Appel a projets France 2030," Caisse des Depots et Consignations, Paris, France, 2023. [Online]. Available: https://www.banquedesterritoires.fr. [Accessed: Jul. 2026].
    - Used in: 1.3. Origin: master [84].

[4] Institut national de l'information geographique et forestiere (IGN), "LiDAR HD: Vers une nouvelle cartographie 3D du territoire," IGN, Saint-Mande, France, 2023. [Online]. Available: https://geoservices.ign.fr/lidarhd. [Accessed: Jul. 2026].
    - Used in: 2.2; 3.2. Origin: master [47].

[5] Metropole de Lyon, "Data Grand Lyon - Open data portal," 2023. [Online]. Available: https://data.grandlyon.com. [Accessed: Jul. 2026].
    - Used in: 2.2. Origin: master [50].

[6] M. Isenburg, "LASzip: Lossless compression of lidar data," Photogramm. Eng. Remote Sens., vol. 79, no. 2, pp. 209-217, 2013.
    - Used in: 2.2; 4.3.1; Annexe B. Origin: added for the report (LASzip format).

[7] American Society for Photogrammetry and Remote Sensing (ASPRS), LAS Specification 1.4-R15, ASPRS, Baton Rouge, LA, USA, 2019.
    - Used in: 2.2; Annexe E. Origin: master [46].

[8] American Society for Photogrammetry and Remote Sensing (ASPRS), LAS
    Specification Version 1.2, approved by the ASPRS Board 2 Sep. 2008.
    [Online]. Available:
    https://www.asprs.org/wp-content/uploads/2010/12/asprs_las_format_v12.pdf.
    [Accessed: Aug. 2026].
    - Used in: Annexe E, for point data record format 1 (the base record plus GPS
      time), for the five-bit classification field with maximum value 31, and
      for class 8 as Model Key-point in the class table governing point
      formats 0 to 5; Appendix E, for the same five-bit field. [7] keeps the
      statements that are genuinely about LAS 1.4: the numbering of point data
      record formats 0 to 10, and the extended class table for formats 6 to 10
      in which 8 becomes Reserved and 64 to 255 are user-definable.
      Origin: added for the report (the delivered tiles are LAS 1.2, and the
      1.4-R15 specification cannot carry claims about them).

[9] Open Geospatial Consortium, 3D Tiles Specification 1.1, OGC Document 22-025r4, 2023. [Online]. Available: https://docs.ogc.org/cs/22-025r4/22-025r4.html. [Accessed: Jul. 2026].
    - Used in: 2.3; 3.3; 4.3.4; Annexe B. Origin: supersedes master [43] (3D Tiles 1.0).

[10] M. Aleksandrov, S. Zlatanova, and D. J. Heslop, "Voxelisation algorithms and data structures: A review," Sensors, vol. 21, no. 24, p. 8241, Dec. 2021, doi: 10.3390/s21248241.
    - Used in: 3 (chapter opening); Table 2. Origin: master [1].

[11] M. Wang and Y.-H. Tseng, "Incremental segmentation of lidar point clouds with an octree-structured voxel space," The Photogrammetric Record, vol. 26, no. 133, pp. 32-57, Mar. 2011, doi: 10.1111/j.1477-9730.2011.00624.x.
    - Used in: 3.1; Table 2. Origin: master [2].

[12] B. Gorte, S. Zlatanova, M. Pilouk, A. Diakite, and J. Barton, "3D data integration in the voxel domain," ISPRS Ann. Photogramm. Remote Sens. Spatial Inf. Sci., vol. X-4-2024, pp. 133-140, Oct. 2024, doi: 10.5194/isprs-annals-X-4-2024-133-2024.
    - Used in: 3.1; Table 2. Origin: master [5].

[13] B. Gorte, "Analysis of very large voxel data sets," Int. J. Appl. Earth Obs. Geoinf., vol. 119, p. 103316, May 2023, doi: 10.1016/j.jag.2023.103316.
    - Used in: 3.1; Table 2. Origin: master [11].

[14] V. Kampe, E. Sintorn, and U. Assarsson, "High resolution sparse voxel DAGs," ACM Trans. Graph., vol. 32, no. 4, p. 101, Jul. 2013, doi: 10.1145/2461912.2462024.
    - Used in: 3.1; Table 2. Origin: master [8].

[15] K. Museth, "VDB: High-resolution sparse volumes with dynamic topology," ACM Trans. Graph., vol. 32, no. 3, pp. 1-22, Jun. 2013, doi: 10.1145/2487228.2487235.
    - Used in: 3.1; Table 2. Origin: master [6].

[16] K. Museth, "NanoVDB: A GPU-friendly and portable VDB data structure for real-time rendering and simulation," in ACM SIGGRAPH 2021 Talks, New York, NY, USA, 2021, pp. 1-2, doi: 10.1145/3450623.3464653.
    - Used in: 3.1; Table 2. Origin: master [7].

[17] J. C. Hart, "Sphere tracing: A geometric method for the antialiased ray
    tracing of implicit surfaces," Vis. Comput., vol. 12, no. 10, pp. 527-545,
    1996, doi: 10.1007/s003710050084.
    - Used in: 3.1; Table 2, for the distance field as an implicit
      representation whose distance-to-surface query is available in closed
      form for parametric primitives. [18] keeps the sampled-data clause,
      which is what an exact unsigned Euclidean distance transform supports.
      Origin: master [14]. The master entry records
      doi 10.1007/BF02121702; the DOI above is the one the article and dblp
      give.

[18] T. Saito and J.-I. Toriwaki, "New algorithms for euclidean distance transformation of an n-dimensional digitized picture with applications," Pattern Recognit., vol. 27, no. 11, pp. 1551-1565, Nov. 1994, doi: 10.1016/0031-3203(94)90133-3.
    - Used in: 3.1; Table 2. Origin: master [27].

[19] D. E. O. Tzamarias, K. Chow, I. Blanes, and J. Serra-Sagrista, "Fast run-length compression of point cloud geometry," IEEE Trans. Image Process., vol. 31, pp. 4490-4501, 2022, doi: 10.1109/TIP.2022.3185541.
    - Used in: 3.1; Table 2. Origin: added for the report (closest published relative of the encoding).

[20] S. W. Golomb, "Run-length encodings," IEEE Trans. Inf. Theory, vol. 12, no. 3, pp. 399-401, Jul. 1966, doi: 10.1109/TIT.1966.1053907.
    - Used in: 3.1. Origin: master [25].

[21] D. Abadi, P. Boncz, S. Harizopoulos, S. Idreos, and S. Madden, "The design and implementation of modern column-oriented database systems," Found. Trends Databases, vol. 5, no. 3, pp. 197-280, 2013, doi: 10.1561/1900000024.
    - Used in: 3.1; 4.2.1. Origin: master [24].

[22] H. Aljumaily, D. F. Laefer, D. Cuadra, and M. Velasco, "Point cloud voxel classification of aerial urban LiDAR using voxel attributes and random forest approach," Int. J. Appl. Earth Obs. Geoinf., vol. 118, p. 103208, Apr. 2023, doi: 10.1016/j.jag.2023.103208.
    - Used in: 3.2; Table 2. Origin: master [3].

[23] F. Poux and R. Billen, "Voxel-based 3D point cloud semantic segmentation: Unsupervised geometric and relationship featuring vs deep learning methods," ISPRS Int. J. Geo-Inf., vol. 8, no. 5, p. 213, May 2019, doi: 10.3390/ijgi8050213.
    - Used in: 3.2; Table 2. Origin: master [4].

[24] C. Gaydon, "Myria3D: Deep learning for the semantic segmentation of
    aerial lidar point clouds," GitHub, Institut national de l'information
    geographique et forestiere (IGN), 2022. [Online]. Available:
    https://github.com/IGNF/myria3d. [Accessed: Aug. 2026]. The segmentation
    model is a PyTorch Geometric implementation of RandLA-Net (Q. Hu et al.,
    CVPR 2020).
    - Used in: 3.2; Table 2, as the software behind the classification the
      input already carries. Origin: added for the report (previously listed in
      Part 2.2 as consulted software, promoted when cited).

[25] C. Gaydon, M. Daab, and F. Roche, "FRACTAL: An ultra-large-scale aerial lidar dataset for 3D semantic segmentation of diverse landscapes," 2024, arXiv:2405.04634.
    - Used in: 3.2; Table 2. Origin: added for the report (provenance of the LiDAR HD classes).

[26] M. Kress, "LiVoxGen: LiDAR voxelization and generation of canopy metrics," GitHub, Michigan State University, 2015. [Online]. Available: https://github.com/MeganKress/LiVoxGen. [Accessed: Jul. 2026].
    - Used in: 3.3; Table 2. Origin: master [86].

[27] A. Peytavie, E. Galin, J. Grosjean, and S. Merillou, "Arches: A framework for modeling complex terrains," Comput. Graph. Forum, vol. 28, no. 2, pp. 457-467, 2009, doi: 10.1111/j.1467-8659.2009.01385.x.
    - Used in: 3.3; Table 2; 6.2. Origin: added for the report (lab-heritage SBRT ancestor).

[28] K. Fujiwara, R. Tsurumi, T. Kiyono, Z. Fan, X. Liang, B. Lei, W. Yap, K. Ito, and F. Biljecki, "VoxCity: A seamless framework for open geospatial data integration, grid-based semantic 3D city model generation, and urban environment simulation," Comput. Environ. Urban Syst., vol. 123, Art. 102366, 2026, doi: 10.1016/j.compenvurbsys.2025.102366 (arXiv:2504.13934; online 2025; volume year 2026).
    - Used in: 3.3; Table 2. Origin: added for the report (2026 grid-based semantic city modelling).

[29] V. Jaillot, F. Pedrinis, S. Servigne, and G. Gesquiere, "A generic
    approach for sunlight and shadow impact computation on large city
    models," in Proc. 25th Int. Conf. in Central Europe on Computer Graphics,
    Visualization and Computer Vision (WSCG 2017), Plzen, Czech Republic,
    2017. [Online]. Available: https://hal.science/hal-01559175. [Accessed:
    Aug. 2026].
    - Used in: 3.3, for sunlight and shadow computation on triangulated
      city models within the VCity ecosystem. Origin: added for the report.

[30] Institut national de l'information geographique et forestiere (IGN), "FLAIR-HUB: Multi-source semantic segmentation model for land cover mapping," GitHub, 2023. [Online]. Available: https://github.com/IGNF/FLAIR-HUB. [Accessed: Aug. 2026].
    - Used in: 3.3, for the land-cover segmentation the sibling vegetalisation module fuses into its rasters. Origin: master [55].

[31] C. R. Harris et al., "Array programming with NumPy," Nature, vol. 585, no. 7825, pp. 357-362, Sep. 2020, doi: 10.1038/s41586-020-2649-2.
    - Used in: 4.3.1; Annexe B. Origin: master [56].

[32] laspy Development Team, laspy: A Python library for reading, modifying, and writing LAS files, 2023. [Online]. Available: https://github.com/laspy/laspy. [Accessed: Jul. 2026].
    - Used in: 4.3.1. Origin: master [61].

[33] G. M. Morton, "A computer oriented geodetic data base and a new technique in file sequencing," IBM Ltd., Ottawa, ON, Canada, Tech. Rep., 1966.
    - Used in: Annexe B. Origin: master [22] (the Z-order leaf walk of the LOD exporter).

[34] J. Amanatides and A. Woo, "A fast voxel traversal algorithm for ray tracing," in Proc. Eurographics, Amsterdam, Netherlands, 1987, vol. 87, no. 3, pp. 3-10.
    - Used in: 4.3.5. Origin: master [13].

[35] A. Beer, "Bestimmung der Absorption des rothen Lichts in farbigen
    Fluessigkeiten," *Annalen der Physik und Chemie*, vol. 86, pp. 78-88,
    1852.
    - Used in: 4.4.3; Annexe A. The original absorption law whose exponential form the
      canopy transmittance model applies along each ray segment.
      Origin: added for the report.

[36] M. Monsi and T. Saeki, "Ueber den Lichtfaktor in den
    Pflanzengesellschaften und seine Bedeutung fuer die Stoffproduktion,"
    *Japanese Journal of Botany*, vol. 14, pp. 22-52, 1953. English
    translation: *Annals of Botany*, vol. 95, no. 3, pp. 549-567, 2005,
    doi: 10.1093/aob/mci052.
    - Used in: 4.4.3; Annexe A. The foundational application of Beer-Lambert extinction
      to a plant canopy, and the origin of the extinction-coefficient form
      used here.
      Origin: added for the report.

[37] G. S. Campbell and J. M. Norman, *An Introduction to Environmental
    Biophysics*, 2nd ed. New York, NY, USA: Springer, 1998,
    doi: 10.1007/978-1-4612-1626-1.
    - Used in: 4.4.3; Annexe A; Annexe C, for the canopy radiation-transfer treatment and the
      dependence of the extinction coefficient on leaf angle distribution.
      Origin: added for the report.

[38] W. Verhoef, "Light scattering by leaf layers with application to canopy
    reflectance modelling: the SAIL model," *Remote Sensing of Environment*,
    vol. 16, no. 2, pp. 125-141, 1984, doi: 10.1016/0034-4257(84)90057-9.
    - Used in: 4.4.3; Annexe A, as a REJECTED alternative: a full four-stream radiative
      transfer model requires leaf angle distribution and optical properties
      that LiDAR geometry does not supply.
      Origin: added for the report.

[39] X. Li and A. H. Strahler, "Geometric-optical modeling of a conifer forest
    canopy," *IEEE Trans. Geosci. Remote Sens.*, vol. GE-23, no. 5,
    pp. 705-721, 1985, doi: 10.1109/TGRS.1985.289389.
    - Used in: 4.4.3; Annexe A, as a REJECTED alternative: geometric-optical models need
      explicit crown geometry, which the voxel store deliberately does not
      retain.
      Origin: added for the report.

[40] T. Nilson, "A theoretical analysis of the frequency of gaps in plant
    stands," *Agricultural Meteorology*, vol. 8, pp. 25-38, 1971,
    doi: 10.1016/0002-1571(71)90092-6.
    - Used in: Annexe A, for the gap-fraction basis of estimating extinction from
      observed transmission, which is what the extinction derivation of report
      Section 4.4.5 does with LiDAR ground returns.
      Origin: added for the report.

[41] National Oceanic and Atmospheric Administration, Global Monitoring
    Laboratory, "NOAA Solar Calculator," 2024. [Online]. Available:
    https://gml.noaa.gov/grad/solcalc/. [Accessed: Jul. 2026].
    - Used in: 4.4.4. The published equation set implemented in
      `voxelizer/solar.py`, accurate to about 0.01 degrees near J2000.
      Origin: added for the report.

[42] J. Meeus, *Astronomical Algorithms*, 2nd ed. Richmond, VA, USA: Willmann-Bell, 1998.
    - Used in: 4.4.4 (solar position). The low-precision solar formulae of
      chapters 25 and 28 are what the NOAA calculator implements.
      Origin: added for the report.

[43] I. Reda and A. Andreas, "Solar position algorithm for solar radiation
    applications," *Solar Energy*, vol. 76, no. 5, pp. 577-589, 2004,
    doi: 10.1016/j.solener.2003.12.003.
    - Used in: 4.4.4, as the REJECTED higher-precision alternative
      (+/-0.0003 deg). Rejected on grounds of precision far below the model's
      own discretisation, not on grounds of quality.
      Origin: added for the report.

[44] M. Blanco-Muriel, D. C. Alarcon-Padilla, T. Lopez-Moratalla and
    M. Lara-Coira, "Computing the solar vector," *Solar Energy*, vol. 70,
    no. 5, pp. 431-441, 2001, doi: 10.1016/S0038-092X(00)00156-0.
    - Used in: 4.4.4, as a REJECTED alternative (PSA algorithm): comparable
      accuracy to NOAA but fitted for 1999-2015.
      Origin: added for the report.

[45] J. J. Michalsky, "The Astronomical Almanac's algorithm for approximate
    solar position (1950-2050)," *Solar Energy*, vol. 40, no. 3, pp. 227-235,
    1988, doi: 10.1016/0038-092X(88)90045-X.
    - Used in: 4.4.4, as a REJECTED alternative: simpler, but a bounded
      validity window and no refraction term.
      Origin: added for the report.

[46] P. Jaccard, "Etude comparative de la distribution florale dans une portion
    des Alpes et du Jura," *Bulletin de la Societe Vaudoise des Sciences
    Naturelles*, vol. 37, pp. 547-579, 1901. English statement of the same
    coefficient: "The distribution of the flora in the alpine zone," *New
    Phytologist*, vol. 11, no. 2, pp. 37-50, 1912,
    doi: 10.1111/j.1469-8137.1912.tb05611.x.
    - Used in: 5.2 and the Glossary, for the intersection-over-union
      coefficient that measures how much of the raw per-class occupancy each
      encoder variant preserves. Origin: added for the report.

Note on [28]: the article number and DOI were verified online during report curation;
the DOI ...102283 recorded earlier belongs to a different
paper (ZenSVI).

---

## Part 2 - Bibliography (consulted, not cited in the report)

### 2.1 From the 86-entry master list

The 61 master entries below were read/collected during the design phase but
did not enter the report's citation set. Original master numbering kept.
Category numbering is the master list's own, preserved rather than
renumbered.

#### II. Voxel Data Structures and Algorithms

[9] P. Nourian, R. Gonzales, S. Zlatanova, K. Arroyo Ohori, and A. V. Vo, "Voxelization algorithms for geospatial applications," *MethodsX*, vol. 3, pp. 69-86, 2016, doi: 10.1016/j.mex.2016.01.001.

[10] B. Gorte and S. Zlatanova, "Rasterization and voxelization of 2-D and 3-D space partitioning," *Int. Arch. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. XLI-B4, pp. 283-288, 2016, doi: 10.5194/isprsarchives-XLI-B4-283-2016.

[12] B. Gorte, S. Zlatanova, and F. Fadli, "Navigation in indoor voxel models," *ISPRS Ann. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. IV-2/W5, pp. 279-283, 2019, doi: 10.5194/isprs-annals-IV-2-W5-279-2019.

#### III. Ray Tracing, Shadow Computation, and Rendering

[15] I. Quilez, "Penumbra shadows in ray marching," *iquilezles.org*, 2010. [Online]. Available: https://iquilezles.org/articles/rmshadows/. [Accessed: Jun. 2026].

[16] J. T. Kajiya, "The rendering equation," *ACM SIGGRAPH Comput. Graph.*, vol. 20, no. 4, pp. 143-150, Aug. 1986, doi: 10.1145/15886.15902.

#### IV. Surface Reconstruction and Mesh Generation

[17] W. E. Lorensen and H. E. Cline, "Marching cubes: A high resolution 3D surface construction algorithm," *ACM SIGGRAPH Comput. Graph.*, vol. 21, no. 4, pp. 163-169, Jul. 1987, doi: 10.1145/37402.37422.

[18] H. Edelsbrunner and E. P. Muecke, "Three-dimensional alpha shapes," *ACM Trans. Graph.*, vol. 13, no. 1, pp. 43-72, Jan. 1994, doi: 10.1145/174462.156635.

#### V. Machine Learning: Classification and Feature Engineering

[19] L. Breiman, "Random forests," *Mach. Learn.*, vol. 45, no. 1, pp. 5-32, Oct. 2001, doi: 10.1023/A:1010933404324.

[20] M. Ester, H. P. Kriegel, J. Sander, and X. Xu, "A density-based algorithm for discovering clusters in large spatial databases with noise," in *Proc. 2nd Int. Conf. Knowledge Discovery and Data Mining (KDD-96)*, Portland, OR, USA, 1996, pp. 226-231.

[21] K. Pearson, "On lines and planes of closest fit to systems of points in space," *London Edinburgh Dublin Philos. Mag. J. Sci.*, vol. 2, no. 11, pp. 559-572, Nov. 1901, doi: 10.1080/14786440109462720.

#### VI. Spatial Indexing and Data Structures

[23] J. R. Gilbert, C. Moler, and R. Schreiber, "Sparse matrices in MATLAB: Design and implementation," *SIAM J. Matrix Anal. Appl.*, vol. 13, no. 1, pp. 333-356, Jan. 1992, doi: 10.1137/0613024.

[26] Y. Saad, *Iterative Methods for Sparse Linear Systems*, 2nd ed. Philadelphia, PA, USA: SIAM, 2003.

#### VII. Distance Transforms and Implicit Representations

[28] A. Pasko, V. Adzhiev, A. Sourin, and V. V. Savchenko, "Function representation in geometric modeling: Concepts, implementation and applications," *Vis. Comput.*, vol. 11, no. 8, pp. 429-446, Aug. 1995, doi: 10.1007/BF02464333.

[29] R. A. Newcombe, S. Izadi, O. Hilliges, D. Molyneaux, D. Kim, A. J. Davison, P. Kohli, J. Shotton, S. Hodges, and A. Fitzgibbon, "KinectFusion: Real-time dense surface mapping and tracking," in *Proc. 10th IEEE Int. Symp. Mixed and Augmented Reality (ISMAR)*, Basel, Switzerland, 2011, pp. 127-136, doi: 10.1109/ISMAR.2011.6162034.

#### VIII. LiDAR Processing and 3D City Modelling

[30] F. W. Fichtner, A. A. Diakite, S. Zlatanova, and R. Voute, "Semantic enrichment of octree structured point clouds for multi-story 3D pathfinding," *Trans. GIS*, vol. 22, no. 1, pp. 233-248, Feb. 2018, doi: 10.1111/tgis.12312.

[31] B. R. Staats, A. A. Diakite, R. L. Voute, and S. Zlatanova, "Detection of doors in a voxel model, derived from a point cloud and its scanner trajectory, to improve the segmentation of the walkable space," *Int. J. Urban Sci.*, vol. 23, no. 3, pp. 369-390, 2019, doi: 10.1080/12265934.2018.1553685.

[32] M. Aleksandrov, S. Zlatanova, D. J. Heslop, and A. Diakite, "BIM-based connectivity graph and voxels classification for pedestrian-hazard interaction," *J. Spatial Sci.*, 2023, doi: 10.1080/14498596.2023.2281923.

[33] J. Zhao, Q. Xu, S. Zlatanova, L. Liu, C. Ye, and T. Feng, "Weighted octree-based 3D indoor pathfinding for multiple locomotion types," *Int. J. Appl. Earth Obs. Geoinf.*, vol. 112, p. 102900, Aug. 2022, doi: 10.1016/j.jag.2022.102900.

[34] H. Xu, C. C. Wang, X. Shen, and S. Zlatanova, "Evaluating the performance of high level-of-detail tree models in microclimate simulation," *ISPRS Ann. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. X-4/W3-2022, pp. 277-284, 2022, doi: 10.5194/isprs-annals-X-4-W3-2022-277-2022.

[35] J. Yan, S. Zlatanova, M. Aleksandrov, A. Diakite, and C. Pettit, "Integration of 3D objects and terrain for 3D modelling supporting the digital twin," *ISPRS Ann. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. IV-4/W8, pp. 147-154, 2019, doi: 10.5194/isprs-annals-IV-4-W8-147-2019.

[36] A. A. Diakite, L. Ng, J. Barton, M. Rigby, K. Williams, S. Barr, and S. Zlatanova, "Liveable city digital twin: A pilot project for the city of Liverpool (NSW, Australia)," *ISPRS Ann. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. X-4/W2-2022, pp. 45-52, 2022, doi: 10.5194/isprs-annals-X-4-W2-2022-45-2022.

[37] H. Ledoux and M. Meijers, "Topologically consistent 3D city models obtained by extrusion," *Int. J. Geogr. Inf. Sci.*, vol. 25, no. 4, pp. 557-574, Apr. 2011, doi: 10.1080/13658816.2010.507194.

[38] A. A. Diakite and S. Zlatanova, "Valid space description in BIM for 3D indoor navigation," *Int. J. 3-D Inf. Model.*, vol. 5, no. 3, pp. 1-17, 2016, doi: 10.4018/IJ3DIM.2016070101.

[39] P. Boguslawski, S. Zlatanova, D. Gotlib, M. Wyszomirski, M. Gnat, and P. Grzempowski, "3D building interior modelling for navigation in emergency response applications," *Int. J. Appl. Earth Obs. Geoinf.*, vol. 114, p. 103066, Nov. 2022, doi: 10.1016/j.jag.2022.103066.

[40] D. Wagner, M. Wewetzer, J. Bogdahn, N. Alam, M. Pries, and V. Coors, "Geometric-semantical consistency validation of CityGML models," in *Progress and New Trends in 3D Geoinformation Sciences*, Berlin, Germany: Springer, 2012, pp. 171-192, doi: 10.1007/978-3-642-29793-9_10.

#### IX. Semantic Segmentation and Deep Learning (Remote Sensing)

[41] A. Garioud, S. Giordano, S. Valero, B. Wattrelos, J. Vincenot, C. Schwartz, and J. Rives, "FLAIR-2: Textural and temporal information for semantic segmentation from multi-source optical imagery," *arXiv preprint arXiv:2305.14467*, 2023. [Online]. Available: https://arxiv.org/abs/2305.14467.

[42] A. Garioud, N. Tordeaux, S. Giordano, S. Valero, B. Wattrelos, E. Bralet, and J. Rives, "FLAIR #1: Semantic segmentation and domain adaptation dataset," *arXiv preprint arXiv:2211.12979*, 2022. [Online]. Available: https://arxiv.org/abs/2211.12979.

#### X. 3D Standards and Formats

[43] P. Cozzi, S. Lilley, and G. Maki, *3D Tiles Specification 1.0*, Open Geospatial Consortium, OGC Document 18-053r2, 2018. [Online]. Available: https://docs.ogc.org/cs/18-053r2/18-053r2.html. - also the origin of report reference [9], which supersedes it; the only master listed in both parts.

[44] G. Groeger, T. H. Kolbe, C. Nagel, and K. H. Haefele, *OGC City Geography Markup Language (CityGML) Encoding Standard*, Open Geospatial Consortium, OGC Document 12-019, 2012.

[45] Z. Yao, C. Nagel, F. Kunde, G. Hudra, P. Willkomm, A. Donaubauer, T. Adolphi, and T. H. Kolbe, "3DCityDB - a 3D geodatabase solution for the management, analysis, and visualization of semantic 3D city models based on CityGML," *Open Geospatial Data, Softw. Stand.*, vol. 3, no. 1, pp. 1-26, 2018, doi: 10.1186/s40965-018-0046-7.

#### XI. IGN Data Sources and French Geospatial Infrastructure

[48] Institut national de l'information geographique et forestiere (IGN), "RGE ALTI - Modele numerique de terrain," IGN, Saint-Mande, France, 2023. [Online]. Available: https://geoservices.ign.fr/rgealti. [Accessed: Jun. 2026].

[49] Institut national de l'information geographique et forestiere (IGN), "Referentiel a grande echelle (RGE): Systeme de reference RGF93 et projection officielle Lambert-93," IGN, Saint-Mande, France, 2009. [Online]. Available: https://geodesie.ign.fr/index.php?page=geodesie. [Accessed: Jun. 2026].

[51] Metropole de Lyon, "Arbres d'alignement - Metropole de Lyon," Data Grand Lyon, 2022. [Online]. Available: https://data.grandlyon.com/jeux-de-donnees/arbres-alignement-metropole-lyon. [Accessed: Jun. 2026].

[52] European Environment Agency (EEA), "High Resolution Layer: Imperviousness Density (IMD)," Copernicus Land Monitoring Service, Copenhagen, Denmark, 2018. [Online]. Available: https://www.eea.europa.eu/data-and-maps/data/copernicus-land-monitoring-service-imperviousness. [Accessed: Jun. 2026].

#### XII. IA.rbre Project Repository and Software

[53] TelesCoop, Metropole de Lyon, Universite Lyon 2, and Exo-dev, "UD-IArbre-Research: Research repository for the IA.rbre urban tree planting project," GitHub, 2025. [Online]. Available: https://github.com/VCityTeam/UD-IArbre-Research. [Accessed: Jun. 2026].

#### XIII. Python Scientific Libraries

[57] P. Virtanen, R. Gommers, T. E. Oliphant, M. Haberland, T. Reddy, D. Cournapeau, E. Burovski, P. Peterson, W. Weckesser, J. Bright, S. J. van der Walt, M. Brett, J. Wilson, K. J. Millman, N. Mayorov, A. R. J. Nelson, E. Jones, R. Kern, E. Larson, C. J. Carey, I. Polat, Y. Feng, E. W. Moore, J. VanderPlas, D. Laxalde, J. Perktold, R. Cimrman, I. Henriksen, E. A. Quintero, C. R. Harris, A. M. Archibald, A. H. Ribeiro, F. Pedregosa, P. van Mulbregt, and SciPy 1.0 Contributors, "SciPy 1.0: Fundamental algorithms for scientific computing in Python," *Nature Methods*, vol. 17, no. 3, pp. 261-272, Mar. 2020, doi: 10.1038/s41592-020-0772-5.

[58] S. Gillies, C. van der Wel, J. Van den Bossche, M. W. Taves, J. Arnott, B. C. Ward, and others, *Rasterio: Geospatial raster I/O for Python programmers*, 2013. [Online]. Available: https://github.com/rasterio/rasterio. [Accessed: Jun. 2026].

[59] K. Jordahl, J. Van den Bossche, M. Fleischmann, J. Wasserman, J. McBride, J. Gerard, J. Tratner, M. Perry, A. G. Badaracco, C. Farmer, G. A. Hjelle, A. D. Snow, M. Cochran, S. Gillies, L. Culbertson, M. Bartos, N. Eubank, maxalbert, A. Bilogur, S. Rey, C. Ren, D. Arribas-Bel, L. Wasser, L. D. Wolf, M. Journois, J. Wilson, A. Greenhall, C. Holdgraf, Filipe, and F. Leblanc, *geopandas/geopandas: v0.8.1*, Zenodo, 2020, doi: 10.5281/zenodo.3946761.

[60] A. Miles, J. Maitin-Shepard, and others, *Zarr: An implementation of chunked, compressed, N-dimensional arrays for Python*, 2015. [Online]. Available: https://zarr.readthedocs.io. [Accessed: Jun. 2026].

[62] GDAL/OGR contributors, *GDAL/OGR Geospatial Data Abstraction software Library*, Open Source Geospatial Foundation, 2023. [Online]. Available: https://gdal.org. [Accessed: Jun. 2026].

[63] Q.-Y. Zhou, J. Park, and V. Koltun, *Open3D: A Modern Library for 3D Data Processing*, 2018. [Online]. Available: http://www.open3d.org. [Accessed: Jun. 2026].

#### XIV. Urban Forestry, Ecosystem Services, and Urban Heat

[64] D. J. Nowak, D. E. Crane, and J. C. Stevens, "Air pollution removal by urban trees and shrubs in the United States," *Urban For. Urban Green.*, vol. 4, nos. 3-4, pp. 115-123, 2006, doi: 10.1016/j.ufug.2006.01.007.

[65] F. J. Escobedo, T. Kroeger, and J. E. Wagner, "Urban forests and pollution mitigation: Analyzing ecosystem services and disservices," *Environ. Pollut.*, vol. 159, nos. 8-9, pp. 2078-2087, Aug. 2011, doi: 10.1016/j.envpol.2011.01.010.

[66] C. C. Konijnendijk, K. Nilsson, T. B. Randrup, and J. Schipperijn, Eds., *Urban Forests and Trees*. Berlin, Germany: Springer, 2005, doi: 10.1007/3-540-27684-X.

[67] E. G. McPherson, J. R. Simpson, P. J. Peper, S. E. Maco, S. L. Gardner, S. K. Cozad, and Q. Xiao, "Midwest community tree guide: Benefits, costs, and strategic planting," USDA Forest Service Pacific Southwest Research Station, General Technical Report PSW-GTR-199, 2006.

#### XV. LiDAR Forest Metrics and Canopy Structure

[68] M. A. Lefsky, W. B. Cohen, S. A. Acker, G. G. Parker, T. A. Spies, and D. Harding, "LiDAR remote sensing of the canopy structure and biophysical properties of Douglas-fir western hemlock forests," *Remote Sens. Environ.*, vol. 70, no. 3, pp. 339-361, Dec. 1999, doi: 10.1016/S0034-4257(99)00052-8.

[69] N. C. Coops, T. Hilker, M. A. Wulder, B. St-Onge, G. Newnham, A. Siggins, and J. A. T. Trofymow, "Estimating canopy structure of Douglas-fir forest stands from discrete-return LiDAR," *Trees*, vol. 21, no. 3, pp. 295-310, Jun. 2007, doi: 10.1007/s00468-006-0119-6.

[70] T. Jucker, J. Caspersen, J. Chave, C. Antin, N. Barbier, F. Bongers, M. Dalponte, K. J. van Ewijk, D. I. Forrester, M. Haeni, S. I. Higgins, R. J. Holdaway, Y. Iida, C. Lorimer, P. L. Marshall, S. Momo, G. R. Moncrieff, P. Ploton, L. Poorter, K. A. Rahman, M. Schlund, B. Sonke, F. J. Sterck, A. T. Trugman, V. A. Usoltsev, M. C. Vanderwel, P. Waldner, B. M. M. Wedeux, C. Wirth, H. Woell, M. Woods, W. Xiang, N. E. Zimmermann, and D. A. Coomes, "Allometric equations for integrating remote sensing imagery into forest monitoring programmes," *Glob. Change Biol.*, vol. 23, no. 1, pp. 177-190, Jan. 2017, doi: 10.1111/gcb.13388.

#### XVI. Soil Sealing, Hydrology, and Urban Infiltration

[71] K. J. Beven and M. J. Kirkby, "A physically based, variable contributing area model of basin hydrology," *Hydrol. Sci. Bull.*, vol. 24, no. 1, pp. 43-69, Mar. 1979, doi: 10.1080/02626667909491834.

[72] P. Poelmans and A. Van Rompaey, "Detecting and modelling spatial patterns of urban sprawl in highly fragmented areas: A case study in the Flanders-Brussels region," *Landscape Urban Plan.*, vol. 93, nos. 1-2, pp. 10-19, Jun. 2009, doi: 10.1016/j.landurbplan.2009.05.018.

#### XVII. Digital Twins and Smart City Data Integration

[73] J. Doellner, T. H. Kolbe, F. Liecke, T. Sgouros, and K. Teichmann, "The virtual 3D city model of Berlin - Managing, integrating and communicating complex urban information," in *Proc. 25th Int. Symp. Urban Data Management (UDMS 2006)*, Aalborg, Denmark, 2006.

[74] M. Aleksandrov, A. Diakite, J. Yan, W. Li, and S. Zlatanova, "System architecture for management of BIM, 3D GIS and sensor data," *ISPRS Ann. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. IV-4/W9, pp. 3-10, 2019, doi: 10.5194/isprs-annals-IV-4-W9-3-2019.

[75] R. Xie, S. Zlatanova, J. Lee, and M. Aleksandrov, "A motion-based conceptual space model to support 3D evacuation simulation in indoor environments," *ISPRS Int. J. Geo-Inf.*, vol. 12, no. 12, p. 494, 2023, doi: 10.3390/ijgi12120494.

[76] W. Li, S. Zlatanova, J. Yan, A. Diakite, and M. Aleksandrov, "A geo-database solution for the management and analysis of building model with multi-source data fusion," *Int. Arch. Photogramm. Remote Sens. Spatial Inf. Sci.*, vol. XLII-4/W20, pp. 55-63, 2019, doi: 10.5194/isprs-archives-XLII-4-W20-55-2019.

[77] W. Xu, L. Liu, S. Zlatanova, W. Penard, and Q. Xiong, "A pedestrian tracking algorithm using grid-based indoor model," *Autom. Constr.*, vol. 92, pp. 173-187, Aug. 2018, doi: 10.1016/j.autcon.2018.04.003.

[78] R. Xie, S. Zlatanova, and J. Lee, "3D indoor environments in pedestrian evacuation simulations," *Autom. Constr.*, vol. 144, p. 104593, Dec. 2022, doi: 10.1016/j.autcon.2022.104593.

#### XVIII. Big Data Pipeline Architecture

[79] M. Kleppmann, *Designing Data-Intensive Applications: The Big Ideas Behind Reliable, Scalable, and Maintainable Systems*. Sebastopol, CA, USA: O'Reilly Media, 2017.

[80] W. H. Inmon, *Building the Data Warehouse*, 4th ed. Indianapolis, IN, USA: Wiley, 2005.

[81] N. Marz and J. Warren, *Big Data: Principles and Best Practices of Scalable Real-Time Data Systems*. Shelter Island, NY, USA: Manning Publications, 2015.

#### XIX. Computational Geometry Foundations

[82] J. E. Bresenham, "Algorithm for computer control of a digital plotter," *IBM Syst. J.*, vol. 4, no. 1, pp. 25-30, 1965, doi: 10.1147/sj.41.0025.

[83] F. P. Preparata and M. I. Shamos, *Computational Geometry: An Introduction*. New York, NY, USA: Springer-Verlag, 1985, doi: 10.1007/978-1-4612-1098-6.

### 2.2 Additional consulted sources (not in the master list)

A. Peytavie, "Generation procedurale de monde," Ph.D. thesis, Universite Claude Bernard Lyon 1, France, 2010. - read in depth during the design phase; the report cites the companion Arches paper [27] instead.

B. Benes and R. Forsbach, "Layered data representation for visual simulation of terrain erosion," in Proc. Spring Conf. Computer Graphics (SCCG), 2001, pp. 80-86. - the layered-terrain ancestor of both Arches and the column store; identified via the Peytavie comparison.

T. Ito, T. Fujimoto, K. Muraoka, and N. Chiba, "Modeling rocky scenery taking into account joints," in Proc. Computer Graphics International, 2003, pp. 244-247. - Peytavie's voxel-enumeration baseline; read for the SBRT comparison.

H. Zhou, J. Sun, G. Turk, and J. M. Rehg, "Terrain synthesis from digital elevation models," IEEE Trans. Vis. Comput. Graphics, vol. 13, no. 4, pp. 834-848, 2007. - Peytavie-context reading.

A. Glassner, "Aperiodic tiling," IEEE Comput. Graph. Appl., vol. 18, no. 3, pp. 83-90, 1998. - basis of Peytavie's rock-pile follow-up work.

J. N. Tsitsiklis, "Efficient algorithms for globally optimal trajectories," IEEE Trans. Autom. Control, vol. 40, no. 9, pp. 1528-1538, 1995. - grid-based pathing on discrete models; noted as applicable to column-grid shadow walks.

T. M. Cover and P. E. Hart, "Nearest neighbor pattern classification," IEEE Trans. Inf. Theory, vol. 13, no. 1, pp. 21-27, 1967. - classical grounding for the resolve() majority/support tiebreak.

R. M. Haralick, S. R. Sternberg, and X. Zhuang, "Image analysis using mathematical morphology," IEEE Trans. Pattern Anal. Mach. Intell., vol. 9, no. 4, pp. 532-550, 1987. - anchor for the morphological denoise filter design.

A. Elfes, "Using occupancy grids for mobile robot perception and navigation," IEEE Computer, vol. 22, no. 6, pp. 46-57, 1989. - the occupancy-grid lens on the multiset-to-partition resolve step.

B. Johnson, Y. Song, E. Murphy-Hill, and R. Bowdidge, "Why don't software developers use static analysis tools to find bugs?" in Proc. ICSE, 2013, pp. 672-681. - added while fact-checking external static-analysis reports of this codebase.

T. Moller and B. Trumbore, "Fast, minimum storage ray-triangle intersection," J. Graphics Tools, vol. 2, no. 1, pp. 21-28, 1997. - seeded as a candidate for the ray-tracing chapter; not used (the column walker needs no triangle tests).

Note: one further candidate entry - recorded without title, volume or DOI, and never confirmed - was removed from this listing and is not included in the counts.

---

*Counts: 46 references cited in the report; 61 uncited master entries + 11 additional consulted sources; 118 entries in total. Of the 46 references, 26 carry a master number (entry [9] supersedes its master entry with the current version of the standard), 5 were added during report curation, 11 (entries [35] to [45]) were added with the solar position and canopy extinction work, 1 (entry [46]) with the encoder-validation metric, 1 (entry [29]) with the Section 3.3 sunlight-computation fact-check, and 2 ([8] and [24]) while fact-checking the input and state-of-the-art claims at their sources, which is also when [17] and [1] were promoted out of the master list; [30] was promoted from master [55] with the 2-D and 2.5-D exemplar of Section 3.3. The 26 Part-1 masters and the 61 Part-2 masters come to 87 slots against 86 masters because master [43] is deliberately listed in both parts - the 1.0 specification was consulted in its own right and is also the origin of the superseding reference [9].*
