import numpy as np
import lecture
import methodes
import visualisation
import configargparse

# Création/Configuration des arguments recevable dans le fichier de configuration .yaml ou bien en ligne de commande
parser = configargparse.ArgumentParser() 

parser.add_argument(
    "-c",
    "--config",
    type=str,
    default="../config/config.yaml",
    help="Path to the configuration file.",
    is_config_file_arg=True,
    required=False,
)

parser.add_argument(
    "-t",
    "--tile_path",
    type=str,
    default="../donnees/mnt_villeurbanne_parc_2154.tif",
    help="Path to the MNT file.",
    required=True,
)

parser.add_argument(
    "-i",
    "--imperviousness_path",
    type=str,
    default="../donnees/imperviousness_lyon_2154.tif",
    help="Path to the imperviousness file.",
    required=False,
)

parser.add_argument(
    "-m",
    "--method",
    type=str,
    default="casier",
    help="Method to use for infiltration calculation.",
    choices=["casier", "ibk"],
    required=True,
)

parser.add_argument(
    "-cs",
    "--casiersize",
    type=int,
    default=10,
    help="Size of the casier in meters.",
    required=False,
)

parser.add_argument(
    "-slope",
    "--slope",
    type=str,
    default="mean_thresholded",
    help="Slope method to use.",
    choices=["mean_thresholded", "best_fit_plane", "slope_std_dev", "slope_max", "slope_mean_denoised"],
    required=False,
)

parser.add_argument(
    "-if",
    "--imperviousness_factor",
    type=float,
    default=0.4,
    help="Imperviousness factor for infiltration calculation.",
    required=False,
)

parser.add_argument(
    "-out",
    "--output_path",
    type=str,
    default="../casier_out/casiers_infiltration.shp",
    help="Path to the output shapefile.",
    required=False,
)


args = parser.parse_args()
print(args)

# Lecture des arguments
mnt_path = args.tile_path
imperviousness_path = args.imperviousness_path
output_path = args.output_path
casiersize = args.casiersize
method = args.method
slope_method = args.slope
imperviousness_factor = args.imperviousness_factor
slope_factor =  1 - imperviousness_factor # Seulement deux parametres de pondération, pas besoin de deux arguments


# Lancement du code selon la méthode choisie dans les arguments
match method:
    case "casier":
        mnt_data, bounds, crs, mnt_transform = lecture.load_single_tile(mnt_path)
        imperviousness_data, _, _, _ = lecture.load_single_tile(imperviousness_path)
        casiers = methodes.create_grid(bounds, crs, casier_size=casiersize)
        slope_dict = methodes.calculate_slope(mnt_data, mnt_transform, casiers, method=slope_method)

        for key in slope_dict:
            slope_dict[key] = np.array(slope_dict[key])

        casiers["slope"] = slope_dict["slope"].flatten()[:len(casiers)]

        # Calcul du score d'infiltration dans les casiers
        casiers = methodes.compute_infiltration_score(casiers, imperviousness_path, imperviousness_factor, slope_factor)

        print(casiers.head())
        visualisation.plot_tiles_casier(casiers)

        casiers.to_file(output_path)

    case "ibk":
        mnt_data, _, _, mnt_transform = lecture.load_single_tile(mnt_path)
        # Calcul de l'IBK
        ibk, slope_percent, drainage_area = methodes.calculate_ibk(mnt_data)
        visualisation.plot_tiles_ibk(ibk, slope_percent, drainage_area)

    case _:
        raise ValueError("Invalid method specified. Use 'casier' or 'ibk'.")
