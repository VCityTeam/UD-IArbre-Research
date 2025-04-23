import geopandas as gpd
import rasterio
import rasterio.features
import numpy as np
from rasterstats import zonal_stats
from shapely.geometry import box
import pandas as pd
import matplotlib.pyplot as plt


mnt_path = "../mnt_merged/mnt_villeurbanne_parc_3035.tif"
occupation_path = "../urban_atlas/FR003L2_LYON_UA2018_v013.gpkg"
output_path = "../casier_out/casiers_infiltration.shp"  # Chemin de sortie

# Taille du casier
casier_size = 10  # 10m x 10m

occupation = gpd.read_file(occupation_path, layer='FR003L2_LYON_UA2018')

indice_ocsol = { #Peut utiliser carte d'imperméabilité et stats sur urban atlas pour determiner l'imperméabilité de la zone
    "11100": 0.2,   
    "12100": 0.2,   
    "12210": 0.2,   
    "12220": 0.2,   
    "12230": 0.2,   
    "12400": 0.2,
    "12210": 0.4,   
    "11220": 0.4,   
    "13300": 0.4,   
    "13100": 0.4,   
    "11230": 0.6,   
    "13400": 0.6,
    "14200": 0.6,   
    "11300": 0.6,
    "11240": 0.8,   
    "14100": 0.8,
    "21000": 0.8,   
    "22000": 0.8,
    "23000": 0.8,   
    "24000": 0.8,
    "25000": 0.8,   
    "31000": 1.0,
    "32000": 1.0,
    "33000": 1.0,
    "40000": 1.0,
    "50000": 1.0,
    "11210": 0.2,
    "12300": 0.2,
}

occupation["impermeabilite"] = occupation["code_2018"].astype(str).map(indice_ocsol)#.fillna(0.5)

with rasterio.open(mnt_path) as src:
    bounds = src.bounds
    crs = src.crs

cols = int((bounds.right - bounds.left) // casier_size)
rows = int((bounds.top - bounds.bottom) // casier_size)

grid = []
for i in range(cols):
    for j in range(rows):
        minx = bounds.left + i * casier_size
        maxx = minx + casier_size
        miny = bounds.bottom + j * casier_size
        maxy = miny + casier_size
        grid.append(box(minx, miny, maxx, maxy))

grid_gdf = gpd.GeoDataFrame(geometry=grid, crs=crs)

stats = zonal_stats(grid_gdf, mnt_path, stats=["mean", "std"], nodata=0)

grid_gdf["alt_moy"] = [s["mean"] for s in stats]
grid_gdf["pente_approx"] = [s["std"] for s in stats]
print(grid_gdf.head(20))

joined = gpd.sjoin(grid_gdf.reset_index(), occupation[["geometry", "impermeabilite"]], how="left", predicate="intersects")

# Group by the original index of grid_gdf
grouped = joined.groupby("index")["impermeabilite"].mean()
grid_gdf["impermeabilite"] = grid_gdf.index.map(grouped).fillna(0.5)

grid_gdf["pente_norm"] = (grid_gdf["pente_approx"] - grid_gdf["pente_approx"].min()) / (grid_gdf["pente_approx"].max() - grid_gdf["pente_approx"].min())
grid_gdf["indice_infiltration"] = (1 - grid_gdf["impermeabilite"]) * 0.6 + (1 - grid_gdf["pente_norm"]) * 0.4

grid_gdf.to_file(output_path)


# Charger les limites du MNT
with rasterio.open(mnt_path) as src:
    mnt_bounds = src.bounds

# Filtrer les casiers dans les limites du MNT
filtered_grid = grid_gdf[grid_gdf.intersects(box(mnt_bounds.left, mnt_bounds.bottom, mnt_bounds.right, mnt_bounds.top))]

# Tracer la carte
fig, ax = plt.subplots(figsize=(10, 10))
filtered_grid.plot(column="indice_infiltration", ax=ax, legend=True, cmap="viridis", edgecolor="k")
ax.set_title("Carte d'infiltration")
plt.show()