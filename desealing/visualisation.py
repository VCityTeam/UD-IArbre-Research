import matplotlib.pyplot as plt
import geopandas as gpd
import numpy as np

def plot_tiles_casier(data_to_plot: gpd.GeoDataFrame):
    """
    Fonction pour visualiser plusieurs ensembles de données vectorielles :
    - Indice d'infiltration
    - Pente normalisée
    - Imperméabilité

    Paramètres :
    - data_to_plot : GeoDataFrame, contenant les données vectorielles à tracer, contenues dans leur colonne respective:
        - "indice_infiltration" : Indice d'infiltration
        - "pente_norm" : Pente normalisée
        - "impermeabilite" : Imperméabilité
        

    Cette fonction crée une figure unique avec trois sous-graphiques
    """
    fig, axes = plt.subplots(2, 2, figsize=(16, 9))

    # Carte pour l'indice d'infiltration
    data_to_plot.plot(
        column="indice_infiltration",
        ax=axes[0, 0],
        legend=True,
        cmap="viridis",
        edgecolor="k",
        scheme="user_defined",
        classification_kwds={"bins": [i / 20 for i in range(0, 21)]}  # Tous les 5%
    )
    axes[0, 0].set_title("Indice d'infiltration")

    # Carte pour la pente
    data_to_plot.plot(
        column="pente_norm",
        ax=axes[0, 1],
        cmap="plasma",
        edgecolor="k",
        scheme="user_defined",
        classification_kwds={"bins": [i / 20 for i in range(0, 21)]}  # Tous les 5%
    )
    axes[0, 1].set_title("Pente normalisée")

    # Carte pour l'imperméabilité
    data_to_plot.plot(
        column="impermeabilite",
        ax=axes[1, 0],
        cmap="turbo",
        edgecolor="k",
        scheme="user_defined",
        classification_kwds={"bins": [i / 20 for i in range(0, 21)]}  # Tous les 5%
    )
    axes[1, 0].set_title("Carte d'imperméabilité")

    # Masquer le sous-graphe inutilisé
    axes[1, 1].axis("off")

    plt.tight_layout()
    plt.show()

def plot_tiles_ibk(ibk, slope_percent, drainage_area):
    """
    Fonction pour visualiser trois ensembles de données raster différents : 
    - Indice de Beven Kirkby (IBK)
    - Pourcentage de pente
    - Surface drainée

    Paramètres :
    - ibk : tableau 2D, représentant les valeurs de l'IBK
    - slope_percent : tableau 2D, représentant le pourcentage de pente
    - drainage_area : tableau 2D, représentant la surface drainée

    Cette fonction crée une figure unique avec trois sous-graphiques, chacun affichant un des ensembles de données.
    """
    fig, axes = plt.subplots(1, 3, figsize=(16, 6))

    # Tracer la carte de l'IBK
    im = axes[0].imshow(ibk, cmap="turbo")  
    fig.colorbar(im, ax=axes[0])  
    axes[0].set_title("Indice de Beven Kirkby (IBK)")

    # Tracer la carte du pourcentage de pente
    im = axes[1].imshow(slope_percent, cmap="plasma")  
    fig.colorbar(im, ax=axes[1])
    axes[1].set_title("Pente en %")

    # Tracer la carte de la surface drainée
    im = axes[2].imshow(drainage_area, cmap="plasma")  
    fig.colorbar(im, ax=axes[2])
    axes[2].set_title("Surface drainée")

    plt.tight_layout()
    plt.show()