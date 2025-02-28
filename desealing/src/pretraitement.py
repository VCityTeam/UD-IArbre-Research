def clean_data(gdf):
    gdf['code_2018'] = gdf['code_2018'].astype(int) # Cast code_2018 (object) à int sinon pas de recherche possible dans les arrays
    return gdf