from __future__ import annotations

import csv
import threading
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
    gray_vec: bytes


@dataclass(frozen=True)
class SpeciesMatch:
    key: SpeciesKey
    score: float


_CATALOG_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE_KEY: tuple[str, str, int] | None = None
_CATALOG_CACHE_REFS: list[SpeciesReference] | None = None


def load_species_catalog(catalog_csv: Path, image_base_dir: Path) -> list[SpeciesReference]:
    global _CATALOG_CACHE_KEY, _CATALOG_CACHE_REFS

    if not catalog_csv.exists():
        return []

    cache_key = _build_cache_key(catalog_csv=catalog_csv, image_base_dir=image_base_dir)
    if cache_key is not None:
        with _CATALOG_CACHE_LOCK:
            if _CATALOG_CACHE_KEY == cache_key and _CATALOG_CACHE_REFS is not None:
                return _CATALOG_CACHE_REFS

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

    if cache_key is not None:
        with _CATALOG_CACHE_LOCK:
            _CATALOG_CACHE_KEY = cache_key
            _CATALOG_CACHE_REFS = refs

    return refs


def _build_cache_key(catalog_csv: Path, image_base_dir: Path) -> tuple[str, str, int] | None:
    try:
        csv_stat = catalog_csv.stat()
    except OSError:
        return None

    return (
        str(catalog_csv.resolve()),
        str(image_base_dir.resolve()),
        int(csv_stat.st_mtime_ns),
    )


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
