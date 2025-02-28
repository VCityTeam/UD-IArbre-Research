from matplotlib.colors import ListedColormap
import matplotlib.pyplot as plt
import geopandas as gpd
import numpy as np

def plot_mnt_maps(ibk, rivers, watersheds):
    fig, axes = plt.subplots(1, 4, figsize=(18, 6))

    #Aucune différence entre watersheds et rivières.... Formule ou detection a revoir

    # Carte de l'IBK
    cax1 = axes[0].imshow(ibk, cmap='coolwarm')
    axes[0].set_title('Indice de Beven Kirkby (IBK)')
    axes[0].axis('off')
    fig.colorbar(cax1, ax=axes[0], orientation='vertical', label='IBK')

    # Carte des cours d'eau
    cax2 = axes[1].imshow(rivers, cmap='Blues')
    axes[1].set_title('Cours d\'eau détectés')
    axes[1].axis('off')

    # Carte des bassins versants
    cax3 = axes[2].imshow(watersheds, cmap='Purples')
    axes[2].set_title('Bassins versants détectés')
    axes[2].axis('off')

    # Carte de différence
    #cax4 = axes[3].imshow(test_diff, cmap='coolwarm')
    #axes[3].set_title('Différence')
    #axes[3].axis('off')
    #fig.colorbar(cax4, ax=axes[3], orientation='vertical', label='Différence')

    plt.tight_layout()
    plt.show()

def plot_UA_data(gdf, cmap, norm, code_2018_colors):
    fig, ax = plt.subplots(1, 1, figsize=(10, 10))
    gdf.plot(column='code_2018', cmap=cmap, norm=norm, ax=ax)
    handles = []
    for code, color in code_2018_colors.items():
        handles.append(plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=color, markersize=10, label=gdf[gdf['code_2018'] == code]['class_2018'].values))
    plt.title('Urban Atlas 2018')
    plt.axis('off')
    plt.show()

def define_urbanatlas_colormap():
    code_2018_colors = {
        11100: 'red', 12100: 'red', 12210: 'red', 12220: 'red', 12230: 'red', 12300: 'red', 12400: 'red',
        11210: 'orange', 11220: 'orange', 13300 : 'orange', 13100 : 'orange',
        11230: 'yellow', 13400: 'yellow', 14200: 'yellow', 11300: 'yellow',
        11240 : 'green', 14100 : 'green', 21000 : 'green', 22000: 'green', 23000: 'green', 24000: 'green', 25000: 'green',
        31000: 'purple', 32000: 'purple', 33000: 'purple', 40000: 'purple',
        50000: 'blue',
    }

    cmap = ListedColormap([code_2018_colors[key] for key in sorted(code_2018_colors.keys())])
    norm = plt.Normalize(vmin=min(code_2018_colors.keys()), vmax=max(code_2018_colors.keys()))
    return cmap, norm, code_2018_colors

def plot_mnt_as_gdf(gdf_UA, ibk):
    fig, axes = plt.subplots(1, 1, figsize=(15, 15))
    gdf_UA.plot(column='code_2018')

    cax1 = axes[0].imshow(ibk, cmap='coolwarm')
    axes[0].set_title('Indice de Beven Kirkby (IBK)')
    axes[0].axis('off')
    fig.colorbar(cax1, ax=axes[0], orientation='vertical', label='IBK')

    plt.title('MNT as GeoDataFrame')
    plt.axis('off')
    plt.show()
