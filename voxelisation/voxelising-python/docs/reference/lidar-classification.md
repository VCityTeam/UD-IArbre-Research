Table
Code	Classification	Notes
0	Created, never classified	Default on raw capture; should not remain 0 in delivered data
1	Unclassified	Processed but not assigned to a specific class
2	Ground	Bare earth; essential for DTM generation
3	Low Vegetation	Grass, crops, vegetation typically < 0.5 m
4	Medium Vegetation	Shrubs, bushes, ~0.5-2 m
5	High Vegetation	Trees, canopy, > 2 m
6	Building	Roofs, building structures
7	Low Point (noise)	Low outliers, clutter, errors
8	Reserved	Legacy: was "Model Key-point"
9	Water	Lakes, rivers, ponds
10	Rail	Railway tracks
11	Road Surface	Paved roads
12	Reserved	Legacy: was "Overlap Points"
13	Wire - Guard (Shield)	Shield wires on power lines
14	Wire - Conductor (Phase)	Power line conductors
15	Transmission Tower	Pylons, power poles
16	Wire-Structure Connector	Insulators, connectors
17	Bridge Deck	Bridge surfaces
18	High Noise	High outliers, atmospheric noise, birds
19	Overhead Structure	Conveyors, traffic lights, mining equipment
20	Ignored Ground	Ground near breaklines (USGS 3DEP)
21	Snow	Snow-covered surfaces
22	Temporal Exclusion	Features excluded due to temporal change
23-63	Reserved	Reserved for future ASPRS definitions
64-255	User Definable	Project-specific or vendor-specific classes

(An early 12-entry CLASS_NAMES sketch used to sit here. It has been removed
rather than left to rot: voxelizer/classes_config.py now defines 26 codes -
0-11, 13-22 and 64-67 - and IS the single source of truth. Read it there.)


Three approaches:

-Python : Current,  Z stored as discrete index, fastest performance, simplest math
-CSC Pipeline: Primary Internship goal, Z stored as exact floats from the LiDAR, preserves the sensor's native precision 
-SBRT model: Extended Internship goal, Z stored as uniform (20cm) grid, uniform stepping for shadow ray DDA and heat finite-difference

In many files we have unclassified grey between 10 and 40%, which belongs in the **legacy** classification category 8. What to do with that?
Answer: The 2023 data was made with LAS 1.2 classification, not 1.4
8 and 12 categories were still active then.
8 really matters as it is a "super high" vegetation. Turning green. 12 still has to do with overlap, will be kept unclassified for now.


Currently We only store the columns that have points in them, and inside each column we compress vertical runs of identical class into
"intervals". 
A) need to improve detection and grouping per column.
Empty columns get discarded so,
B) need to consider a regeneration algorithm around empty/black voxels/columns
C) stats that matter?
