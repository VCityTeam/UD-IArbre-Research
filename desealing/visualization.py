import matplotlib.pyplot as plt
import geopandas as gpd
import numpy as np
from mpl_toolkits.axes_grid1 import make_axes_locatable

def plot_tiles_casier(data_to_plot: gpd.GeoDataFrame, docker_check: bool, output_path: str):
    """
    Function to visualize several sets of vector data:
    - Infiltration index
    - Normalized slope
    - Imperviousness

    Parameters:
    - data_to_plot: GeoDataFrame containing the vector data to plot, with the following columns:
        - "infiltration_index": Infiltration index
        - "normalized_slope": Normalized slope
        - "imperviousness": Imperviousness
        - "geometry": Geometry of the vector data

    This function creates a single figure with three subplots.
    """
    fig, axes = plt.subplots(2, 2, figsize=(24, 12))

    # Map for the infiltration index
    infil = data_to_plot.plot(
        column="infiltration_index",
        ax=axes[0, 0],
        legend=False,
        cmap="turbo",
        edgecolor="k",
        scheme="user_defined",
        classification_kwds={"bins": [i / 20 for i in range(0, 21)]}
    )
    axes[0, 0].set_title("Infiltration Index Map")

    # Map for the normalized slope
    pente = data_to_plot.plot(
        column="normalized_slope",
        ax=axes[0, 1],
        legend=False,
        cmap="plasma",
        edgecolor="k",
        scheme="user_defined",
        classification_kwds={"bins": [i / 20 for i in range(0, 21)]}
    )
    axes[0, 1].set_title("Normalized Slope Map")

    # Map for imperviousness
    impermeab = data_to_plot.plot(
        column="imperviousness",
        ax=axes[1, 0],
        legend=False,
        cmap="turbo",
        edgecolor="k",
        scheme="user_defined",
        classification_kwds={"bins": [i / 20 for i in range(0, 21)]}
    )
    axes[1, 0].set_title("Imperviousness Map")

    # Hide the empty subplot
    axes[1, 1].axis("off")

    # Add colorbars for each subplot

    for ax, column, cmap in zip(
        [axes[0, 0], axes[0, 1], axes[1, 0]],
        ["infiltration_index", "normalized_slope", "imperviousness"],
        ["turbo", "plasma", "turbo"] # These colormaps can be adjusted/changed as needed
        #["grey_r", "grey_r", "grey_r"] # These colormaps can be adjusted/changed as needed
    ):
        divider = make_axes_locatable(ax)
        cax = divider.append_axes("right", size="5%", pad=0.1)
        sm = plt.cm.ScalarMappable(
            cmap=cmap,
            norm=plt.Normalize(
                vmin=data_to_plot[column].min(),
                vmax=data_to_plot[column].max()
            )
        )
        plt.colorbar(sm, cax=cax)

    plt.tight_layout()

    if not docker_check:
        plt.show()
    else:
        # Save the plot as an image file
        save_plot_as_image(fig, output_path + "/casiers_infiltration")

def plot_tiles_ibk(ibk, slope_percent, drainage_area):
    """
    Function to visualize three sets of raster data: 
    - Beven Kirkby Index (IBK) / Topographical Wetness Index (TWI) 
    - Slope percentage
    - Drainage area

    Parameters:
    - ibk : 2D array, representing the Beven Kirkby Index (IBK) or Topographical Wetness Index (TWI)
    - slope_percent : 2D array, representing the slope percentage
    - drainage_area : 2D array, representing the drainage area

    This function creates a single figure with three subplots, each displaying one of the datasets.
    """
    fig, axes = plt.subplots(1, 3, figsize=(16, 6))

    # IBK / TWI map
    im = axes[0].imshow(ibk, cmap="turbo")  
    fig.colorbar(im, ax=axes[0])  
    axes[0].set_title("Beven Kirkby Index (IBK) / TWI")

    # Slope percentage map
    im = axes[1].imshow(slope_percent, cmap="plasma")  
    fig.colorbar(im, ax=axes[1])
    axes[1].set_title("Slope Percentage")

    # Drainage area map
    im = axes[2].imshow(drainage_area, cmap="plasma")  
    fig.colorbar(im, ax=axes[2])
    axes[2].set_title("Drainage Area")

    plt.tight_layout()
    plt.show()

def save_plot_as_image(fig, filename):
    """
    Function to save a matplotlib figure as an image file.

    Parameters:
    - fig: The matplotlib figure to save.
    - filename: The name of the file to save the figure as (should include the file extension, e.g., 'plot.png').
    """
    fig.savefig(filename, bbox_inches='tight')
    plt.close(fig)  # Close the figure to free up memory
    print(f"Plot saved as {filename}")
    