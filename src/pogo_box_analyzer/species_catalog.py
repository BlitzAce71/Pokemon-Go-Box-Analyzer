from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .image_ops import (
    average_color,
    color_similarity,
    dhash,
    extract_foreground,
    extract_sprite_component,
    gray_vector_similarity,
    hamming_distance,
    load_image,
    to_gray_vector,
)
from .models import SpeciesKey


@dataclass(frozen=True)
class SpeciesReference:
    key: SpeciesKey
    image_path: Path
    image_hash: int
    avg_color: tuple[float, float, float]
    gray_vec: list[int]


@dataclass(frozen=True)
class SpeciesMatch:
    key: SpeciesKey
    score: float


def load_species_catalog(catalog_csv: Path, image_base_dir: Path) -> list[SpeciesReference]:
    if not catalog_csv.exists():
        return []

    refs: list[SpeciesReference] = []
    with catalog_csv.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            image_rel = (row.get("image") or "").strip()
            species = (row.get("species") or "").strip()
            form = (row.get("form") or "").strip()
            costume = (row.get("costume") or "").strip()
            regional_variant = (row.get("regional_variant") or "").strip()

            if not image_rel or not species:
                continue

            image_path = image_base_dir / image_rel
            if not image_path.exists():
                continue

            image = load_image(str(image_path))
            focused = extract_foreground(image)
            refs.append(
                SpeciesReference(
                    key=SpeciesKey(
                        species=species,
                        form=form,
                        costume=costume,
                        regional_variant=regional_variant,
                    ),
                    image_path=image_path,
                    image_hash=dhash(focused),
                    avg_color=average_color(focused),
                    gray_vec=to_gray_vector(focused),
                )
            )

    return refs


def find_best_species_match(icon_image, refs: list[SpeciesReference]) -> SpeciesMatch | None:
    if not refs:
        return None

    icon_focus = extract_sprite_component(icon_image)
    icon_hash = dhash(icon_focus)
    icon_avg_color = average_color(icon_focus)
    icon_gray = to_gray_vector(icon_focus)

    best: SpeciesMatch | None = None

    for ref in refs:
        hash_distance = hamming_distance(icon_hash, ref.image_hash)
        hash_similarity = 1.0 - (hash_distance / 64.0)
        col_similarity = color_similarity(icon_avg_color, ref.avg_color)
        gray_similarity = gray_vector_similarity(icon_gray, ref.gray_vec)
        score = (0.75 * gray_similarity) + (0.15 * hash_similarity) + (0.10 * col_similarity)

        candidate = SpeciesMatch(key=ref.key, score=score)
        if best is None or candidate.score > best.score:
            best = candidate

    return best


def match_species(icon_image, refs: list[SpeciesReference], threshold: float) -> SpeciesMatch | None:
    best = find_best_species_match(icon_image, refs)
    if best is None:
        return None
    if best.score < threshold:
        return None
    return best
