import lecture
import pretraitement
import methodes
import visualisation
import sys

# Load data
match sys.argv[1]:
    case 'MNT_rhone_25m':
        mnt, transform = lecture.read_and_merge_tiles('MNT_rhone_25m')
    case 'MNT_rhone_1m':
        mnt, transform = lecture.read_and_merge_tiles('MNT_rhone_1m')
    case 'UrbanAtlas':
        gdf = lecture.load_data('urban_atlas/FR003L2_LYON_UA2018_v013.gpkg', 'FR003L2_LYON_UA2018')
        gdf = pretraitement.clean_data(gdf)
    case 'MNT_gdf': #Endroit de tests de MNT x UrbanAtlas
        gdf_mnt = lecture.read_merge_tiles_to_gdf('MNT_rhone_25m')
        gdf_UA = lecture.load_data('urban_atlas/FR003L2_LYON_UA2018_v013.gpkg', 'FR003L2_LYON_UA2018')
        gdf_UA = pretraitement.clean_data(gdf_UA)

        mnt, transform = lecture.read_and_merge_tiles('MNT_rhone_25m')
    


# Use methods
match sys.argv[1]:
    case 'MNT_rhone_25m' | 'MNT_rhone_1m':
        ibk, drainage_area = methodes.calculate_ibk(mnt)
        rivers = methodes.extract_rivers(mnt)
        watersheds = methodes.extract_watersheds(mnt)
    case 'UrbanAtlas':
        cmap, norm, code_2018_colors = visualisation.define_urbanatlas_colormap()
    case 'MNT_gdf':
        ibk, drainage_area = methodes.calculate_ibk(mnt)
        

    

# Visualize data
match sys.argv[1]:
    case 'MNT_rhone_25m' | 'MNT_rhone_1m':
        visualisation.plot_mnt_maps(ibk, rivers, watersheds)
    case 'UrbanAtlas':
        visualisation.plot_UA_data(gdf, cmap, norm, code_2018_colors)
    case 'MNT_gdf':
        visualisation.plot_mnt_as_gdf(gdf_mnt, ibk)
