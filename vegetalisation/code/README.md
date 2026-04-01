# Vegetalisation Workflow

This folder contains a reproducible Docker workflow for the urban vegetation pipeline:

1. download LiDAR and orthophoto tiles,
2. build LiDAR rasters,
3. mosaic the orthophoto,
4. run `flairhub_zonal` with `LC-A RGB swinlarge upernet`,
5. reweight the FLAIR probability map to recover the vegetation argmax,
6. fuse LiDAR and FLAIR vegetation outputs,
7. run the historical LiDAR vegetation branch and verify results with a confusion matrix.

The FLAIR-HUB configuration base is the repository file
[`configs/config_zonal_detection.yaml`](configs/config_zonal_detection.yaml).
The workflow reads that file, injects run-specific paths, and writes a runtime copy before inference.

## Why RGB

FLAIR-HUB can also work with infrared orthophotos, but this workflow intentionally uses RGB only with the
`IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet` model so the pipeline stays easier to reproduce with more commonly
available orthophotos.

## Files

- `Dockerfile`: Docker image for the workflow and FLAIR-HUB.
- `docker-compose.yml`: CPU and GPU services for reproducible execution.
- `requirements-workflow.txt`: dependencies for the local orchestration scripts.
- `run_workflow.py`: end-to-end workflow runner.
- `configs/config_zonal_detection.yaml`: base FLAIR-HUB zonal inference configuration.
- `lidarCorrection.py`: optional LiDAR NaN correction.
- `calculateVegetationFromLidar.py`: optional historical LiDAR vegetation derivation.
- `fusionBetweenFlairAndLidar.py`: optional historical LiDAR+FLAIR fusion.
- `confusionMatrix.py`: optional reproducible evaluation against a reference raster.

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
  --run-name doua_1845_5175 `
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
  --run-name doua_1845_5175 `
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
  --run-name doua_1845_5175 `
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
`workdir/runs/doua_1845_5175/evaluation/`.

## Main Options

```text
--workspace                 Working root. Default: workdir
--run-name                  Output run name.
--download-missing-inventories
                            Download missing inventory JSON files when URLs are provided.
--nuage-json-url            Optional URL used to download the LiDAR inventory JSON.
--ortho-json-url            Optional URL used to download the orthophoto inventory JSON.
--xmin-start/--xmin-end     Bounding box selection in Lambert-93.
--ymin-start/--ymin-end     Bounding box selection in Lambert-93.
--skip-download             Reuse existing LiDAR/ortho downloads.
--reuse-derived-rasters    Reuse existing LiDAR/ortho mosaics for the run instead of rebuilding them.
--skip-flair                Reuse an existing FLAIR probability raster.
--skip-reweight             Skip reweighting and use an existing vegetation raster.
--flair-probability-raster  Existing FLAIR probability GeoTIFF.
--reweighted-raster         Existing reweighted vegetation GeoTIFF.
--model-path                Local model path. If omitted, downloaded from Hugging Face.
--weights-json              Optional JSON object for custom probability-band weights.
--mapping-json              Optional JSON object for custom class remapping after reweighting.
--ortho-source-resolution   Optional source orthophoto pixel size in meters. Default: inferred from raster metadata.
--ortho-output-resolution   Output orthophoto pixel size in meters. Alias: --ortho-target-resolution. Default: 0.8.
--use-gpu                   Ask FLAIR-HUB to run on GPU and use CUDA for evaluation when available.
--run-legacy-fusion         Also produce the legacy LiDAR vegetation and legacy LiDAR+FLAIR fusion outputs.
--apply-lidar-correction    Correct the LiDAR MNS mosaic before the legacy branch.
--reference-raster          Compute a confusion matrix and summary metrics against a reference raster.
--metadata-name             Name of the run manifest JSON written under the run directory.
```

Full help:

```powershell
docker compose run --rm vegetalisation python run_workflow.py --help
```

## Outputs

For a run named `doua_1845_5175`, outputs are written under:

- `workdir/runs/doua_1845_5175/ortho/mosaic/orthophoto_mosaic_08m.tif`
- `workdir/runs/doua_1845_5175/lidar/mosaic/lidar_height_08m.tif`
- `workdir/runs/doua_1845_5175/lidar/mosaic/lidar_class_08m.tif`
- `workdir/runs/doua_1845_5175/flair/runtime_config.yaml`
- `workdir/runs/doua_1845_5175/run_metadata.json`
- `workdir/runs/doua_1845_5175/flair/probabilities/...`
- `workdir/runs/doua_1845_5175/flair/flair_vegetation_reweighted.tif`
- `workdir/runs/doua_1845_5175/fusion/final_fused_08m.tif`
- `workdir/runs/doua_1845_5175/fusion/legacy_lidar_classes.tif`
- `workdir/runs/doua_1845_5175/fusion/legacy_fused_lidar_flair.tif`
- `workdir/runs/doua_1845_5175/evaluation/confusion_matrix_percent.png`
- `workdir/runs/doua_1845_5175/evaluation/metrics_summary.json`
- `workdir/runs/doua_1845_5175/evaluation/metrics_log.txt`

## Notes

- The workflow uses the official FLAIR-HUB entry point `flairhub_zonal`, as documented in the FLAIR-HUB GitHub README.
- The model download defaults to `IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet` on Hugging Face.
- The default model revision is pinned in [`run_workflow.py`](run_workflow.py) so the workflow stays stable over time.
- Every workflow run now writes a reproducibility manifest with CLI arguments, resolved paths, model pinning, package versions, and platform metadata.
- Evaluation is only run if you pass `--reference-raster`.

## Configuration Overrides

The probability reweighting step is no longer hardcoded. You can override both the class weights and the
output remapping with JSON files:

```json
{
  "8": 1.0,
  "12": 1.0,
  "13": 1.0,
  "14": 1.0
}
```

```json
{
  "8": 0,
  "14": 1,
  "12": 2,
  "13": 3
}
```

Use them with:

```powershell
docker compose run --rm vegetalisation python run_workflow.py `
  --run-name doua_1845_5175 `
  --nuage-json workdir/inputs/nuage.json `
  --ortho-json workdir/inputs/ortho.json `
  --xmin-start 1845000 `
  --xmin-end 1846000 `
  --ymin-start 5175000 `
  --ymin-end 5176000 `
```

### Reuse Existing Inference Inputs

If you only changed the vegetation strata definitions in [`configs/configs.yml`](configs/configs.yml)
or a similar post-processing setting, you can now skip rebuilding the derived LiDAR and orthophoto
rasters and also skip FLAIR inference:

```powershell
docker compose run --rm vegetalisation python run_workflow.py `
  --run-name doua_1845_5175 `
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
