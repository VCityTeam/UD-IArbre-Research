import numpy as np
import geopandas as gpd
from rasterstats import zonal_stats
from sklearn.linear_model import LinearRegression
import rasterio

def slope(mnt_data, resolution=1):
    """
    Fonction pour calculer les pente locale à partir d'un MNT (Modèle Numérique de Terrain).

    Paramètres :
    - mnt_data : numpy.ndarray, données du MNT.
    - resolution : int, résolution spatiale du MNT en mètre (par défaut 1 mètre).

    Retourne :
    - slope_rad : numpy.ndarray, pente en radians.
    - slope_angle : numpy.ndarray, pente en degrés.
    """
    x, y = np.gradient(mnt_data, resolution)
    slope = np.sqrt(x**2 + y**2)
    slope_rad = np.arctan(slope)
    slope_angle = np.degrees(slope_rad)
    return slope_rad, slope_angle


def best_fit_plane_slope(mnt_data, mask, mnt_transform):
    """
    Fonction pour calculer la pente moyenne d'un plan ajusté aux points d'un MNT.
    Ici, l'objectif est de trouver la pente "moyenne" sur le casier qui s'adapte le mieux à tous les points de l'aire d'étude.
    On ne prend pas en compte les points qui sont en dehors du casier (si le masque est correctement appliqué).
    On ne tiens pas compte non plus de la possible inclinaison du plan, seulement sa valeur (ex:8°)

    Paramètres :
    - mnt_data : numpy.ndarray, données du MNT.
    - mask : numpy.ndarray, masque binaire pour sélectionner les points d'intérêt.
    - mnt_transform : Affine, transformation affine du MNT.

    Retourne :
    - best_fit_plane : list, pente moyenne en degrés pour le plan ajusté.
    """
    best_fit_plane = []
    rows, cols = np.where(mask)
    zs = mnt_data[rows, cols] # Prend les valeurs de z (altitude) des pixels sur les lignes "rows" et colonnes "cols"
    xs = rasterio.transform.xy(mnt_transform, rows, cols, offset='center')[0] # Prend les coordonnées x et y des pixels sur les lignes "rows" et colonnes "cols", [0] pour x
    ys = rasterio.transform.xy(mnt_transform, rows, cols, offset='center')[1] # Prend les coordonnées x et y des pixels sur les lignes "rows" et colonnes "cols", [1] pour y

    if len(zs) >= 3: # Taille de matrice de 3x3, standard pour des calcul des pentes ou de plans
        A = np.c_[xs, ys] # Transforme les slice en matrice 2D (en concaténant  sur la 2ème dimension)
        model = LinearRegression().fit(A, zs)
        dzdx, dzdy = model.coef_
        plane_slope_rad = np.arctan(np.sqrt(dzdx**2 + dzdy**2))
        best_fit_plane.append(np.degrees(plane_slope_rad))
    else:
        best_fit_plane.append(0)

    return best_fit_plane


def calculate_slope(mnt_data, mnt_transform, casiers, resolution=1, method="mean_thresholded"):
    """
    Fonction pour calculer la pente pour chaque casier selon différentes méthodes.

    Paramètres :
    - mnt_data : numpy.ndarray, données du MNT.
    - mnt_transform : Affine, transformation affine du MNT.
    - casiers : GeoDataFrame, grille de casiers.
    - resolution : int, résolution spatiale du MNT (par défaut 1 mètre).
    - method : str, méthode de calcul de la pente ("mean_thresholded", "slope_std_dev", etc.).

    Retourne :
    - slope_dict : dict, dictionnaire contenant les pentes calculées pour chaque casier.
    """
    slope_rad, slope_angle = slope(mnt_data, resolution)
    slope_dict = {"slope": []}

    for geom in casiers.geometry:
        mask = rasterio.features.geometry_mask([geom], transform=mnt_transform, invert=True, out_shape=mnt_data.shape)
        slope_vals = slope_angle[mask]

        match method:
            case "mean_thresholded":
                filtered = slope_vals[(slope_vals >= 5) & (slope_vals <= 50)]
                slope_dict["slope"].append(filtered.mean() if filtered.size > 0 else 0)
            case "slope_std_dev":
                slope_dict["slope"].append(np.std(slope_vals) if slope_vals.size > 0 else 0)
            case "slope_max":
                slope_dict["slope"].append(np.max(slope_vals) if slope_vals.size > 0 else 0)
            case "slope_mean_denoised":
                slope_vals_denoised = slope_rad[mask]
                slope_dict["slope"].append(np.mean(slope_vals_denoised) if slope_vals_denoised.size > 0 else 0)
            case "best_fit_plane":
                slope_dict["slope"].append(best_fit_plane_slope(mnt_data, mask, mnt_transform))

    return slope_dict

def compute_infiltration_score(casiers, imperviousness_path, imperviousness_factor, slope_factor):
    """
    Fonction pour calculer l'indice d'infiltration pour chaque casier.

    Paramètres :
    - casiers : GeoDataFrame, grille de casiers.
    - imperviousness_path : str, chemin vers le raster d'imperméabilité.
    - imperviousness_factor : float, facteur de pondération pour l'imperméabilité.
    - slope_factor : float, facteur de pondération pour la pente.

    Retourne :
    - casiers : GeoDataFrame, grille de casiers avec l'indice d'infiltration ajouté.
    """
    imperviousness_stats = zonal_stats(casiers, imperviousness_path, stats=["mean"], nodata=0)
    casiers["impermeabilite"] = [s["mean"]/100 if s["mean"] is not None else 0 for s in imperviousness_stats]
    casiers["pente_norm"] = (casiers["slope"] - casiers["slope"].min()) / (casiers["slope"].max() - casiers["slope"].min())
    casiers["indice_infiltration"] = (1-casiers["impermeabilite"]) * imperviousness_factor + (1 - casiers["pente_norm"]) * slope_factor
    return casiers

def calculate_ibk(mnt):
    """
    Fonction pour calculer l'Indice de Beven Kirkby (IBK) à partir d'un MNT.

    Paramètres :
    - mnt : numpy.ndarray, données du MNT.

    Retourne :
    - ibk : numpy.ndarray, indice IBK.
    - slope_rad : numpy.ndarray, pente en radians.
    - drainage_area : numpy.ndarray, surface drainée.
    """
    slope_rad, slope_angle = slope(mnt)
    drainage_area = calculate_drainage_area(mnt)
    slope_rad[slope_rad == 0] = 1e-6
    ibk = np.log(drainage_area / np.tan(slope_rad) + 1e-6)
    return ibk, slope_rad, drainage_area


def create_grid(bounds, crs, casier_size=10):
    """
    Fonction pour créer une grille de casiers réguliers à partir des limites spatiales.

    Paramètres :
    - bounds : shapely.bounds, limites spatiales (left, bottom, right, top).
    - crs : str ou CRS, système de coordonnées de référence.
    - casier_size : int, taille des casiers en unités spatiales (par défaut 10).

    Retourne :
    - grid_gdf : GeoDataFrame, grille de casiers.
    """
    from shapely.geometry import box
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
    return grid_gdf


def calculate_drainage_area(mnt):
    """
    Fonction pour calculer une surface drainée simplifiée à partir d'un MNT.

    Paramètres :
    - mnt : numpy.ndarray, données du MNT.

    Retourne :
    - drainage_area : numpy.ndarray, surface drainée.
    """
    drainage_area = np.ones_like(mnt)
    for i in range(1, mnt.shape[0] - 1):
        for j in range(1, mnt.shape[1] - 1):
            drainage_area[i, j] += np.sum(mnt[i-1:i+2, j-1:j+2] < mnt[i, j])
    return drainage_area
