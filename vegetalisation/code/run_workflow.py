from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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
DEFAULT_EXPERIMENT_CONFIG_DIR = Path("configs/baseline")
DEFAULT_CONFIG = DEFAULT_EXPERIMENT_CONFIG_DIR / "config_zonal_detection.yaml"
DEFAULT_MATRIX_CONFIG = DEFAULT_EXPERIMENT_CONFIG_DIR / "configs.yml"
DEFAULT_MODEL_REPO = "IGNF/FLAIR-HUB_LC-A_RGB_swinlarge-upernet"
DEFAULT_MODEL_FILENAME = "FLAIR-HUB_LC-A_RGB_swinlarge-upernet.safetensors"
DEFAULT_MODEL_REVISION = "336bd84"
DEFAULT_FLAIR_HUB_REPO_URL = "https://github.com/IGNF/FLAIR-HUB.git"
INVENTORY_DOWNLOAD_TIMEOUT_SECONDS = 60

try:
    import torch
except ImportError:  # pragma: no cover - optional dependency outside runtime images
    torch = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the reproducible LiDAR + orthophoto + FLAIR-HUB vegetation workflow."
    )
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--run-name", required=True)
    parser.add_argument(
        "--experiment-config-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory containing both config_zonal_detection.yaml and configs.yml "
            "for a self-contained experiment."
        ),
    )
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
    parser.add_argument("--resolution", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--num-worker", type=int, default=None)
    parser.add_argument("--img-pixels-detection", type=int, default=None)
    parser.add_argument("--margin", type=int, default=None)
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
        default=None,
        help="Output orthophoto pixel size in meters.",
    )
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument(
        "--reuse-derived-rasters",
        action="store_true",
        help=(
            "Reuse existing LiDAR and orthophoto derived rasters for the run instead of "
            "rebuilding them. Useful when only post-processing settings changed."
        ),
    )
    parser.add_argument("--skip-flair", action="store_true")
    parser.add_argument("--skip-reweight", action="store_true")
    parser.add_argument("--use-gpu", action=argparse.BooleanOptionalAction, default=None)
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
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)
    parser.add_argument("--flair-probability-raster", type=Path)
    parser.add_argument("--reweighted-raster", type=Path)
    parser.add_argument(
        "--keep-class-lidar1", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument("--modify-flair", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--flair-only-herbaceous", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--apply-lidar-correction",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Apply NaN correction to the LiDAR MNS mosaic before the legacy LiDAR vegetation derivation.",
    )
    parser.add_argument(
        "--run-legacy-fusion",
        action=argparse.BooleanOptionalAction,
        default=None,
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


def require_existing_file(path: Path, *, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


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


def load_yaml_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid YAML config: {path}")
    return config


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


def ensure_cuda_available_if_requested(use_gpu: bool) -> None:
    if not use_gpu:
        return
    if torch is None:
        raise RuntimeError(
            "GPU inference was requested, but PyTorch is not installed in this environment. "
            "Disable GPU with `--no-use-gpu` or set `use_gpu: false` in the experiment "
            "config_zonal_detection.yaml."
        )
    if not torch.cuda.is_available():
        raise RuntimeError(
            "GPU inference was requested, but CUDA is unavailable in this environment. "
            "Disable GPU with `--no-use-gpu` or set `use_gpu: false` in the experiment "
            "config_zonal_detection.yaml."
        )


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


def resolve_workflow_settings(
    *,
    runtime_template_config: dict[str, Any],
    matrix_config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    workflow_config = matrix_config.get("workflow", {})
    orthophoto_config = workflow_config.get("orthophoto", {})
    fusion_config = workflow_config.get("fusion", {})
    legacy_config = workflow_config.get("legacy", {})

    def choose(cli_value: Any, config_value: Any, fallback: Any) -> Any:
        if cli_value is not None:
            return cli_value
        if config_value is not None:
            return config_value
        return fallback

    settings = {
        "resolution": float(
            choose(args.resolution, runtime_template_config.get("output_px_meters"), 1.0)
        ),
        "batch_size": int(choose(args.batch_size, runtime_template_config.get("batch_size"), 1)),
        "num_worker": int(choose(args.num_worker, runtime_template_config.get("num_worker"), 0)),
        "img_pixels_detection": int(
            choose(args.img_pixels_detection, runtime_template_config.get("img_pixels_detection"), 512)
        ),
        "margin": int(choose(args.margin, runtime_template_config.get("margin"), 128)),
        "use_gpu": bool(choose(args.use_gpu, runtime_template_config.get("use_gpu"), False)),
        "ortho_source_resolution": choose(
            args.ortho_source_resolution, orthophoto_config.get("source_resolution"), None
        ),
        "ortho_output_resolution": float(
            choose(args.ortho_output_resolution, orthophoto_config.get("output_resolution"), 0.2)
        ),
        "modify_flair": bool(choose(args.modify_flair, fusion_config.get("modify_flair"), False)),
        "keep_class_lidar1": bool(
            choose(args.keep_class_lidar1, fusion_config.get("keep_class_lidar1"), False)
        ),
        "flair_only_herbaceous": bool(
            choose(
                args.flair_only_herbaceous,
                fusion_config.get("flair_only_herbaceous"),
                False,
            )
        ),
        "run_legacy_fusion": bool(
            choose(args.run_legacy_fusion, legacy_config.get("run_legacy_fusion"), False)
        ),
        "apply_lidar_correction": bool(
            choose(args.apply_lidar_correction, legacy_config.get("apply_lidar_correction"), False)
        ),
    }
    if settings["ortho_source_resolution"] is not None:
        settings["ortho_source_resolution"] = float(settings["ortho_source_resolution"])
    return settings


def resolve_experiment_config_paths(
    code_dir: Path,
    *,
    experiment_config_dir: Path | None,
    config_template: Path,
    matrix_config: Path,
) -> tuple[Path, Path, Path | None]:
    resolved_experiment_dir = None
    resolved_config_template = config_template
    resolved_matrix_config = matrix_config

    if experiment_config_dir is not None:
        resolved_experiment_dir = (
            experiment_config_dir
            if experiment_config_dir.is_absolute()
            else (code_dir / experiment_config_dir)
        ).resolve()

        if config_template == DEFAULT_CONFIG:
            resolved_config_template = resolved_experiment_dir / "config_zonal_detection.yaml"
        if matrix_config == DEFAULT_MATRIX_CONFIG:
            resolved_matrix_config = resolved_experiment_dir / "configs.yml"

    resolved_config_template = (
        resolved_config_template
        if resolved_config_template.is_absolute()
        else (code_dir / resolved_config_template)
    ).resolve()
    resolved_matrix_config = (
        resolved_matrix_config
        if resolved_matrix_config.is_absolute()
        else (code_dir / resolved_matrix_config)
    ).resolve()

    return resolved_config_template, resolved_matrix_config, resolved_experiment_dir


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
    ortho_tiles_dir = run_dir / "ortho" / "tiles"
    ortho_mosaic_dir = run_dir / "ortho" / "mosaic"

    flair_dir = run_dir / "flair"
    flair_probability_dir = flair_dir / "probabilities"
    fusion_dir = run_dir / "fusion"

    lidar_height_mosaic = lidar_mosaic_dir / "lidar_height.tif"
    lidar_class_mosaic = lidar_mosaic_dir / "lidar_class.tif"
    lidar_mns_mosaic = lidar_mosaic_dir / "lidar_mns.tif"
    lidar_mnt_mosaic = lidar_mosaic_dir / "lidar_mnt.tif"
    lidar_mns_corrected = lidar_mosaic_dir / "lidar_mns_corrected.tif"
    orthophoto_mosaic = ortho_mosaic_dir / "orthophoto_mosaic.tif"
    runtime_config = flair_dir / "runtime_config.yaml"
    reweighted_raster = args.reweighted_raster or (flair_dir / "flair_vegetation_reweighted.tif")
    legacy_lidar_height = fusion_dir / "legacy_lidar_height.tif"
    legacy_lidar_classes = fusion_dir / "legacy_lidar_classes.tif"
    legacy_fused_raster = fusion_dir / "legacy_fused_lidar_flair.tif"
    evaluation_dir = run_dir / "evaluation"
    metadata_path = run_dir / args.metadata_name

    nuage_json = (args.nuage_json or (inputs_dir / "nuage.json")).resolve()
    ortho_json = (args.ortho_json or (inputs_dir / "ortho.json")).resolve()
    config_template, matrix_config_path, experiment_config_dir = resolve_experiment_config_paths(
        code_dir,
        experiment_config_dir=args.experiment_config_dir,
        config_template=args.config_template,
        matrix_config=args.matrix_config,
    )
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
    if not matrix_config_path.exists():
        raise FileNotFoundError(f"Matrix config not found: {matrix_config_path}")

    runtime_template_config = load_yaml_config(config_template)
    matrix_config = load_yaml_config(matrix_config_path)
    workflow_settings = resolve_workflow_settings(
        runtime_template_config=runtime_template_config,
        matrix_config=matrix_config,
        args=args,
    )

    validate_positive_number(workflow_settings["resolution"], "resolution")
    if workflow_settings["ortho_source_resolution"] is not None:
        validate_positive_number(
            workflow_settings["ortho_source_resolution"], "ortho_source_resolution"
        )
    validate_positive_number(workflow_settings["ortho_output_resolution"], "ortho_output_resolution")
    if workflow_settings["batch_size"] <= 0:
        raise ValueError("batch_size must be strictly positive.")
    if workflow_settings["num_worker"] < 0:
        raise ValueError("num_worker must be greater than or equal to 0.")
    if workflow_settings["img_pixels_detection"] <= 0:
        raise ValueError("img_pixels_detection must be strictly positive.")
    if workflow_settings["margin"] < 0:
        raise ValueError("margin must be greater than or equal to 0.")
    ensure_cuda_available_if_requested(workflow_settings["use_gpu"])

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
            str(workflow_settings["ortho_output_resolution"]),
        ]
        if workflow_settings["ortho_source_resolution"] is not None:
            ortho_extract_command.extend(
                ["--source-resolution", str(workflow_settings["ortho_source_resolution"])]
            )
        run_command(ortho_extract_command, cwd=code_dir)

    if args.reuse_derived_rasters:
        require_existing_file(lidar_height_mosaic, label="LiDAR height mosaic")
        require_existing_file(lidar_class_mosaic, label="LiDAR class mosaic")
        require_existing_file(lidar_mns_mosaic, label="LiDAR MNS mosaic")
        require_existing_file(lidar_mnt_mosaic, label="LiDAR MNT mosaic")
        require_existing_file(orthophoto_mosaic, label="orthophoto mosaic")
    else:
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
                str(workflow_settings["resolution"]),
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
            use_gpu=workflow_settings["use_gpu"],
            batch_size=workflow_settings["batch_size"],
            num_worker=workflow_settings["num_worker"],
            img_pixels_detection=workflow_settings["img_pixels_detection"],
            margin=workflow_settings["margin"],
            output_px_meters=workflow_settings["resolution"],
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
        try:
            probability_raster = find_latest_raster(flair_probability_dir)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "FLAIR inference finished without producing a probability GeoTIFF. "
                "Check the inference logs above. A common cause is requesting GPU inference "
                "in a CPU-only environment."
            ) from exc
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
            "--matrix-config",
            str(matrix_config_path),
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
        "--matrix-config",
        str(matrix_config_path),
    ]
    if workflow_settings["modify_flair"]:
        fusion_command.append("--modify-flair")
    if workflow_settings["keep_class_lidar1"]:
        fusion_command.append("--keep-class-lidar1")
    if workflow_settings["flair_only_herbaceous"]:
        fusion_command.append("--flair-only-herbaceous")

    run_command(fusion_command, cwd=code_dir)

    if workflow_settings["run_legacy_fusion"]:
        legacy_mns_input = lidar_mns_mosaic
        if workflow_settings["apply_lidar_correction"]:
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
            legacy_fused_raster if workflow_settings["run_legacy_fusion"] else fusion_dir / "final_fused.tif"
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
            "--matrix-config",
            str(matrix_config_path),
        ]
        if workflow_settings["use_gpu"]:
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
    if workflow_settings["run_legacy_fusion"]:
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
                "experiment_config_dir": (
                    experiment_config_dir.as_posix() if experiment_config_dir is not None else None
                ),
                "config_template": config_template.as_posix(),
                "matrix_config": matrix_config_path.as_posix(),
                "runtime_config": runtime_config.as_posix(),
                "reweighted_raster": reweighted_raster.as_posix(),
            },
            "resolved_workflow": workflow_settings,
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
