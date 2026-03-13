from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .aggregate import aggregate_observations, write_species_csv
from .config import Config, GridConfig
from .image_ops import dhash, hamming_distance, iter_grid_cells, load_image
from .models import Observation, SpeciesKey, UnknownObservation
from .ocr_windows import OcrLine, SpeciesNameMatcher, pick_cell_cp_value, pick_cell_name_text, run_ocr
from .species_catalog import SpeciesMatch, SpeciesReference, find_best_species_match, load_species_catalog
from .trait_detector import TraitTemplateStore, detect_visible_traits

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

_AUTO_PASS_NAMES = {"auto", "mixed", "unsorted", "uploads", "inbox"}


@dataclass(frozen=True)
class ScreenshotRecord:
    path: Path
    pass_name: str


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    config: Config,
    catalog_csv: Path,
    catalog_images_dir: Path,
    trait_templates_dir: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    unknown_crops_dir = output_dir / "unknown_crops"
    if unknown_crops_dir.exists():
        for child in unknown_crops_dir.iterdir():
            if child.is_file():
                child.unlink()
    else:
        unknown_crops_dir.mkdir(parents=True, exist_ok=True)

    shots = _load_screenshots(input_dir=input_dir, manifest_path=manifest_path)
    species_refs = load_species_catalog(catalog_csv=catalog_csv, image_base_dir=catalog_images_dir)
    refs_by_species = _group_refs_by_species(species_refs)
    default_key_by_species = _default_key_by_species(refs_by_species)
    name_matcher = SpeciesNameMatcher(list(refs_by_species.keys()))

    trait_templates = TraitTemplateStore.from_directory(trait_templates_dir, trait_rois=config.trait_rois)

    observations: list[Observation] = []
    unknowns: list[UnknownObservation] = []
    seen_fingerprints: dict[str, list[int]] = {}
    processed_cells = 0
    skipped_duplicates = 0
    ocr_slots_used = 0
    visible_trait_observations = 0
    auto_pass_shots = 0
    auto_pass_detected = 0
    auto_pass_fallback_all = 0

    for shot in shots:
        image = load_image(str(shot.path))
        image_w, image_h = image.size

        ocr_lines = run_ocr(shot.path)
        effective_pass_name, was_auto, was_detected = _resolve_effective_pass_name(
            original_pass_name=shot.pass_name,
            ocr_lines=ocr_lines,
            image_w=image_w,
            image_h=image_h,
        )

        if was_auto:
            auto_pass_shots += 1
            if was_detected:
                auto_pass_detected += 1
            else:
                auto_pass_fallback_all += 1

        pass_rule = _resolve_pass_rule(effective_pass_name, config)
        effective_grid = _resolve_effective_grid(
            base_grid=config.grid,
            effective_pass_name=effective_pass_name,
            ocr_lines=ocr_lines,
            image_h=image_h,
        )

        for slot_index, cell_rect, cell_image in iter_grid_cells(image, effective_grid):
            processed_cells += 1
            fingerprint = dhash(cell_image)
            pass_seen = seen_fingerprints.setdefault(effective_pass_name, [])
            if any(hamming_distance(fingerprint, prev) <= config.dedupe_hash_max_distance for prev in pass_seen):
                skipped_duplicates += 1
                continue
            pass_seen.append(fingerprint)

            cell_bbox = _rect_to_pixels(image_w, image_h, cell_rect)
            slot_name_text = pick_cell_name_text(ocr_lines, cell_bbox)
            slot_cp = pick_cell_cp_value(ocr_lines, cell_bbox)

            species_match, accepted_from_ocr = _match_with_ocr_first(
                icon_image=cell_image,
                slot_name_text=slot_name_text,
                name_matcher=name_matcher,
                refs_by_species=refs_by_species,
                default_key_by_species=default_key_by_species,
                all_refs=species_refs,
                global_threshold=config.species_match_threshold,
            )

            if accepted_from_ocr:
                ocr_slots_used += 1

            is_confident = False
            if species_match is not None:
                if accepted_from_ocr:
                    is_confident = True
                elif species_match.score >= config.species_match_threshold:
                    is_confident = True

            if not is_confident or species_match is None:
                filename = f"{effective_pass_name}__{shot.path.stem}__slot{slot_index:02d}.png"
                cell_image.save(unknown_crops_dir / filename)
                unknowns.append(
                    UnknownObservation(
                        screenshot_path=shot.path,
                        pass_name=effective_pass_name,
                        slot_index=slot_index,
                        best_candidate="" if species_match is None else species_match.key.species,
                        best_score=0.0 if species_match is None else species_match.score,
                    )
                )
                continue

            visible_traits: set[str] = set()
            if bool(pass_rule["include_visible_traits"]):
                visible_trait_observations += 1
            if bool(pass_rule["include_visible_traits"]) and trait_templates.template_count > 0:
                visible_traits = detect_visible_traits(
                    cell_image=cell_image,
                    trait_templates=trait_templates,
                    trait_rois=config.trait_rois,
                    thresholds=config.trait_thresholds,
                )

            observations.append(
                Observation(
                    species_key=species_match.key,
                    screenshot_path=shot.path,
                    pass_name=effective_pass_name,
                    slot_index=slot_index,
                    cp=slot_cp,
                    icon_hash=fingerprint,
                    visible_traits=visible_traits,
                    hidden_traits=set(pass_rule["add_traits"]),
                    include_total=bool(pass_rule["include_total"]),
                    include_visible_traits=bool(pass_rule["include_visible_traits"]),
                    best_species_score=species_match.score,
                )
            )

    aggregate_rows = aggregate_observations(
        observations=observations,
        trait_columns=config.trait_columns,
        visible_special_traits=config.visible_special_traits,
    )

    csv_path = _write_species_csv_with_fallback(output_dir / "species_counts.csv", aggregate_rows, config.trait_columns)
    unknown_path = _write_unknowns_csv_with_fallback(output_dir / "unknown_slots.csv", unknowns)

    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "species_counts_csv": str(csv_path),
        "unknown_slots_csv": str(unknown_path),
        "screenshots": len(shots),
        "cells_processed": processed_cells,
        "observations_used": len(observations),
        "unknown_slots": len(unknowns),
        "duplicates_skipped": skipped_duplicates,
        "species_catalog_entries": len(species_refs),
        "trait_templates_loaded": trait_templates.template_count,
        "ocr_slots_used": ocr_slots_used,
        "visible_trait_observations": visible_trait_observations,
        "auto_pass_screenshots": auto_pass_shots,
        "auto_pass_detected": auto_pass_detected,
        "auto_pass_fallback_all": auto_pass_fallback_all,
    }

    summary_path = _next_available_path(output_dir / "run_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _match_with_ocr_first(
    icon_image,
    slot_name_text: str | None,
    name_matcher: SpeciesNameMatcher,
    refs_by_species: dict[str, list[SpeciesReference]],
    default_key_by_species: dict[str, SpeciesKey],
    all_refs: list[SpeciesReference],
    global_threshold: float,
) -> tuple[SpeciesMatch | None, bool]:
    if slot_name_text:
        species_name, score = name_matcher.match(slot_name_text)
        if species_name:
            scoped_refs = refs_by_species.get(species_name, [])
            if scoped_refs:
                scoped_match = find_best_species_match(icon_image=icon_image, refs=scoped_refs)
                scoped_threshold = max(0.45, global_threshold - 0.22)
                if scoped_match is not None and scoped_match.score >= scoped_threshold:
                    return scoped_match, True

            # OCR-only fallback is strict to avoid false positives from fuzzy text.
            fallback_key = default_key_by_species.get(species_name)
            if fallback_key is not None and score >= 0.93:
                return SpeciesMatch(key=fallback_key, score=score), True

    # If OCR name is weak/missing, only accept an icon-only match at a very high threshold.
    global_match = find_best_species_match(icon_image=icon_image, refs=all_refs)
    high_threshold = max(0.78, global_threshold + 0.08)
    if global_match is not None and global_match.score >= high_threshold:
        return global_match, False

    return None, False


def _group_refs_by_species(refs: list[SpeciesReference]) -> dict[str, list[SpeciesReference]]:
    grouped: dict[str, list[SpeciesReference]] = {}
    for ref in refs:
        grouped.setdefault(ref.key.species, []).append(ref)
    return grouped


def _default_key_by_species(refs_by_species: dict[str, list[SpeciesReference]]) -> dict[str, SpeciesKey]:
    out: dict[str, SpeciesKey] = {}
    for species, refs in refs_by_species.items():
        chosen = min(
            refs,
            key=lambda r: (
                1 if r.key.form else 0,
                1 if r.key.costume else 0,
                1 if r.key.regional_variant else 0,
                len(r.key.form),
                len(r.key.costume),
                len(r.key.regional_variant),
                r.image_path.name,
            ),
        )
        out[species] = chosen.key
    return out


def _rect_to_pixels(image_w: int, image_h: int, rect) -> tuple[int, int, int, int]:
    x0 = max(0, min(image_w - 1, int(round(rect.x * image_w))))
    y0 = max(0, min(image_h - 1, int(round(rect.y * image_h))))
    x1 = max(x0 + 1, min(image_w, int(round((rect.x + rect.w) * image_w))))
    y1 = max(y0 + 1, min(image_h, int(round((rect.y + rect.h) * image_h))))
    return x0, y0, x1, y1


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    for idx in range(2, 1000):
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate

    return parent / f"{stem}_overflow{suffix}"


def _write_species_csv_with_fallback(path: Path, rows, trait_columns: list[str]) -> Path:
    target = path
    for _ in range(25):
        try:
            write_species_csv(target, rows, trait_columns)
            return target
        except PermissionError:
            target = _next_available_path(target)
    raise PermissionError(f"Unable to write species CSV after retries: {path}")


def _write_unknowns_csv_with_fallback(path: Path, unknowns: list[UnknownObservation]) -> Path:
    target = path
    for _ in range(25):
        try:
            _write_unknowns_csv(target, unknowns)
            return target
        except PermissionError:
            target = _next_available_path(target)
    raise PermissionError(f"Unable to write unknown slots CSV after retries: {path}")


def _load_screenshots(input_dir: Path, manifest_path: Path | None = None) -> list[ScreenshotRecord]:
    if manifest_path is not None and manifest_path.exists():
        return _load_from_manifest(manifest_path, input_dir)

    manifest_candidate = input_dir / "manifest.csv"
    if manifest_candidate.exists():
        return _load_from_manifest(manifest_candidate, input_dir)

    shots: list[ScreenshotRecord] = []
    if not input_dir.exists():
        return shots

    # Backward-compatible folder mode.
    for pass_dir in sorted(p for p in input_dir.iterdir() if p.is_dir()):
        pass_name = pass_dir.name
        for image_path in sorted(pass_dir.iterdir()):
            if image_path.suffix.lower() in IMAGE_SUFFIXES:
                shots.append(ScreenshotRecord(path=image_path, pass_name=pass_name))

    # Convenience mode: images directly under input/ are treated as auto-detect.
    for image_path in sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES):
        shots.append(ScreenshotRecord(path=image_path, pass_name="auto"))

    return shots


def _load_from_manifest(manifest_path: Path, input_dir: Path) -> list[ScreenshotRecord]:
    shots: list[ScreenshotRecord] = []
    with manifest_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            image_path = (row.get("image_path") or "").strip()
            pass_name = (row.get("pass_name") or "").strip()
            if not image_path or not pass_name:
                continue

            path = Path(image_path)
            if not path.is_absolute():
                path = (input_dir / path).resolve()

            shots.append(ScreenshotRecord(path=path, pass_name=pass_name))

    return shots


def _resolve_effective_pass_name(
    original_pass_name: str,
    ocr_lines: list[OcrLine],
    image_w: int,
    image_h: int,
) -> tuple[str, bool, bool]:
    raw_name = original_pass_name.strip() or "auto"
    lowered = raw_name.lower()

    should_auto_detect = lowered in _AUTO_PASS_NAMES or lowered.startswith("auto")
    if not should_auto_detect:
        return raw_name, False, False

    detected = _detect_pass_name_from_search_bar(ocr_lines, image_w=image_w, image_h=image_h)
    if detected is not None:
        return detected, True, True

    return "all", True, False


def _detect_pass_name_from_search_bar(lines: list[OcrLine], image_w: int, image_h: int) -> str | None:
    if not lines:
        return None

    candidates: list[tuple[float, str]] = []

    for line in lines:
        raw_text = (line.text or "").strip()
        if len(raw_text) < 2:
            continue

        cx = line.x + (line.w / 2.0)
        cy = line.y + (line.h / 2.0)
        rel_x = cx / max(1.0, float(image_w))
        rel_y = cy / max(1.0, float(image_h))

        # Search bar text is around upper-middle; keep this tight to avoid matching top header text.
        if rel_y < 0.14 or rel_y > 0.34:
            continue
        if rel_x < 0.08 or rel_x > 0.92:
            continue

        normalized = _normalize_search_text(raw_text)
        if not normalized:
            continue

        classified = _classify_search_query_text(normalized)

        score = 2.8
        score -= abs(rel_y - 0.255) * 8.0
        score += min(2.0, (line.w / max(1.0, float(image_w))) * 4.0)

        compact = normalized.replace(" ", "")
        if classified is not None and classified != "all":
            score += 2.4
        if any(token in compact for token in ("dynamax", "lucky", "shadow", "purified", "shiny", "costume", "mega", "4*", "4star", "hundo")):
            score += 2.2
        if "search" in compact:
            score += 0.8
        if any(token in compact for token in ("pokemon", "eggs", "tags")):
            score -= 1.6
        if "!" in compact or "&" in compact:
            score += 0.8

        digit_count = sum(1 for c in compact if c.isdigit())
        if digit_count >= 2 and classified is None:
            score -= 1.1

        candidates.append((score, normalized))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)

    # Try strongest candidates first.
    for _, text in candidates[:8]:
        classified = _classify_search_query_text(text)
        if classified is not None:
            return classified

    # OCR boxes can be noisy on some devices; do one final unconstrained scan.
    for line in lines:
        classified = _classify_search_query_text(_normalize_search_text(line.text or ""))
        if classified is not None and classified != "all":
            return classified

    # No confident pass classification from OCR.
    return None


def _normalize_search_text(text: str) -> str:
    out = text.strip().lower()
    out = out.replace("\u2605", "*")
    out = out.replace("\u2606", "*")
    out = out.replace("\u2013", "-")
    out = out.replace("\u2014", "-")
    out = out.replace("\u2212", "-")
    out = re.sub(r"[^a-z0-9!&*\-\s]", " ", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()

def _classify_search_query_text(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text.lower())
    if not compact:
        return None

    if "search" in compact:
        return "all"

    if "dynamax" in compact or "gmax" in compact or ("dyna" in compact and "max" in compact):
        return "query_dynamax"
    if "lucky" in compact or "luck" in compact:
        return "query_lucky"
    if "shadow" in compact or "shado" in compact:
        return "query_shadow"
    if "purified" in compact or "purif" in compact:
        return "query_purified"
    if "shiny" in compact or "shin" in compact:
        return "query_shiny"
    if "costume" in compact or "costum" in compact:
        return "query_costume"

    if "hundo" in compact or "4star" in compact or "4*" in compact or "*4" in compact:
        return "query_4star"
    if "mega" in compact:
        return "query_mega"

    if "normal" in compact:
        return "query_normal"

    neg_hits = sum(1 for token in ("!dynamax", "!lucky", "!shadow", "!purified", "!shiny", "!costume", "!4", "!mega") if token in compact)
    if "!" in compact and neg_hits >= 2:
        return "query_normal"

    if compact == "all" or compact.startswith("all"):
        return "all"

    return None



def _resolve_effective_grid(
    base_grid: GridConfig,
    effective_pass_name: str,
    ocr_lines: list[OcrLine],
    image_h: int,
) -> GridConfig:
    # Search-mode screens can have extra UI rows (e.g. "Show Evolutionary Line"),
    # which pushes the Pokemon grid downward.
    search_overlay = effective_pass_name != "all" or _has_evolutionary_line_text(ocr_lines)
    if not search_overlay:
        return base_grid

    estimated_start_y = _estimate_grid_start_y_from_cp_lines(
        ocr_lines=ocr_lines,
        image_h=image_h,
        cell_h=base_grid.cell_h,
        base_start_y=base_grid.start_y,
    )

    if estimated_start_y is None:
        estimated_start_y = min(0.33, base_grid.start_y + 0.05)

    if abs(estimated_start_y - base_grid.start_y) < 0.004:
        return base_grid

    return GridConfig(
        rows=base_grid.rows,
        cols=base_grid.cols,
        start_x=base_grid.start_x,
        start_y=estimated_start_y,
        cell_w=base_grid.cell_w,
        cell_h=base_grid.cell_h,
        gap_x=base_grid.gap_x,
        gap_y=base_grid.gap_y,
    )


def _has_evolutionary_line_text(lines: list[OcrLine]) -> bool:
    for line in lines:
        normalized = _normalize_search_text(line.text or "")
        if "evolutionary" in normalized and "line" in normalized:
            return True
    return False


def _estimate_grid_start_y_from_cp_lines(
    ocr_lines: list[OcrLine],
    image_h: int,
    cell_h: float,
    base_start_y: float,
) -> float | None:
    cp_centers: list[float] = []
    cp_pattern = re.compile(r"\bcp\s*[0-9]{2,5}\b")

    for line in ocr_lines:
        text = _normalize_search_text(line.text or "")
        compact = text.replace(" ", "")
        if not compact:
            continue

        has_cp = bool(cp_pattern.search(text))
        if not has_cp:
            has_cp = compact.startswith("cp") and any(ch.isdigit() for ch in compact)
        if not has_cp:
            continue

        cy = line.y + (line.h / 2.0)
        rel_y = cy / max(1.0, float(image_h))
        if rel_y < 0.16 or rel_y > 0.95:
            continue
        cp_centers.append(rel_y)

    if len(cp_centers) < 3:
        return None

    cp_centers.sort()
    first_row_cp = cp_centers[min(2, len(cp_centers) - 1)]

    # CP text usually sits near the top area of each cell.
    estimated = first_row_cp - (cell_h * 0.12)
    min_start = max(0.0, base_start_y - 0.01)
    max_start = min(0.40, base_start_y + 0.14)
    estimated = max(min_start, min(max_start, estimated))
    return estimated
def _resolve_pass_rule(pass_name: str, config: Config) -> dict[str, object]:
    direct = config.pass_rules.get(pass_name)
    if direct is None:
        direct = config.pass_rules.get(pass_name.lower())
    if direct is not None:
        return dict(direct)

    lower_name = pass_name.lower().strip()
    compact = re.sub(r"\s+", "", lower_name)

    inferred_traits: list[str] = []

    if any(token in compact for token in ("4star", "4*", "hundo")):
        inferred_traits.append("hundo_4star")
    if "mega" in compact:
        inferred_traits.append("mega_capable")
    if "dynamax" in compact or "gmax" in compact:
        inferred_traits.append("dynamax")
    if "lucky" in compact:
        inferred_traits.append("lucky")
    if "shadow" in compact:
        inferred_traits.append("shadow")
    if "purified" in compact:
        inferred_traits.append("purified")
    if "shiny" in compact:
        inferred_traits.append("shiny")
    if "costume" in compact:
        inferred_traits.append("costume")
    if "0star" in compact or "0*" in compact:
        inferred_traits.append("0star")
    if "1star" in compact or "1*" in compact:
        inferred_traits.append("1star")
    if "2star" in compact or "2*" in compact:
        inferred_traits.append("2star")
    if "3star" in compact or "3*" in compact:
        inferred_traits.append("3star")

    looks_like_normal_negation = all(
        token in compact
        for token in ("!dynamax", "!lucky", "!shadow", "!purified", "!shiny", "!costume", "!4*", "!mega")
    )
    if "normal" in compact or looks_like_normal_negation:
        inferred_traits.append("normal")

    include_total = compact in {"all", "query_all"} or compact.startswith("all_") or not inferred_traits

    return {
        "include_total": include_total,
        "include_visible_traits": not inferred_traits,
        "add_traits": inferred_traits,
    }


def _write_unknowns_csv(path: Path, unknowns: list[UnknownObservation]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["screenshot_path", "pass_name", "slot_index", "best_candidate", "best_score"],
        )
        writer.writeheader()
        for item in unknowns:
            writer.writerow(
                {
                    "screenshot_path": str(item.screenshot_path),
                    "pass_name": item.pass_name,
                    "slot_index": item.slot_index,
                    "best_candidate": item.best_candidate,
                    "best_score": item.best_score,
                }
            )


