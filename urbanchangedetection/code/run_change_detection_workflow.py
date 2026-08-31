from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from compute_band_stats import compute_band_stats
from workflow_utils import (
    build_run_manifest,
    collect_runtime_versions,
    validate_bbox,
    validate_positive_number,
    write_json,
)

DEFAULT_CHANNELS_BY_BAND_MODE = {
    "rgb": [1, 2, 3],
    # Idem run_workflow.py: le raster IR Grand Lyon est déjà un composite 3 bandes
    # dans l'ordre attendu, pas besoin de réordonner ni de supposer une 4ème bande.
    "ir": [1, 2, 3],
}

# On réutilise directement les briques de run_workflow.py plutôt que de les
# dupliquer : ce script se contente d'orchestrer "run_workflow" pour chaque
# année, puis d'enchaîner sur le script de détection de changement.
from run_workflow import (
    DEFAULT_CONFIG,
    DEFAULT_FLAIR_HUB_REPO_URL,
    DEFAULT_MATRIX_CONFIG,
    ensure_cuda_available_if_requested,
    ensure_flair_hub_source,
    ensure_inventory_file,
    find_latest_raster,
    load_yaml_config,
    require_existing_file,
    resolve_experiment_config_paths,
    resolve_model,
    resolve_model_reference,
    resolve_workflow_settings,
    run_command,
    stage_matching_tiles,
    write_runtime_config,
)

DEFAULT_WORKSPACE = Path("workdir")
DEFAULT_YEARS = (2018, 2023)
DEFAULT_CHANGE_DETECTION_SCRIPT = "change-detection-lidar-flair-class_prob.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rejoue le pipeline LiDAR + orthophoto + FLAIR-HUB (run_workflow.py) pour "
            "plusieurs années, puis lance la détection de changement combinée."
        )
    )
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--run-name", required=True)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(DEFAULT_YEARS),
        help="Années à traiter, dans l'ordre chronologique (ex: 2018 2023).",
    )

    # --- Configs FLAIR-HUB (partagées entre les années par défaut) ---
    parser.add_argument("--experiment-config-dir", type=Path, default=None)
    parser.add_argument("--config-template", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--matrix-config", type=Path, default=DEFAULT_MATRIX_CONFIG)

    # --- Emprise spatiale (zone de test), partagée entre les années ---
    parser.add_argument("--xmin-start", type=int, required=True)
    parser.add_argument("--xmin-end", type=int, required=True)
    parser.add_argument("--ymin-start", type=int, required=True)
    parser.add_argument("--ymin-end", type=int, required=True)

    # --- Inventaires par année : format "ANNEE=CHEMIN", répétable ---
    parser.add_argument(
        "--nuage-json",
        action="append",
        required=True,
        metavar="ANNEE=CHEMIN",
        help="Inventaire JSON LiDAR pour une année. Répéter une fois par année (ex: --nuage-json 2018=inputs/nuage_2018.json).",
    )
    parser.add_argument(
        "--ortho-json",
        action="append",
        default=[],
        metavar="ANNEE=CHEMIN",
        help=(
            "Inventaire JSON orthophoto pour une année. Répéter une fois par année. "
            "Optionnel : si omis pour une année, résolu automatiquement vers "
            "<inputs-dir>/<RGB ou IR>/ortho_<ANNEE>.json selon --band-mode."
        ),
    )
    parser.add_argument(
        "--nuage-json-url",
        action="append",
        default=[],
        metavar="ANNEE=URL",
        help="URL de secours pour télécharger l'inventaire LiDAR manquant d'une année.",
    )
    parser.add_argument(
        "--ortho-json-url",
        action="append",
        default=[],
        metavar="ANNEE=URL",
        help="URL de secours pour télécharger l'inventaire orthophoto manquant d'une année.",
    )
    parser.add_argument(
        "--download-missing-inventories",
        action="store_true",
        help="Télécharge les inventaires manquants si une URL est fournie.",
    )

    # --- Paramètres FLAIR / rasterisation, partagés entre les années ---
    parser.add_argument("--resolution", type=float, default=None)
    parser.add_argument(
        "--band-mode",
        choices=["rgb", "ir"],
        default="rgb",
        help=(
            "Type d'orthophoto utilisé : rgb ou ir. Sert de valeur par défaut pour "
            "--channels, à choisir automatiquement inputs/RGB/ ou inputs/IR/ quand "
            "--ortho-json n'est pas fourni, et à choisir le modèle FLAIR-HUB par défaut."
        ),
    )
    parser.add_argument(
        "--inputs-dir",
        type=Path,
        default=None,
        help=(
            "Dossier racine des inventaires JSON (nuage_ANNEE.json à la racine, "
            "sous-dossiers RGB/ et IR/ pour ortho_ANNEE.json). Défaut: <workspace>/inputs."
        ),
    )
    parser.add_argument(
        "--channels",
        type=int,
        nargs="+",
        default=None,
        help="Bandes à lire dans la mosaïque orthophoto (1-based), dans l'ordre attendu "
        "par le modèle. Par défaut [1, 2, 3] (RGB ou IR : les deux rasters sources ont "
        "déjà leurs bandes dans l'ordre voulu).",
    )
    parser.add_argument(
        "--means",
        type=float,
        nargs="+",
        default=None,
        help="Moyennes de normalisation, une par canal, dans le même ordre que --channels. "
        "Si omis, calculées automatiquement sur la mosaïque orthophoto de chaque année.",
    )
    parser.add_argument(
        "--stds",
        type=float,
        nargs="+",
        default=None,
        help="Écarts-types de normalisation, une par canal, dans le même ordre que --channels. "
        "Si omis, calculés automatiquement sur la mosaïque orthophoto de chaque année.",
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--num-worker", type=int, default=None)
    parser.add_argument("--img-pixels-detection", type=int, default=None)
    parser.add_argument("--margin", type=int, default=None)
    parser.add_argument("--ortho-source-resolution", type=float, default=None)
    parser.add_argument(
        "--ortho-target-resolution",
        "--ortho-output-resolution",
        dest="ortho_output_resolution",
        type=float,
        default=None,
    )
    parser.add_argument("--use-gpu", action=argparse.BooleanOptionalAction, default=None)

    # --- Flags requis par resolve_workflow_settings() (run_workflow.py), même si
    # ce script n'utilise pas la pipeline "legacy" / fusion bâtiment par défaut. ---
    parser.add_argument("--modify-flair", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--keep-class-lidar1", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--flair-only-herbaceous", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--run-legacy-fusion", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--apply-lidar-correction", action=argparse.BooleanOptionalAction, default=None)

    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument(
        "--reuse-derived-rasters",
        action="store_true",
        help="Réutilise les mosaïques LiDAR/orthophoto déjà présentes pour chaque année.",
    )
    parser.add_argument("--skip-flair", action="store_true")

    # --- Modèle FLAIR-HUB (téléchargé une seule fois, partagé entre les années) ---
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--model-repo", default=None)
    parser.add_argument("--model-filename", default=None)
    parser.add_argument("--model-revision", default=None)
    parser.add_argument("--flair-hub-ref", default="main")
    parser.add_argument("--flair-hub-repo-url", default=DEFAULT_FLAIR_HUB_REPO_URL)

    # --- Détection de changement ---
    parser.add_argument(
        "--change-detection-script",
        default=DEFAULT_CHANGE_DETECTION_SCRIPT,
        help="Script de combinaison LiDAR+FLAIR à lancer sur les deux dates.",
    )
    parser.add_argument(
        "--skip-change-detection",
        action="store_true",
        help="N'exécute que les étapes par année, sans lancer la combinaison finale.",
    )
    parser.add_argument(
        "--target-classes",
        nargs="+",
        default=["building"],
        help=(
            "Classes à traiter pour la détection de changement (clés définies dans "
            "matrix_config['classes'], ex: building vegetation). Une sortie séparée "
            "est produite par classe."
        ),
    )
    parser.add_argument(
        "--prob-threshold",
        type=float,
        default=20 / 255,
        help=(
            "Seuil de probabilité (0-1) pour binariser la bande valide, transmis "
            "à --prob-threshold du script de détection de changement (même seuil "
            "pour toutes les classes de --target-classes)."
        ),
    )

    parser.add_argument(
        "--metadata-name",
        default="run_metadata.json",
        help="Nom du fichier de métadonnées de run écrit sous le run directory.",
    )
    return parser.parse_args()


def reset_directory(path: Path) -> None:
    """Vide entièrement un dossier (le recrée s'il n'existe pas).

    Nécessaire car extract_nuage.py / ortho_extract.py / fusion_nuage.py écrivent
    dans des dossiers qui persistent entre deux lancements (même --run-name). Sans
    ce nettoyage, des tuiles d'un précédent lancement avec une autre emprise
    (--xmin/--ymin différents) restent sur disque et se retrouvent mélangées aux
    nouvelles tuiles lors de la mosaïque (ortho_fusion.py), produisant un résultat
    incohérent avec les coordonnées passées en paramètre.
    """
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def parse_year_value_pairs(values: list[str] | None, *, label: str) -> dict[int, str]:
    """Transforme une liste ["2018=chemin_a", "2023=chemin_b"] en dict {2018: "chemin_a", ...}."""
    result: dict[int, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"Format invalide pour {label}: '{raw}' (attendu ANNEE=VALEUR)")
        year_str, value = raw.split("=", 1)
        try:
            year = int(year_str)
        except ValueError as exc:
            raise ValueError(f"Année invalide dans {label}: '{year_str}'") from exc
        result[year] = value
    return result


def run_year_pipeline(
    *,
    year: int,
    args: argparse.Namespace,
    workflow_settings: dict[str, Any],
    code_dir: Path,
    year_run_dir: Path,
    nuage_json: Path,
    ortho_json: Path,
    nuage_json_url: str | None,
    ortho_json_url: str | None,
    config_template: Path,
    matrix_config_path: Path,
    model_path: Path,
    flair_hub_src: Path,
) -> dict[str, Path]:
    """Reproduit, pour une année donnée, les étapes LiDAR + orthophoto + FLAIR-HUB
    de run_workflow.py (extraction, rasterisation, mosaïques, inférence), sans les
    étapes de repondération/fusion qui ne concernent que le pipeline "bâtiment"."""

    laz_dir = year_run_dir / "lidar" / "laz_tiles"
    lidar_tiles_dir = year_run_dir / "lidar" / "tiles"
    lidar_height_tiles_dir = lidar_tiles_dir / "heights"
    lidar_class_tiles_dir = lidar_tiles_dir / "class"
    lidar_mns_mnt_tiles_dir = lidar_tiles_dir / "mns_mnt"
    lidar_mosaic_dir = year_run_dir / "lidar" / "mosaic"

    ortho_temp_dir = year_run_dir / "ortho" / "temp_5cm"
    ortho_tiles_dir = year_run_dir / "ortho" / "tiles"
    ortho_mosaic_dir = year_run_dir / "ortho" / "mosaic"

    flair_dir = year_run_dir / "flair"
    flair_probability_dir = flair_dir / "probabilities"

    lidar_height_mosaic = lidar_mosaic_dir / "lidar_height.tif"
    lidar_class_mosaic = lidar_mosaic_dir / "lidar_class.tif"
    lidar_mns_mosaic = lidar_mosaic_dir / "lidar_mns.tif"
    lidar_mnt_mosaic = lidar_mosaic_dir / "lidar_mnt.tif"
    orthophoto_mosaic = ortho_mosaic_dir / "orthophoto_mosaic.tif"
    runtime_config = flair_dir / f"runtime_config_{year}.yaml"

    for folder in [
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
    ]:
        folder.mkdir(parents=True, exist_ok=True)

    if not args.skip_download:
        ensure_inventory_file(
            nuage_json,
            label=f"inventaire LiDAR {year}",
            url=nuage_json_url,
            download_missing=args.download_missing_inventories,
        )
        ensure_inventory_file(
            ortho_json,
            label=f"inventaire orthophoto {year}",
            url=ortho_json_url,
            download_missing=args.download_missing_inventories,
        )

        # Purge des tuiles d'un éventuel lancement précédent (autre emprise) avant
        # de réextraire, pour ne jamais mélanger deux bbox dans la même mosaïque.
        reset_directory(laz_dir)
        reset_directory(ortho_tiles_dir)
        reset_directory(ortho_temp_dir)

        # Extraction des tuiles LiDAR (.laz) pour l'année courante
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

        # Extraction et rééchantillonnage des tuiles orthophotos pour l'année courante
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
        require_existing_file(lidar_height_mosaic, label=f"mosaïque hauteur LiDAR {year}")
        require_existing_file(lidar_class_mosaic, label=f"mosaïque classes LiDAR {year}")
        require_existing_file(lidar_mns_mosaic, label=f"mosaïque MNS {year}")
        require_existing_file(lidar_mnt_mosaic, label=f"mosaïque MNT {year}")
        require_existing_file(orthophoto_mosaic, label=f"mosaïque orthophoto {year}")
    else:
        # Purge des tuiles rasterisées d'un précédent lancement (autre emprise).
        reset_directory(lidar_height_tiles_dir)
        reset_directory(lidar_class_tiles_dir)
        reset_directory(lidar_mns_mnt_tiles_dir)

        # Rasterisation du nuage de points -> hauteur, classe, MNS, MNT (par tuile)
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

        # Mosaïque des hauteurs LiDAR
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

        if not mns_count:
            raise FileNotFoundError(f"Aucune tuile MNS trouvée dans: {lidar_mns_mnt_tiles_dir}")
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

        if not mnt_count:
            raise FileNotFoundError(f"Aucune tuile MNT trouvée dans: {lidar_mns_mnt_tiles_dir}")
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

        # Mosaïque des classes LiDAR
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

        # Mosaïque orthophoto
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

    probability_raster: Path
    if not args.skip_flair:
        # Purge d'éventuels rasters de probabilité d'un précédent lancement (autre
        # emprise) : find_latest_raster() se base sur la date de modification, donc
        # un vieux fichier ne doit jamais traîner dans ce dossier.
        reset_directory(flair_probability_dir)

        means, stds = workflow_settings["means"], workflow_settings["stds"]
        if means is None or stds is None:
            means, stds = compute_band_stats(orthophoto_mosaic, workflow_settings["channels"])
            print(
                f"[auto] means/stds calculés sur {orthophoto_mosaic.name} pour {year} "
                f"(band_mode={workflow_settings['band_mode']}, channels={workflow_settings['channels']}): "
                f"means={means} stds={stds}"
            )

        write_runtime_config(
            config_template,
            runtime_config,
            model_path=model_path,
            orthophoto_mosaic=orthophoto_mosaic,
            flair_output_dir=flair_probability_dir,
            run_name=f"{args.run_name}_{year}",
            use_gpu=workflow_settings["use_gpu"],
            batch_size=workflow_settings["batch_size"],
            num_worker=workflow_settings["num_worker"],
            img_pixels_detection=workflow_settings["img_pixels_detection"],
            margin=workflow_settings["margin"],
            output_px_meters=workflow_settings["resolution"],
            channels=workflow_settings["channels"],
            means=means,
            stds=stds,
        )
        import os

        flair_env = os.environ.copy()
        existing_pythonpath = flair_env.get("PYTHONPATH")
        flair_env["PYTHONPATH"] = (
            f"{flair_hub_src}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else str(flair_hub_src)
        )
        # Inférence FLAIR-HUB sur la mosaïque orthophoto -> raster de probabilités (class_prob)
        run_command(
            [sys.executable, "-m", "flair_zonal_detection.main", "--config", str(runtime_config)],
            cwd=code_dir,
            env=flair_env,
        )
        try:
            probability_raster = find_latest_raster(flair_probability_dir)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"L'inférence FLAIR pour {year} n'a produit aucun GeoTIFF de probabilités. "
                "Vérifie les logs ci-dessus."
            ) from exc
    else:
        probability_raster = find_latest_raster(flair_probability_dir)

    return {
        "lidar_height": lidar_height_mosaic,
        "lidar_class": lidar_class_mosaic,
        "lidar_mns": lidar_mns_mosaic,
        "lidar_mnt": lidar_mnt_mosaic,
        "orthophoto_mosaic": orthophoto_mosaic,
        "class_prob": probability_raster,
    }


def main() -> None:
    args = parse_args()
    validate_bbox(args.xmin_start, args.xmin_end, args.ymin_start, args.ymin_end)
    resolve_model_reference(args)

    nuage_json_by_year = {
        year: Path(path) for year, path in parse_year_value_pairs(args.nuage_json, label="--nuage-json").items()
    }
    ortho_json_by_year = {
        year: Path(path) for year, path in parse_year_value_pairs(args.ortho_json, label="--ortho-json").items()
    }
    nuage_json_url_by_year = parse_year_value_pairs(args.nuage_json_url, label="--nuage-json-url")
    ortho_json_url_by_year = parse_year_value_pairs(args.ortho_json_url, label="--ortho-json-url")

    code_dir = Path(__file__).resolve().parent
    workspace = args.workspace.resolve()
    inputs_dir = (args.inputs_dir or (workspace / "inputs")).resolve()

    for year in args.years:
        if year not in nuage_json_by_year:
            raise ValueError(f"--nuage-json manquant pour l'année {year}")
        if year not in ortho_json_by_year:
            # Convention: inputs/RGB/ortho_<ANNEE>.json ou inputs/IR/ortho_<ANNEE>.json
            auto_path = inputs_dir / args.band_mode.upper() / f"ortho_{year}.json"
            if not auto_path.exists():
                raise ValueError(
                    f"--ortho-json manquant pour l'année {year} et introuvable par convention: "
                    f"{auto_path} (band-mode={args.band_mode})"
                )
            ortho_json_by_year[year] = auto_path
            print(f"[auto] --ortho-json {year}={auto_path} (résolu depuis --band-mode {args.band_mode})")

    run_dir = workspace / "runs" / args.run_name
    model_dir = workspace / "models"
    flair_hub_source_root = workspace / ".deps"
    change_detection_dir = run_dir / "change_detection"
    metadata_path = run_dir / args.metadata_name

    config_template, matrix_config_path, experiment_config_dir = resolve_experiment_config_paths(
        code_dir,
        experiment_config_dir=args.experiment_config_dir,
        config_template=args.config_template,
        matrix_config=args.matrix_config,
    )
    if not config_template.exists():
        raise FileNotFoundError(f"Config FLAIR-HUB introuvable: {config_template}")
    if not matrix_config_path.exists():
        raise FileNotFoundError(f"Matrix config introuvable: {matrix_config_path}")

    runtime_template_config = load_yaml_config(config_template)
    matrix_config = load_yaml_config(matrix_config_path)
    workflow_settings = resolve_workflow_settings(
        runtime_template_config=runtime_template_config,
        matrix_config=matrix_config,
        args=args,
    )

    validate_positive_number(workflow_settings["resolution"], "resolution")
    if workflow_settings["ortho_source_resolution"] is not None:
        validate_positive_number(workflow_settings["ortho_source_resolution"], "ortho_source_resolution")
    validate_positive_number(workflow_settings["ortho_output_resolution"], "ortho_output_resolution")
    if workflow_settings["batch_size"] <= 0:
        raise ValueError("batch_size must be strictly positive.")
    if workflow_settings["num_worker"] < 0:
        raise ValueError("num_worker must be greater than or equal to 0.")
    if workflow_settings["img_pixels_detection"] <= 0:
        raise ValueError("img_pixels_detection must be strictly positive.")
    if workflow_settings["margin"] < 0:
        raise ValueError("margin must be greater than or equal to 0.")
    if workflow_settings["means"] is not None and len(workflow_settings["means"]) != len(
        workflow_settings["channels"]
    ):
        raise ValueError("--means doit avoir autant de valeurs que --channels.")
    if workflow_settings["stds"] is not None and len(workflow_settings["stds"]) != len(
        workflow_settings["channels"]
    ):
        raise ValueError("--stds doit avoir autant de valeurs que --channels.")
    ensure_cuda_available_if_requested(workflow_settings["use_gpu"])

    for folder in [workspace, run_dir, model_dir, change_detection_dir]:
        folder.mkdir(parents=True, exist_ok=True)

    # Le modèle FLAIR-HUB et la source du code sont partagés entre toutes les années.
    model_path = resolve_model(args, model_dir)
    flair_hub_src = ensure_flair_hub_source(
        flair_hub_source_root,
        repo_url=args.flair_hub_repo_url,
        ref=args.flair_hub_ref,
    )

    outputs_by_year: dict[int, dict[str, Path]] = {}
    for year in args.years:
        print(f"\n=== Traitement de l'année {year} ===")
        year_run_dir = run_dir / str(year)
        year_run_dir.mkdir(parents=True, exist_ok=True)
        outputs_by_year[year] = run_year_pipeline(
            year=year,
            args=args,
            workflow_settings=workflow_settings,
            code_dir=code_dir,
            year_run_dir=year_run_dir,
            nuage_json=nuage_json_by_year[year],
            ortho_json=ortho_json_by_year[year],
            nuage_json_url=nuage_json_url_by_year.get(year),
            ortho_json_url=ortho_json_url_by_year.get(year),
            config_template=config_template,
            matrix_config_path=matrix_config_path,
            model_path=model_path,
            flair_hub_src=flair_hub_src,
        )

    if not args.skip_change_detection:
        if len(args.years) < 2:
            raise ValueError("Il faut au moins deux années pour lancer la détection de changement.")
        before_year, after_year = args.years[0], args.years[-1]
        before = outputs_by_year[before_year]
        after = outputs_by_year[after_year]

        # change-detection-lidar-flair-class_prob.py utilise la grille du raster
        # --class-prob2 comme référence de reprojection : "1" doit donc être l'année
        # la plus ancienne (before_year) et "2" la plus récente (after_year). Le MNS
        # et le MNT ne sont pas utilisés par ce script (ils ne servent qu'à calculer
        # le raster de hauteur, déjà produit par fusion_nuage.py).
        change_detection_dirs: dict[str, Path] = {}
        for target_class in args.target_classes:
            class_out_dir = change_detection_dir / target_class
            class_out_dir.mkdir(parents=True, exist_ok=True)
            change_detection_command = [
                sys.executable,
                args.change_detection_script,
                "--class-map1",
                str(before["lidar_class"]),
                "--height-map1",
                str(before["lidar_height"]),
                "--class-prob1",
                str(before["class_prob"]),
                "--class-map2",
                str(after["lidar_class"]),
                "--height-map2",
                str(after["lidar_height"]),
                "--class-prob2",
                str(after["class_prob"]),
                "--out-dir",
                str(class_out_dir),
                "--target-class",
                target_class,
                "--matrix-config",
                str(matrix_config_path),
                "--prob-threshold",
                str(args.prob_threshold),
            ]
            run_command(change_detection_command, cwd=code_dir)
            change_detection_dirs[target_class] = class_out_dir

    print("\nWorkflow multi-années terminé.")
    for year, outputs in outputs_by_year.items():
        print(f"--- {year} ---")
        for key, path in outputs.items():
            print(f"  {key}: {path}")
    if not args.skip_change_detection:
        for target_class, class_dir in change_detection_dirs.items():
            print(f"Détection de changement ({target_class}): {class_dir}")

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
                "config_template": config_template.as_posix(),
                "matrix_config": matrix_config_path.as_posix(),
                "change_detection_dir": change_detection_dir.as_posix(),
                "change_detection_dirs": {
                    cls: path.as_posix() for cls, path in change_detection_dirs.items()
                } if not args.skip_change_detection else {},
            },
            "resolved_workflow": workflow_settings,
            "outputs_by_year": {
                str(year): {key: path.as_posix() for key, path in outputs.items()}
                for year, outputs in outputs_by_year.items()
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