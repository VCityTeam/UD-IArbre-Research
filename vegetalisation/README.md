# Vegetalisation

This directory groups the documentation, reference material, illustrations, and reproducible code used for the urban vegetation work in the IA.rbre project.

## What Is Here

- [code/](code/README.md): reproducible workflow for LiDAR + orthophoto processing, FLAIR-HUB inference, probability reweighting, fusion, and evaluation.
- [images/](images): figures used by the syntheses and presentations.
- [Revue-segmentation-végétation-urbaine.md](Revue-segmentation-végétation-urbaine.md): broader literature review on urban vegetation segmentation by deep learning.
- [Synthese-COSIA-FLAIRHUB.md](Synthese-COSIA-FLAIRHUB.md): comparison of COSIA and FLAIR-HUB for vegetation segmentation.
- [Synthese-COSIA-FLAIRHUB.pdf](Synthese-COSIA-FLAIRHUB.pdf): PDF export of the COSIA / FLAIR-HUB synthesis.
- [Synthese-Amelioration-Flair.md](Synthese-Amelioration-Flair.md): notes on possible improvements around FLAIR-based processing.
- [Synthese-LidarHD-Myria3D.md](Synthese-LidarHD-Myria3D.md): notes about LiDAR HD and Myria3D as related inputs or approaches.
- [Synthese-These-Arnaud-Bellec-2018.md](Synthese-These-Arnaud-Bellec-2018.md): summary tied to earlier research material used for this topic.
- [Présentation Cotech 20-11-2025 Segmentation Végétalisation.pdf](<Présentation Cotech 20-11-2025 Segmentation Végétalisation.pdf>): project presentation.

## Recommended Reading Order

1. Start with [Revue-segmentation-végétation-urbaine.md](Revue-segmentation-végétation-urbaine.md) for the broader landscape.
2. Continue with [Synthese-COSIA-FLAIRHUB.md](Synthese-COSIA-FLAIRHUB.md) for the main tooling comparison used in this repository.
3. Use [code/README.md](code/README.md) when you want to reproduce or extend the workflow.
4. Refer to the other synthesis notes for narrower experiments and follow-up ideas.

## Code Map

The implementation lives in [code/](code/README.md). The main entry points are:

- [code/run_workflow.py](code/run_workflow.py): orchestrates the end-to-end workflow.
- [code/workflow_utils.py](code/workflow_utils.py): shared path, config, and workflow helpers.
- [code/extract_nuage.py](code/extract_nuage.py): extracts and rasterizes LiDAR point-cloud inputs.
- [code/ortho_extract.py](code/ortho_extract.py): downloads or assembles orthophoto tiles.
- [code/ortho_fusion.py](code/ortho_fusion.py): merges orthophoto tiles into a mosaic.
- [code/fusion_nuage.py](code/fusion_nuage.py): merges LiDAR-derived rasters.
- [code/flair_probs_reweight.py](code/flair_probs_reweight.py): reweights FLAIR class probabilities for vegetation recovery.
- [code/fusion_flair.py](code/fusion_flair.py): post-processing helper around FLAIR outputs.
- [code/fusion_lidar_flair.py](code/fusion_lidar_flair.py): main LiDAR / FLAIR fusion logic.
- [code/confusionMatrix.py](code/confusionMatrix.py): evaluation against a reference raster.
- [code/lidarCorrection.py](code/lidarCorrection.py): optional LiDAR cleanup step.
- [code/calculateVegetationFromLidar.py](code/calculateVegetationFromLidar.py): historical LiDAR-only vegetation derivation.
- [code/fusionBetweenFlairAndLidar.py](code/fusionBetweenFlairAndLidar.py): historical fusion script kept for comparison.

## Configuration And Tests

- [code/configs/baseline/config_zonal_detection.yaml](code/configs/baseline/config_zonal_detection.yaml): baseline FLAIR-HUB inference configuration.
- [code/configs/baseline/configs.yml](code/configs/baseline/configs.yml): baseline post-processing, fusion, and evaluation configuration.
- [code/tests/](code/tests): regression tests for workflow helpers, reweighting, fusion, and file handling.
- [code/pyproject.toml](code/pyproject.toml): local Python project metadata and test tooling.
- [code/docker-compose.yml](code/docker-compose.yml) and [code/Dockerfile](code/Dockerfile): reproducible execution environment.

## Outputs And Working Data

Runtime inputs and generated outputs are expected under `vegetalisation/code/workdir/`, as described in [code/README.md](code/README.md). Some temporary folders may also appear inside `vegetalisation/code/` during local runs and tests; the files listed above are the tracked project artifacts to use as entry points.
