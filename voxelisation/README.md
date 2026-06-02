# Voxel-Based Representation for Volumetric Modeling of Territories

## Context

The IA.RBRE project explores an innovative paradigm for territorial representation based on the concept of information stored at the pixel level.
This methodological framework enables each rasterized element (pixel) to be associated with rich information combining environmental, morphological, 
and artificial intelligence–derived data. The objective is to move beyond traditional GIS approaches by designing a homogeneous large-scale information 
model that supports territorial analysis and simulation.

However, the intrinsic limitation of the pixel paradigm lies in its two-dimensional nature. Territories, and particularly their vegetation and built
components, are organized in space through volumetric structures that can only be partially represented in 2D. Phenomena such as shading, solar exposure, 
light propagation, and radiation interception by vegetation require a discrete and homogeneous 3D model capable of linking each portion of space to physical and environmental attributes.

## Internship Objectives

The objective of this internship is to extend the IA.RBRE paradigm toward a volumetric representation by investigating a voxel-based data structure 
(volumetric pixels), in which information is attached not to an elementary surface but to an elementary volume.

The work will draw inspiration from world-representation models used in simulation and gaming environments (such as Minecraft), 
where territories are decomposed into grid-oriented volumetric blocks.

The main objectives are:

* Design a voxelized data model suitable for representing different types of spatial entities, including vegetation, soil, buildings, and subsurface structures.
* Construct this model from existing datasets (LiDAR, DSM, CityGML, etc.).
* Classify and categorize voxels using these data sources.
* Experiment with shadow-casting algorithms (or volumetric ray casting) that account for interactions between voxels representing different territorial elements.
* Compare the results with existing 2D and 2.5D approaches (digital surface models, solar radiation rasters, etc.) in order to assess improvements in accuracy, realism, and interpretability.

## Team 
- Nikolaos Vynios
- John Samuel
- Gilles Gesquière

