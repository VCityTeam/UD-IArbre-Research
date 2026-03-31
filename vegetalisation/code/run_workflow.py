from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
import yaml
from huggingface_hub import hf_hub_download

from workflow_utils import (
    build_run_manifest,
    collect_runtime_versions,
    validate_bbox,
    validate_positive_number,
    write_json,
)

DEFAULT_WORKSPACE = Path("workdir")
DEFAULT_CONFIG = Path("configs/config_zonal_detection.yaml")
DEFAULT_MODEL_REPO = "IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet"
DEFAULT_MODEL_FILENAME = "FLAIR-HUB_LC-A_RGB_swinlarge-upernet.safetensors"
DEFAULT_MODEL_REVISION = "336bd84"
DEFAULT_FLAIR_HUB_REPO_URL = "https://github.com/IGNF/FLAIR-HUB.git"
INVENTORY_DOWNLOAD_TIMEOUT_SECONDS = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the reproducible LiDAR + orthophoto + FLAIR-HUB vegetation workflow."
    )
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--config-template", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--nuage-json", type=Path)
    parser.add_argument("--ortho-json", type=Path)
    parser.add_argument(
        "--download-missing-inventories",
        action="store_true",
        help="Download missing inventory JSON files when a source URL is provided.",
    )
    parser.add_argument(
        "--nuage-json-url",
        help="Optional URL used to download the LiDAR inventory JSON if it is missing.",
    )
    parser.add_argument(
        "--ortho-json-url",
        help="Optional URL used to download the orthophoto inventory JSON if it is missing.",
    )
    parser.add_argument("--xmin-start", type=int, required=True)
    parser.add_argument("--xmin-end", type=int, required=True)
    parser.add_argument("--ymin-start", type=int, required=True)
    parser.add_argument("--ymin-end", type=int, required=True)
    parser.add_argument("--resolution", type=float, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--num-worker", type=int, default=0)
    parser.add_argument("--img-pixels-detection", type=int, default=512)
    parser.add_argument("--margin", type=int, default=128)
    parser.add_argument(
        "--ortho-source-resolution",
        type=float,
        default=None,
        help=(
            "Optional source orthophoto pixel size in meters. "
            "When omitted, ortho_extract.py infers it from the raster metadata."
        ),
    )
    parser.add_argument(
        "--ortho-target-resolution",
        "--ortho-output-resolution",
        dest="ortho_output_resolution",
        type=float,
        default=0.2,
        help="Output orthophoto pixel size in meters.",
    )
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-flair", action="store_true")
    parser.add_argument("--skip-reweight", action="store_true")
    parser.add_argument("--use-gpu", action="store_true")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--model-repo", default=DEFAULT_MODEL_REPO)
    parser.add_argument("--model-filename", default=DEFAULT_MODEL_FILENAME)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument(
        "--flair-hub-ref",
        default=os.environ.get("FLAIR_HUB_REF", "main"),
        help="Git ref used when downloading the full FLAIR-HUB source tree for zonal inference.",
    )
    parser.add_argument(
        "--flair-hub-repo-url",
        default=DEFAULT_FLAIR_HUB_REPO_URL,
        help="Git repository used to fetch the full FLAIR-HUB source tree.",
    )
    parser.add_argument("--weights-json", type=Path)
    parser.add_argument("--mapping-json", type=Path)
    parser.add_argument("--flair-probability-raster", type=Path)
    parser.add_argument("--reweighted-raster", type=Path)
    parser.add_argument("--keep-class-lidar1", action="store_true")
    parser.add_argument("--modify-flair", action="store_true")
    parser.add_argument("--flair-only-herbaceous", action="store_true")
    parser.add_argument(
        "--apply-lidar-correction",
        action="store_true",
        help="Apply NaN correction to the LiDAR MNS mosaic before the legacy LiDAR vegetation derivation.",
    )
    parser.add_argument(
        "--run-legacy-fusion",
        action="store_true",
        help="Also run the legacy LiDAR vegetation derivation and the historical LiDAR+Flair fusion.",
    )
    parser.add_argument(
        "--reference-raster",
        type=Path,
        help="Optional reference raster used to generate a confusion matrix and summary metrics.",
    )
    parser.add_argument(
        "--metadata-name",
        default="run_metadata.json",
        help="Run metadata filename written under the run directory for reproducibility.",
    )
    return parser.parse_args()


def run_command(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    printable = " ".join(command)
    print(f"\n>>> {printable}")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def ensure_flair_hub_source(
    source_root: Path,
    *,
    repo_url: str,
    ref: str,
) -> Path:
    repo_dir = source_root / "FLAIR-HUB"
    src_dir = repo_dir / "src"

    if (src_dir / "flair_hub").exists() and (src_dir / "flair_zonal_detection").exists():
        return src_dir

    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    source_root.mkdir(parents=True, exist_ok=True)
    run_command(["git", "clone", repo_url, str(repo_dir)])
    run_command(["git", "checkout", ref], cwd=repo_dir)

    if not (src_dir / "flair_hub").exists():
        raise FileNotFoundError(f"Missing flair_hub package under cloned source tree: {src_dir}")
    if not (src_dir / "flair_zonal_detection").exists():
        raise FileNotFoundError(
            f"Missing flair_zonal_detection package under cloned source tree: {src_dir}"
        )
    return src_dir


def find_latest_raster(folder: Path) -> Path:
    candidates = sorted(
        list(folder.rglob("*.tif")) + list(folder.rglob("*.tiff")),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(f"No GeoTIFF found under: {folder}")
    return candidates[-1]


def ensure_inventory_file(
    path: Path,
    *,
    label: str,
    url: str | None,
    download_missing: bool,
) -> Path:
    if path.exists():
        return path

    if not (download_missing and url):
        raise FileNotFoundError(f"Missing {label}: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {label}: {url}")
    with requests.get(url, timeout=INVENTORY_DOWNLOAD_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        path.write_bytes(response.content)
    print(f"Saved {label} to: {path}")
    return path


def resolve_model(args: argparse.Namespace, model_dir: Path) -> Path:
    if args.model_path:
        model_path = args.model_path.resolve()
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        return model_path

    model_dir.mkdir(parents=True, exist_ok=True)
    print(
        "Downloading model from Hugging Face:",
        f"{args.model_repo}@{args.model_revision}/{args.model_filename}",
    )
    downloaded = hf_hub_download(
        repo_id=args.model_repo,
        filename=args.model_filename,
        revision=args.model_revision,
        local_dir=model_dir,
        local_dir_use_symlinks=False,
    )
    return Path(downloaded)


def write_runtime_config(
    template_path: Path,
    output_path: Path,
    *,
    model_path: Path,
    orthophoto_mosaic: Path,
    flair_output_dir: Path,
    run_name: str,
    use_gpu: bool,
    batch_size: int,
    num_worker: int,
    img_pixels_detection: int,
    margin: int,
    output_px_meters: float,
) -> None:
    with template_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    config["output_path"] = flair_output_dir.as_posix()
    config["output_name"] = run_name
    config["output_type"] = "class_prob"
    config["model_weights"] = model_path.as_posix()
    config["use_gpu"] = use_gpu
    config["batch_size"] = batch_size
    config["num_worker"] = num_worker
    config["img_pixels_detection"] = img_pixels_detection
    config["margin"] = margin
    config["output_px_meters"] = output_px_meters
    config["modalities"]["AERIAL_RGBI"]["input_img_path"] = orthophoto_mosaic.as_posix()
    config["modalities"]["AERIAL_RGBI"]["channels"] = [1, 2, 3]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)


def stage_matching_tiles(source_dir: Path, target_dir: Path, pattern: str) -> int:
    target_dir.mkdir(parents=True, exist_ok=True)
    for existing_file in target_dir.glob("*.tif"):
        existing_file.unlink()

    count = 0
    for tif_path in sorted(source_dir.glob(pattern)):
        shutil.copy2(tif_path, target_dir / tif_path.name)
        count += 1
    return count


def main() -> None:
    args = parse_args()
    validate_bbox(args.xmin_start, args.xmin_end, args.ymin_start, args.ymin_end)
    validate_positive_number(args.resolution, "resolution")
    if args.ortho_source_resolution is not None:
        validate_positive_number(args.ortho_source_resolution, "ortho_source_resolution")
    validate_positive_number(args.ortho_output_resolution, "ortho_output_resolution")
    if args.batch_size <= 0:
        raise ValueError("batch_size must be strictly positive.")
    if args.num_worker < 0:
        raise ValueError("num_worker must be greater than or equal to 0.")
    if args.img_pixels_detection <= 0:
        raise ValueError("img_pixels_detection must be strictly positive.")
    if args.margin < 0:
        raise ValueError("margin must be greater than or equal to 0.")

    code_dir = Path(__file__).resolve().parent
    workspace = args.workspace.resolve()
    run_dir = workspace / "runs" / args.run_name
    inputs_dir = workspace / "inputs"
    model_dir = workspace / "models"
    flair_hub_source_root = workspace / ".deps"

    laz_dir = run_dir / "lidar" / "laz_tiles"
    lidar_tiles_dir = run_dir / "lidar" / "tiles"
    lidar_height_tiles_dir = lidar_tiles_dir / "heights"
    lidar_class_tiles_dir = lidar_tiles_dir / "class"
    lidar_mns_mnt_tiles_dir = lidar_tiles_dir / "mns_mnt"
    lidar_mosaic_dir = run_dir / "lidar" / "mosaic"

    ortho_temp_dir = run_dir / "ortho" / "temp_5cm"
    ortho_tiles_dir = run_dir / "ortho" / "tiles_08m"
    ortho_mosaic_dir = run_dir / "ortho" / "mosaic"

    flair_dir = run_dir / "flair"
    flair_probability_dir = flair_dir / "probabilities"
    fusion_dir = run_dir / "fusion"

    lidar_height_mosaic = lidar_mosaic_dir / "lidar_height_08m.tif"
    lidar_class_mosaic = lidar_mosaic_dir / "lidar_class_08m.tif"
    lidar_mns_mosaic = lidar_mosaic_dir / "lidar_mns_08m.tif"
    lidar_mnt_mosaic = lidar_mosaic_dir / "lidar_mnt_08m.tif"
    lidar_mns_corrected = lidar_mosaic_dir / "lidar_mns_08m_corrected.tif"
    orthophoto_mosaic = ortho_mosaic_dir / "orthophoto_mosaic_08m.tif"
    runtime_config = flair_dir / "runtime_config.yaml"
    reweighted_raster = args.reweighted_raster or (flair_dir / "flair_vegetation_reweighted.tif")
    legacy_lidar_height = fusion_dir / "legacy_lidar_height.tif"
    legacy_lidar_classes = fusion_dir / "legacy_lidar_classes.tif"
    legacy_fused_raster = fusion_dir / "legacy_fused_lidar_flair.tif"
    evaluation_dir = run_dir / "evaluation"
    metadata_path = run_dir / args.metadata_name

    nuage_json = (args.nuage_json or (inputs_dir / "nuage.json")).resolve()
    ortho_json = (args.ortho_json or (inputs_dir / "ortho.json")).resolve()
    config_template = (code_dir / args.config_template).resolve()
    weights_json = args.weights_json.resolve() if args.weights_json else None
    mapping_json = args.mapping_json.resolve() if args.mapping_json else None

    for folder in [
        workspace,
        run_dir,
        inputs_dir,
        laz_dir,
        lidar_height_tiles_dir,
        lidar_class_tiles_dir,
        lidar_mns_mnt_tiles_dir,
        lidar_mosaic_dir,
        ortho_temp_dir,
        ortho_tiles_dir,
        ortho_mosaic_dir,
        flair_dir,
        flair_probability_dir,
        fusion_dir,
        evaluation_dir,
    ]:
        folder.mkdir(parents=True, exist_ok=True)

    if not config_template.exists():
        raise FileNotFoundError(f"FLAIR-HUB config template not found: {config_template}")

    if not args.skip_download:
        ensure_inventory_file(
            nuage_json,
            label="LiDAR inventory",
            url=args.nuage_json_url,
            download_missing=args.download_missing_inventories,
        )
        ensure_inventory_file(
            ortho_json,
            label="orthophoto inventory",
            url=args.ortho_json_url,
            download_missing=args.download_missing_inventories,
        )

        run_command(
            [
                sys.executable,
                "extract_nuage.py",
                "--json-file",
                str(nuage_json),
                "--output-dir",
                str(laz_dir),
                "--xmin-start",
                str(args.xmin_start),
                "--xmin-end",
                str(args.xmin_end),
                "--ymin-start",
                str(args.ymin_start),
                "--ymin-end",
                str(args.ymin_end),
            ],
            cwd=code_dir,
        )

        ortho_extract_command = [
            sys.executable,
            "ortho_extract.py",
            "--json-file",
            str(ortho_json),
            "--output-dir",
            str(ortho_tiles_dir),
            "--temp-dir",
            str(ortho_temp_dir),
            "--xmin-start",
            str(args.xmin_start),
            "--xmin-end",
            str(args.xmin_end),
            "--ymin-start",
            str(args.ymin_start),
            "--ymin-end",
            str(args.ymin_end),
            "--output-resolution",
            str(args.ortho_output_resolution),
        ]
        if args.ortho_source_resolution is not None:
            ortho_extract_command.extend(
                ["--source-resolution", str(args.ortho_source_resolution)]
            )
        run_command(ortho_extract_command, cwd=code_dir)

    run_command(
        [
            sys.executable,
            "fusion_nuage.py",
            "--laz-folder",
            str(laz_dir),
            "--height-folder",
            str(lidar_height_tiles_dir),
            "--class-folder",
            str(lidar_class_tiles_dir),
            "--mns-mnt-folder",
            str(lidar_mns_mnt_tiles_dir),
            "--resolution",
            str(args.resolution),
        ],
        cwd=code_dir,
    )

    run_command(
        [
            sys.executable,
            "ortho_fusion.py",
            "--input-dir",
            str(lidar_height_tiles_dir),
            "--output-file",
            str(lidar_height_mosaic),
        ],
        cwd=code_dir,
    )

    mns_tiles_only_dir = lidar_mosaic_dir / "mns_tiles_only"
    mnt_tiles_only_dir = lidar_mosaic_dir / "mnt_tiles_only"
    mns_count = stage_matching_tiles(lidar_mns_mnt_tiles_dir, mns_tiles_only_dir, "*_mns.tif")
    mnt_count = stage_matching_tiles(lidar_mns_mnt_tiles_dir, mnt_tiles_only_dir, "*_mnt.tif")

    if mns_count:
        run_command(
            [
                sys.executable,
                "ortho_fusion.py",
                "--input-dir",
                str(mns_tiles_only_dir),
                "--output-file",
                str(lidar_mns_mosaic),
            ],
            cwd=code_dir,
        )
    else:
        raise FileNotFoundError(f"No MNS tiles found in: {lidar_mns_mnt_tiles_dir}")
    if mnt_count:
        run_command(
            [
                sys.executable,
                "ortho_fusion.py",
                "--input-dir",
                str(mnt_tiles_only_dir),
                "--output-file",
                str(lidar_mnt_mosaic),
            ],
            cwd=code_dir,
        )
    else:
        raise FileNotFoundError(f"No MNT tiles found in: {lidar_mns_mnt_tiles_dir}")

    run_command(
        [
            sys.executable,
            "ortho_fusion.py",
            "--input-dir",
            str(lidar_class_tiles_dir),
            "--output-file",
            str(lidar_class_mosaic),
        ],
        cwd=code_dir,
    )

    run_command(
        [
            sys.executable,
            "ortho_fusion.py",
            "--input-dir",
            str(ortho_tiles_dir),
            "--output-file",
            str(orthophoto_mosaic),
        ],
        cwd=code_dir,
    )

    probability_raster = args.flair_probability_raster
    if not args.skip_flair:
        model_path = resolve_model(args, model_dir)
        flair_hub_src = ensure_flair_hub_source(
            flair_hub_source_root,
            repo_url=args.flair_hub_repo_url,
            ref=args.flair_hub_ref,
        )
        write_runtime_config(
            config_template,
            runtime_config,
            model_path=model_path,
            orthophoto_mosaic=orthophoto_mosaic,
            flair_output_dir=flair_probability_dir,
            run_name=args.run_name,
            use_gpu=args.use_gpu,
            batch_size=args.batch_size,
            num_worker=args.num_worker,
            img_pixels_detection=args.img_pixels_detection,
            margin=args.margin,
            output_px_meters=args.resolution,
        )
        flair_env = os.environ.copy()
        existing_pythonpath = flair_env.get("PYTHONPATH")
        flair_env["PYTHONPATH"] = (
            f"{flair_hub_src}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else str(flair_hub_src)
        )
        run_command(
            [sys.executable, "-m", "flair_zonal_detection.main", "--config", str(runtime_config)],
            cwd=code_dir,
            env=flair_env,
        )
        probability_raster = find_latest_raster(flair_probability_dir)
    elif probability_raster is None:
        probability_raster = find_latest_raster(flair_probability_dir)

    if not args.skip_reweight:
        reweight_command = [
            sys.executable,
            "flair_probs_reweight.py",
            "--input",
            str(probability_raster),
            "--output",
            str(reweighted_raster),
        ]
        if weights_json:
            reweight_command.extend(["--weights-json", str(weights_json)])
        if mapping_json:
            reweight_command.extend(["--mapping-json", str(mapping_json)])
        run_command(reweight_command, cwd=code_dir)
    elif not reweighted_raster.exists():
        raise FileNotFoundError(f"Missing reweighted raster: {reweighted_raster}")

    fusion_command = [
        sys.executable,
        "fusion_lidar_flair.py",
        "--class-map",
        str(lidar_class_mosaic),
        "--height-map",
        str(lidar_height_mosaic),
        "--veg-mask",
        str(reweighted_raster),
        "--second-map",
        str(reweighted_raster),
        "--out-dir",
        str(fusion_dir),
    ]
    if args.modify_flair:
        fusion_command.append("--modify-flair")
    if args.keep_class_lidar1:
        fusion_command.append("--keep-class-lidar1")
    if args.flair_only_herbaceous:
        fusion_command.append("--flair-only-herbaceous")

    run_command(fusion_command, cwd=code_dir)

    if args.run_legacy_fusion:
        legacy_mns_input = lidar_mns_mosaic
        if args.apply_lidar_correction:
            run_command(
                [
                    sys.executable,
                    "lidarCorrection.py",
                    "--input",
                    str(lidar_mns_mosaic),
                    "--output",
                    str(lidar_mns_corrected),
                ],
                cwd=code_dir,
            )
            legacy_mns_input = lidar_mns_corrected

        run_command(
            [
                sys.executable,
                "calculateVegetationFromLidar.py",
                "--lidar-max-height",
                str(legacy_mns_input),
                "--lidar-min-height",
                str(lidar_mnt_mosaic),
                "--lidar-class",
                str(lidar_class_mosaic),
                "--mask-raster",
                str(reweighted_raster),
                "--height-output",
                str(legacy_lidar_height),
                "--class-output",
                str(legacy_lidar_classes),
            ],
            cwd=code_dir,
        )

        run_command(
            [
                sys.executable,
                "fusionBetweenFlairAndLidar.py",
                "--lidar-raster",
                str(legacy_lidar_classes),
                "--flair-raster",
                str(reweighted_raster),
                "--output",
                str(legacy_fused_raster),
            ],
            cwd=code_dir,
        )

    if args.reference_raster:
        prediction_for_eval = (
            legacy_fused_raster if args.run_legacy_fusion else fusion_dir / "final_fused_08m.tif"
        )
        evaluation_command = [
            sys.executable,
            "confusionMatrix.py",
            "--reference",
            str(args.reference_raster.resolve()),
            "--prediction",
            str(prediction_for_eval),
            "--output-dir",
            str(evaluation_dir),
        ]
        if args.use_gpu:
            evaluation_command.append("--use-gpu")
        run_command(evaluation_command, cwd=code_dir)

    print("\nWorkflow completed successfully.")
    print(f"Orthophoto mosaic: {orthophoto_mosaic}")
    print(f"LiDAR class mosaic: {lidar_class_mosaic}")
    print(f"LiDAR height mosaic: {lidar_height_mosaic}")
    print(f"LiDAR MNS mosaic: {lidar_mns_mosaic}")
    print(f"LiDAR MNT mosaic: {lidar_mnt_mosaic}")
    print(f"FLAIR probability raster: {probability_raster}")
    print(f"Reweighted vegetation raster: {reweighted_raster}")
    print(f"Final fusion directory: {fusion_dir}")
    if args.run_legacy_fusion:
        print(f"Legacy LiDAR classes: {legacy_lidar_classes}")
        print(f"Legacy fused raster: {legacy_fused_raster}")
    if args.reference_raster:
        print(f"Evaluation outputs: {evaluation_dir}")

    manifest = build_run_manifest(
        command=sys.argv,
        args={
            key: (value.as_posix() if isinstance(value, Path) else value)
            for key, value in vars(args).items()
        },
        extra={
            "paths": {
                "workspace": workspace.as_posix(),
                "run_dir": run_dir.as_posix(),
                "runtime_config": runtime_config.as_posix(),
                "reweighted_raster": reweighted_raster.as_posix(),
            },
            "model": {
                "repo": args.model_repo,
                "filename": args.model_filename,
                "revision": args.model_revision,
            },
            "package_versions": collect_runtime_versions(
                [
                    "huggingface_hub",
                    "laspy",
                    "matplotlib",
                    "numpy",
                    "PyYAML",
                    "rasterio",
                    "requests",
                    "scikit-learn",
                    "seaborn",
                    "tqdm",
                ]
            ),
        },
    )
    write_json(manifest, metadata_path)
    print(f"Run metadata: {metadata_path}")


if __name__ == "__main__":
    main()
