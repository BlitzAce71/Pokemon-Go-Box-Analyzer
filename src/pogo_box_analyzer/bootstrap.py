from __future__ import annotations

import csv
import shutil
from pathlib import Path

from .image_ops import dhash, hamming_distance, load_image


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def bootstrap_catalog_from_unknowns(
    unknown_crops_dir: Path,
    catalog_base_dir: Path,
    draft_csv_path: Path,
    include_passes: set[str] | None = None,
    dedupe_hash_max_distance: int = 2,
) -> dict[str, int]:
    include_passes = include_passes or {"all"}

    catalog_base_dir.mkdir(parents=True, exist_ok=True)
    icons_dir = catalog_base_dir / "icons_candidates"
    icons_dir.mkdir(parents=True, exist_ok=True)

    files = [
        p
        for p in sorted(unknown_crops_dir.iterdir())
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and _pass_name_from_file(p.name) in include_passes
    ]

    selected: list[tuple[Path, int]] = []

    for file in files:
        image = load_image(str(file))
        fp = dhash(image)
        if any(hamming_distance(fp, existing_fp) <= dedupe_hash_max_distance for _, existing_fp in selected):
            continue
        selected.append((file, fp))

    rows: list[dict[str, str]] = []
    for idx, (source_file, _) in enumerate(selected, start=1):
        out_name = f"candidate_{idx:04d}.png"
        out_path = icons_dir / out_name
        shutil.copyfile(source_file, out_path)

        rows.append(
            {
                "image": f"icons_candidates/{out_name}",
                "species": "",
                "form": "",
                "costume": "",
                "regional_variant": "",
                "source_unknown_crop": source_file.name,
            }
        )

    draft_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with draft_csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "image",
                "species",
                "form",
                "costume",
                "regional_variant",
                "source_unknown_crop",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return {
        "unknown_crops_scanned": len(files),
        "candidate_images_written": len(rows),
    }


def _pass_name_from_file(filename: str) -> str:
    # Expects filename pattern: <pass>__<screenshot_stem>__slotNN.png
    if "__" not in filename:
        return ""
    return filename.split("__", 1)[0].strip().lower()
