# Vegetalisation Workflow

This folder contains a reproducible Docker workflow for the urban vegetation pipeline:

1. download LiDAR and orthophoto tiles,
2. build LiDAR rasters,
3. mosaic the orthophoto,
4. run `flairhub_zonal` with `LC-A RGB swinlarge upernet`,
5. reweight the FLAIR probability map to recover the vegetation argmax,
6. fuse LiDAR and FLAIR vegetation outputs,
7. run the historical LiDAR vegetation branch and verify results with a confusion matrix.

The default experiment configuration lives under
[`configs/baseline/config_zonal_detection.yaml`](configs/baseline/config_zonal_detection.yaml)
and
[`configs/baseline/configs.yml`](configs/baseline/configs.yml).
The workflow reads those files, injects run-specific runtime paths into the FLAIR-HUB template, and writes a
runtime copy before inference.

## Why RGB

FLAIR-HUB can also work with infrared orthophotos, but this workflow intentionally uses RGB only with the
`IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet` model so the pipeline stays easier to reproduce with more commonly
available orthophotos.

## Files

- `Dockerfile`: Docker image for the workflow and FLAIR-HUB.
- `docker-compose.yml`: CPU and GPU services for reproducible execution.
- `requirements-workflow.txt`: dependencies for the local orchestration scripts.
- `run_workflow.py`: end-to-end workflow runner.
- `configs/baseline/config_zonal_detection.yaml`: baseline FLAIR-HUB zonal inference configuration.
- `configs/baseline/configs.yml`: baseline post-processing, fusion, evaluation, and reweighting configuration.
- `lidarCorrection.py`: optional LiDAR NaN correction.
- `calculateVegetationFromLidar.py`: optional historical LiDAR vegetation derivation.
- `fusionBetweenFlairAndLidar.py`: optional historical LiDAR+FLAIR fusion.
- `confusionMatrix.py`: optional reproducible evaluation against a reference raster.

## Experiment Configurations

Each experiment is a self-contained directory under `configs/` that contains:

- `config_zonal_detection.yaml` for FLAIR-HUB inference settings
- `configs.yml` for reweighting, fusion, and evaluation settings

The repository currently provides:

- `configs/baseline/`

To create a new experiment, copy the baseline directory and edit the two YAML files:

```text
configs/
  baseline/
    config_zonal_detection.yaml
    configs.yml
  experiment1/
    config_zonal_detection.yaml
    configs.yml
```

Then select it with `--experiment-config-dir`.

## Expected Inputs

Create the `workdir/inputs` directory in [`vegetalisation/code`](vegetalisation/code) and store the workflow
inputs there:

- `workdir/inputs/nuage.json`
- `workdir/inputs/ortho.json`
- `workdir/inputs/reference.tiff` optional evaluation raster exported from the Grand Lyon
  `vegetation stratifiee 2018` dataset

They are used by:

- [`extract_nuage.py`](code/extract_nuage.py)
- [`ortho_extract.py`](code/ortho_extract.py)
- [`confusionMatrix.py`](code/confusionMatrix.py) when `--reference-raster` is provided

### Download `nuage.json` and `ortho.json`

Download the inventories from the Grand Lyon portal section
`Telechargement des donnees a partir de l'API WS`, then rename the downloaded files:

1. Open the LiDAR dataset download page:
   <https://data.grandlyon.com/portail/fr/jeux-de-donnees/nuage-points-lidar-2018-metropole-lyon-format-laz/telechargements>
2. Download the API WS inventory JSON and save it as `workdir/inputs/nuage.json`.
3. Open the orthophoto dataset download page:
   <https://data.grandlyon.com/portail/fr/jeux-de-donnees/orthophotographie-2018-metropole-lyon-format-tiff/telechargements>
4. Download the API WS inventory JSON and save it as `workdir/inputs/ortho.json`.
5. Optional for evaluation: open the reference vegetation dataset download page:
   <https://data.grandlyon.com/portail/fr/jeux-de-donnees/vegetation-stratifiee-2018-metropole-lyon/telechargements>
6. Download the GeoTIFF reference raster and save it as `workdir/inputs/reference.tiff`.

Expected result:

```text
vegetalisation/code/
  workdir/
    inputs/
      nuage.json
      ortho.json
      reference.tiff
```

## Build

From [`vegetalisation/code`](vegetalisation/code):

```powershell
docker compose build vegetalisation
docker compose build vegetalisation-gpu
```

`vegetalisation` is the portable CPU image.

`vegetalisation-gpu` builds a CUDA-enabled PyTorch image with `gpus: all` for NVIDIA hosts.

If you change the Dockerfile or the PyTorch/CUDA setup, run these build commands again before launching
the workflow.

## Run

### Prepare local inputs

From [`vegetalisation/code`](vegetalisation/code), create the `workdir/inputs` directory if needed:

```powershell
New-Item -ItemType Directory -Force -Path workdir\inputs | Out-Null
```

Then place the two renamed inventory files in `workdir/inputs`.

If you want evaluation outputs, also place the downloaded Grand Lyon vegetation reference raster in
`workdir/inputs/reference.tiff`.

Example on CPU:

```powershell
docker compose run --rm vegetalisation `
  python run_workflow.py `
  --run-name 1845_5175 `
  --experiment-config-dir configs/baseline `
  --nuage-json workdir/inputs/nuage.json `
  --ortho-json workdir/inputs/ortho.json `
  --xmin-start 1845000 `
  --xmin-end 1846000 `
  --ymin-start 5175000 `
  --ymin-end 5176000
```

Example on GPU:

```powershell
docker compose run --rm vegetalisation-gpu `
  python run_workflow.py `
  --run-name 1845_5175 `
  --experiment-config-dir configs/baseline `
  --nuage-json workdir/inputs/nuage.json `
  --ortho-json workdir/inputs/ortho.json `
  --xmin-start 1845000 `
  --xmin-end 1846000 `
  --ymin-start 5175000 `
  --ymin-end 5176000 `
  --use-gpu
```

### Run With Evaluation

To populate the `evaluation` folder, pass `--reference-raster` with the downloaded Grand Lyon
`vegetation stratifiee 2018` GeoTIFF:

```powershell
docker compose run --rm vegetalisation-gpu `
  python run_workflow.py `
  --run-name 1845_5175 `
  --experiment-config-dir configs/baseline `
  --nuage-json workdir/inputs/nuage.json `
  --ortho-json workdir/inputs/ortho.json `
  --reference-raster workdir/inputs/reference.tiff `
  --xmin-start 1845000 `
  --xmin-end 1846000 `
  --ymin-start 5175000 `
  --ymin-end 5176000 `
  --use-gpu
```

This writes the confusion matrix image, metrics summary JSON, and evaluation log under
`workdir/runs/1845_5175/evaluation/`.

## Main Options

Most workflow behavior should now be adjusted through your experiment configuration files:

- [`configs/baseline/config_zonal_detection.yaml`](configs/baseline/config_zonal_detection.yaml) for FLAIR-HUB inference settings
- [`configs/baseline/configs.yml`](configs/baseline/configs.yml) for reweighting, fusion, and evaluation settings

In practice, the command line only needs a small set of options for selecting inputs, area, and execution mode.

```text
--workspace                 Working root. Default: workdir
--run-name                  Output run name.
--experiment-config-dir     Directory containing both config_zonal_detection.yaml and configs.yml.
                            Default behavior uses configs/baseline through the file defaults.
--nuage-json                LiDAR inventory JSON. Default: workdir/inputs/nuage.json
--ortho-json                Orthophoto inventory JSON. Default: workdir/inputs/ortho.json
--xmin-start/--xmin-end     Bounding box selection in Lambert-93.
--ymin-start/--ymin-end     Bounding box selection in Lambert-93.
--use-gpu                   Ask FLAIR-HUB to run on GPU and use CUDA for evaluation when available.
--reference-raster          Compute a confusion matrix and summary metrics against a reference raster.
--reuse-derived-rasters     Reuse existing LiDAR/ortho mosaics for the run instead of rebuilding them.
--skip-flair                Reuse an existing FLAIR probability raster.
--skip-reweight             Skip reweighting and use an existing vegetation raster.
```

Less common flags such as explicit config file paths, legacy-branch switches, model overrides,
and download helpers are still available through `python run_workflow.py --help`, but they are intentionally
left out here to keep the day-to-day interface simple.

Full help:

```powershell
docker compose run --rm vegetalisation python run_workflow.py --help
```

## Outputs

For a run named `1845_5175`, outputs are written under:

- `workdir/runs/1845_5175/ortho/mosaic/orthophoto_mosaic.tif`
- `workdir/runs/1845_5175/lidar/mosaic/lidar_height.tif`
- `workdir/runs/1845_5175/lidar/mosaic/lidar_class.tif`
- `workdir/runs/1845_5175/flair/runtime_config.yaml`
- `workdir/runs/1845_5175/run_metadata.json`
- `workdir/runs/1845_5175/flair/probabilities/...`
- `workdir/runs/1845_5175/flair/flair_vegetation_reweighted.tif`
- `workdir/runs/1845_5175/fusion/final_fused.tif`
- `workdir/runs/1845_5175/fusion/legacy_lidar_classes.tif`
- `workdir/runs/1845_5175/fusion/legacy_fused_lidar_flair.tif`
- `workdir/runs/1845_5175/evaluation/confusion_matrix_percent.png`
- `workdir/runs/1845_5175/evaluation/metrics_summary.json`
- `workdir/runs/1845_5175/evaluation/metrics_log.txt`

## Notes

- The workflow uses the official FLAIR-HUB entry point `flairhub_zonal`, as documented in the FLAIR-HUB GitHub README.
- The model download defaults to `IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet` on Hugging Face.
- The default model revision is pinned in [`run_workflow.py`](run_workflow.py) so the workflow stays stable over time.
- Every workflow run now writes a reproducibility manifest with CLI arguments, resolved paths, model pinning, package versions, and platform metadata.
- Evaluation is only run if you pass `--reference-raster`.

## Configuration Overrides

The probability reweighting step is now configured only through your experiment
[`configs.yml`](configs/baseline/configs.yml), under `flair.reweight`.

To change class weights or output remapping for an experiment, edit that YAML or create a new
experiment folder by copying `configs/baseline/`.

### Reuse Existing Inference Inputs

If you only changed the vegetation strata definitions in your experiment's `configs.yml`, for example
[`configs/baseline/configs.yml`](configs/baseline/configs.yml), or a similar post-processing setting,
you can now skip rebuilding the derived LiDAR and orthophoto rasters and also skip FLAIR inference:

```powershell
docker compose run --rm vegetalisation python run_workflow.py `
  --run-name 1845_5175 `
  --experiment-config-dir configs/baseline `
  --xmin-start 1845000 `
  --xmin-end 1846000 `
  --ymin-start 5175000 `
  --ymin-end 5176000 `
  --reuse-derived-rasters `
  --skip-flair `
  --skip-reweight
```

This mode expects the rasters from the same run to already exist under `workdir/runs/<run-name>/`.
It only reuses them when you explicitly pass `--reuse-derived-rasters`.

## Tests

Install the test extras from [`pyproject.toml`](pyproject.toml) or install `pytest` directly, then run:

```powershell
python -m pytest -q -p no:cacheprovider -p no:tmpdir tests
```

The test suite covers:

- shared validation and shape-alignment helpers,
- FLAIR probability reweighting,
- LiDAR correction and LiDAR/FLAIR fusion logic,
- runtime configuration generation and file staging,
- orthophoto resampling behavior.
