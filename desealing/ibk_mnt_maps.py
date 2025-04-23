import numpy as np
import rasterio
from rasterio.merge import merge
import matplotlib.pyplot as plt
import glob
from scipy.ndimage import generic_filter

np.set_printoptions(threshold=np.inf)  # Affiche tout, sans coupure

# Fonction pour lire et fusionner les tuiles .asc
def read_and_merge_tiles(folder_path, nodata_value=-9999):
    asc_files = glob.glob(f"{folder_path}/*.asc")
    datasets = [rasterio.open(file) for file in asc_files]

    # Fusion des tuiles
    merged_data, merged_transform = merge(datasets, nodata=nodata_value, method='first')

    for ds in datasets:
        ds.close()

    # Remplace les valeurs nodata par np.nan
    merged_data = np.where(merged_data == nodata_value, np.nan, merged_data)

    return merged_data[0], merged_transform

# Fonction pour lire plusieurs fichiers .tif et afficher les IBK côte à côte
def process_multiple_tifs(file_paths):
    ibk_dict = {}
    for file_path in file_paths:
        with rasterio.open(file_path) as dataset:
            mnt = dataset.read(1)  # Lire la première bande
            nodata_value = dataset.nodata
            if nodata_value is not None:
                mnt = np.where(mnt == nodata_value, np.nan, mnt)

        # Afficher la carte MNT
        # plot_single_map(mnt, title=f'MNT - {file_path}')

        # Calculer IBK
        ibk, slope, drainage_area = calculate_ibk(mnt)
        
        # Clip IBK values to the 2% and 98% quantiles
        # lower_bound = np.nanquantile(ibk, 0.02)
        # upper_bound = np.nanquantile(ibk, 0.98)
        # ibk = np.clip(ibk, lower_bound, upper_bound)

        ibk_dict[len(ibk_dict) + 1] = {
            'ibk': ibk,
            'slope': slope,
            'drainage_area': drainage_area
        }

    # Print unique values in drainage area for each MNT
    for key, data in ibk_dict.items():
        unique_values = np.unique(data['drainage_area'])
        print(f"Unique values in drainage area for MNT {key}: {unique_values}")

    # Afficher les cartes IBK, pente et surface drainée côte à côte
    fig, axes = plt.subplots(len(ibk_dict), 3, figsize=(15, 4 * len(ibk_dict)))

    # Ensure axes is always a 2D array for consistent indexing
    if len(ibk_dict) == 1:
        axes = np.expand_dims(axes, axis=0)

    for i, key in enumerate(ibk_dict):
        data = ibk_dict[key]
        axes[i, 0].imshow(data['ibk'], cmap='coolwarm')
        axes[i, 0].set_title(f'IBK - MNT {i + 1}')
        axes[i, 0].axis('off')
        axes[i, 1].imshow(data['slope'], cmap='viridis')
        axes[i, 1].set_title(f'Slope - MNT {i + 1}')
        axes[i, 1].axis('off')
        axes[i, 2].imshow(data['drainage_area'], cmap='viridis')
        axes[i, 2].set_title(f'Drainage Area - MNT {i + 1}')
        axes[i, 2].axis('off')

    plt.tight_layout()
    plt.show()

# Calcul de la surface drainée (méthode simplifiée)
def calculate_drainage_area(mnt):
    drainage_area = np.ones_like(mnt)
    
    for i in range(1, mnt.shape[0] - 1):
        for j in range(1, mnt.shape[1] - 1):
            # Comptabilise les voisins plus bas comme surface drainée
            drainage_area[i, j] += np.sum(mnt[i-1:i+2, j-1:j+2] < mnt[i, j])
    
    return drainage_area

# Calcul de l'Indice de Beven Kirkby (IBK)
def calculate_ibk(mnt):
    # Calcul de la pente locale
    def calculate_slope(mnt):
        grad_x, grad_y = np.gradient(mnt)
        slope = np.sqrt(grad_x**2 + grad_y**2)  # Norme du gradient
        return slope

    slope = calculate_slope(mnt)
    drainage_area = calculate_drainage_area(mnt)

    # Éviter les divisions par zéro
    slope[slope == 0] = 1e-6

    # Calcul de l'IBK
    ibk = np.log(drainage_area / np.tan(slope) + 1e-6)
    
    print(np.min(ibk), np.max(ibk))
    return ibk, slope, drainage_area

# Exécution du programme
print("Début du programme...")

process_multiple_tifs([
    '../mnt_merged/mnt_villeurbanne_nord_parc.tif',
    '../mnt_merged/mnt_villeurbanne_parc.tif',
    '../mnt_merged/mnt_villeurbanne_parc_tetedor.tif',
    '../mnt_merged/mnt_villeurbanne_bas_doua.tif'
])

print("Programme terminé.")
