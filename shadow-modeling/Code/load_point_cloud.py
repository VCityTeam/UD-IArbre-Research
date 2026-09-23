import numpy as np
import pdal
import json

#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Author : Karima Ouadah < ouadkarima@outlook.com >
#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def load_point_cloud(filename, classification_choice):
    if classification_choice == 1:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [filename]
        }))

    if classification_choice == 2:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification == 3 || Classification == 4 || Classification == 5",
                }
            ]
        }))

    if classification_choice == 3:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification == 3 || Classification == 4 || Classification == 5 || Classification == 8",
                }
            ]
        }))

    if classification_choice == 4:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification == 6",
                }
            ]
        }))

    if classification_choice == 5:
        pipeline = pdal.Pipeline(json.dumps({
            "pipeline": [
                filename,
                {
                    "type": "filters.expression",
                    "expression": "Classification != 3 && Classification != 4 && Classification != 5 && Classification != 8",
                }
            ]
        }))

    pipeline.execute()
    arrays = pipeline.arrays[0]
    points = np.vstack((arrays['X'], arrays['Y'], arrays['Z'], arrays['Classification'])).transpose()
    return points