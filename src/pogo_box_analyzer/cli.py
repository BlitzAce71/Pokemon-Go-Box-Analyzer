from __future__ import annotations

import argparse
import os
from pathlib import Path

from .bootstrap import bootstrap_catalog_from_unknowns
from .config import load_config, write_default_config
from .fandom_import import import_fandom_catalog, merge_catalog_files, write_import_summary
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Pokemon GO box screenshots into species-level CSV counts.")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cfg = sub.add_parser("init-config", help="Write a default config JSON file.")
    init_cfg.add_argument("--output", type=Path, default=Path("config/default_config.json"))

    run = sub.add_parser("run", help="Run the analyzer.")
    run.add_argument("--input-dir", type=Path, default=Path("input"))
    run.add_argument("--output-dir", type=Path, default=Path("output"))
    run.add_argument("--config", type=Path, default=Path("config/default_config.json"))
    run.add_argument("--manifest", type=Path, default=None)
    run.add_argument("--catalog", type=Path, default=Path("data/species_catalog/catalog.csv"))
    run.add_argument("--catalog-images", type=Path, default=Path("data/species_catalog"))
    run.add_argument("--trait-templates", type=Path, default=Path("data/templates/traits"))

    serve = sub.add_parser("serve-web", help="Run mobile web app/API for on-phone screenshot analysis.")
    serve.add_argument("--host", type=str, default="0.0.0.0")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    serve.add_argument("--config", type=Path, default=Path("config/default_config.json"))
    serve.add_argument("--catalog", type=Path, default=Path("data/species_catalog/catalog.csv"))
    serve.add_argument("--catalog-images", type=Path, default=Path("data/species_catalog"))
    serve.add_argument("--trait-templates", type=Path, default=Path("data/templates/traits"))

    bootstrap = sub.add_parser("bootstrap-catalog", help="Create a deduped catalog draft from unknown crops.")
    bootstrap.add_argument("--unknown-crops", type=Path, default=Path("output/unknown_crops"))
    bootstrap.add_argument("--catalog-base", type=Path, default=Path("data/species_catalog"))
    bootstrap.add_argument("--draft-csv", type=Path, default=Path("data/species_catalog/catalog_draft.csv"))
    bootstrap.add_argument("--passes", type=str, default="all", help="Comma-separated pass names, e.g. all,query_4star")
    bootstrap.add_argument("--dedupe-distance", type=int, default=2)

    fandom = sub.add_parser("import-fandom", help="Import species/form catalog from a saved Fandom List of Pokemon page.")
    fandom.add_argument("--html", type=Path, default=Path("data/source/fandom/List of Pokemon.html"))
    fandom.add_argument("--assets-dir", type=Path, default=Path("data/source/fandom/List of Pokemon_files"))
    fandom.add_argument("--catalog-base", type=Path, default=Path("data/species_catalog"))
    fandom.add_argument("--import-csv", type=Path, default=Path("data/species_catalog/catalog_fandom.csv"))
    fandom.add_argument("--catalog", type=Path, default=Path("data/species_catalog/catalog.csv"))
    fandom.add_argument("--include-not-released", action="store_true")
    fandom.add_argument("--include-mega-like", action="store_true", help="Include Mega/Primal/Gigantamax forms.")
    fandom.add_argument("--no-merge", action="store_true", help="Do not merge imported rows into --catalog.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-config":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_default_config(args.output)
        print(f"Wrote default config to {args.output}")
        return

    if args.command == "run":
        config = load_config(args.config if args.config.exists() else None)
        summary = run_pipeline(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            config=config,
            catalog_csv=args.catalog,
            catalog_images_dir=args.catalog_images,
            trait_templates_dir=args.trait_templates,
            manifest_path=args.manifest,
        )
        print("Run complete")
        for key, value in summary.items():
            print(f"{key}: {value}")

        if int(summary.get("trait_templates_loaded", 0)) == 0 and int(summary.get("visible_trait_observations", 0)) > 0:
            print("warning: no trait templates loaded; template-based visible trait detection was requested")

        return

    if args.command == "serve-web":
        from .web_server import run_web_server

        run_web_server(
            host=args.host,
            port=args.port,
            config_path=args.config,
            catalog_csv=args.catalog,
            catalog_images_dir=args.catalog_images,
            trait_templates_dir=args.trait_templates,
        )
        return

    if args.command == "bootstrap-catalog":
        include_passes = {p.strip().lower() for p in args.passes.split(",") if p.strip()}
        summary = bootstrap_catalog_from_unknowns(
            unknown_crops_dir=args.unknown_crops,
            catalog_base_dir=args.catalog_base,
            draft_csv_path=args.draft_csv,
            include_passes=include_passes,
            dedupe_hash_max_distance=args.dedupe_distance,
        )
        print("Catalog bootstrap complete")
        for key, value in summary.items():
            print(f"{key}: {value}")
        print(f"draft_csv: {args.draft_csv}")
        print(f"candidate_icons_dir: {args.catalog_base / 'icons_candidates'}")
        return

    if args.command == "import-fandom":
        summary = import_fandom_catalog(
            html_path=args.html,
            assets_dir=args.assets_dir,
            catalog_base_dir=args.catalog_base,
            output_catalog_csv=args.import_csv,
            include_not_released=args.include_not_released,
            skip_mega_like_forms=not args.include_mega_like,
        )

        merged_summary: dict[str, int | str] | None = None
        if not args.no_merge:
            merged_summary = merge_catalog_files(
                base_catalog_csv=args.catalog,
                imported_catalog_csv=args.import_csv,
                output_catalog_csv=args.catalog,
            )

        summary_path = args.catalog_base / "fandom_import_summary.json"
        final_summary = dict(summary)
        if merged_summary is not None:
            for key, value in merged_summary.items():
                final_summary[f"merge_{key}"] = value

        write_import_summary(summary_path, final_summary)

        print("Fandom import complete")
        for key, value in final_summary.items():
            print(f"{key}: {value}")
        print(f"summary_json: {summary_path}")
        return

    parser.error(f"Unknown command: {args.command}")


