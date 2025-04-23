import numpy as np
import rasterio
from rasterio.merge import merge
import matplotlib.pyplot as plt
import glob

# Fonction pour lire et fusionner les tuiles sans ajouter de valeurs par défaut
def read_and_merge_tiles(folder_path, nodata_value=-9999):
    asc_files = glob.glob(f"{folder_path}/*.asc")
    datasets = [rasterio.open(file) for file in asc_files]

    # Fusion avec nodata explicite
    merged_data, merged_transform = merge(
        datasets,
        nodata=nodata_value,
        method='first',  # Prend la première tuile en cas de chevauchement
    )
    
    for ds in datasets:
        ds.close()

    # Remplace les valeurs nodata par np.nan
    merged_data = np.where(merged_data == nodata_value, np.nan, merged_data)
    
    return merged_data[0], merged_transform

# Function to compute slope from merged terrain
def compute_slope(terrain):
    dzdx, dzdy = np.gradient(terrain)
    slope = np.sqrt(dzdx**2 + dzdy**2)
    slope_angle = np.arctan(slope)  # Angle de la pente
    return slope, slope_angle

# Fonction pour calculer l'infiltration
def compute_infiltration(slope_angle, I0=1, c=1):
    infiltration = I0 * np.exp(-c * np.tan(slope_angle))  # Formule exponentielle ?
    return infiltration

# Function to plot the merged map
def plot_merged_maps(terrain, slope, infiltration):
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    
    # Altimetry Map
    cax1 = axes[0].imshow(terrain, cmap='terrain')
    axes[0].set_title('Carte Altimétrique (Merged)')
    axes[0].axis('off')
    fig.colorbar(cax1, ax=axes[0], orientation='vertical', label='Altitude (m)')
    
    # Slope Map
    cax2 = axes[1].imshow(slope, cmap='viridis')
    axes[1].set_title('Pente du Terrain (Merged)')
    axes[1].axis('off')
    fig.colorbar(cax2, ax=axes[1], orientation='vertical', label='Pente (m/m)')
    
    # Infiltration Map
    cax3 = axes[2].imshow(infiltration, cmap='Blues')
    axes[2].set_title('Infiltration (Infiltration = I0 * exp(-c * tan(slope)))')
    axes[2].axis('off')
    fig.colorbar(cax3, ax=axes[2], orientation='vertical', label='Infiltration (m³/m²)')
    
    plt.tight_layout()
    plt.show()

# Main

folder_path = 'MNT_rhone_25m'  # Change this to your folder containing .asc tiles

# Merge all tiles
merged_terrain, merged_transform = read_and_merge_tiles(folder_path)

# Compute slope and infiltration
slope, slope_angle = compute_slope(merged_terrain)
infiltration = compute_infiltration(slope_angle)

# Plot results
plot_merged_maps(merged_terrain, slope, infiltration)
