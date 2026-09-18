1. VDB - Hierarchical hash map (not a tree)

OpenVDB (DreamWorks, now Academy Software Foundation) is the industry standard for sparse volumes in film, robotics, and autonomous vehicles.
Python bindings: pyopenvdb. Used in NVIDIA's robotics stack (Isaac Sim) for exactly this kind of 3D scene understanding.

OpenVDB (Museth, SIGGRAPH 2013) is not a tree in the pointer-chasing sense. It is a fixed-depth hierarchy of dense arrays, indexed at the top by a flat hash map. The depth never changes regardless of scene size.

VDB ships a production-quality DDA ray traversal: tools::DDA<>. It visits only active leaf nodes, stepping through empty nodes in one operation using the tile structure.

2. Run-Length Encoding (RLE) column store:  2D grid of 1D interval trees.

DEM gives the ground interval, LiDAR gives canopy intervals, networks give underground intervals. No voxelization step is needed for the layers that are already columnar.

**Compressed Sparse Column** representation borrowed from sparse linear algebra.



3. Brick map - dense chunks, sparse index

We divide the scene into fixed-size bricks of, e.g., 64x64x64 voxels at 0.5m resolution = 32m cubes. Store only bricks where data exists in a flat hash dict. Each brick is a dense numpy array. Empty space costs nothing.
Each brick is independently processable (can be parallelised). Merging two layers (buildings + vegetation) is a per-brick operation.

4. Implicit / on-demand voxelisation

We define every urban object as a mathematical function f(x,y,z) -> semantic_class. Buildings become extruded polygon functions. Trees become the Gorte 5-parameter ellipsoid formula. Underground networks become cylindrical buffer functions.
When a query arrives -"what is at position (x,y,z)?"- evaluate all relevant functions at that point and take the highest-priority result. The voxel grid is materialised only where and when needed (lazy evaluation).
LiDAR point clouds are not a function. Raw LiDAR returns cannot be expressed as a closed-form implicit. They must either be converted to a semantic layer first (via the RF classifier from Paper: Point cloud voxel classification of aerial urban LiDAR using voxel attributes and random forest) or kept as a separate KD-tree point store for feature computation. 


Suggested approach RLE column: We avoid the isotropic nature of most voxel approaches.

The column store has no concept of "left and right." For shadow computation, a ray from (x0, y0) toward the sun at angle (az, el) will cross multiple columns in sequence. This is handled not by the data structure knowing its neighbours, but by the query traversing a series of columns along the 2D Bresenham path of the ray

Bresenham path of rays / bresenham's line algorithm  / Amanatides & Woo / DDA 
