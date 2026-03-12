from __future__ import annotations

import colorsys
import re
from pathlib import Path

from PIL import Image

from .config import Rect
from .image_ops import crop_rect, extract_foreground, grayscale_similarity

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_COMPARE_SIZE = (56, 56)

_COLOR_GATES: dict[str, dict[str, object]] = {
    "shadow": {
        "h_ranges": [(255.0, 300.0)],
        "s_min": 0.25,
        "v_min": 0.25,
        "v_max": 1.0,
        "min_ratio": 0.05,
    },
    "purified": {
        "h_ranges": [(170.0, 205.0)],
        "s_min": 0.20,
        "v_min": 0.50,
        "v_max": 1.0,
        "min_ratio": 0.05,
    },
    "dynamax": {
        "h_ranges": [(315.0, 360.0), (0.0, 10.0)],
        "s_min": 0.20,
        "v_min": 0.45,
        "v_max": 1.0,
        "min_ratio": 0.012,
    },
    "shiny": {
        "h_ranges": [(190.0, 235.0)],
        "s_min": 0.22,
        "v_min": 0.20,
        "v_max": 0.80,
        "min_ratio": 0.002,
    },
}


class TraitTemplateStore:
    def __init__(self, templates: dict[str, list[Image.Image]], template_count: int):
        self.templates = templates
        self.template_count = template_count

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        trait_rois: dict[str, Rect] | None = None,
    ) -> "TraitTemplateStore":
        templates: dict[str, list[Image.Image]] = {}
        template_count = 0
        if not directory.exists():
            return cls(templates, template_count)

        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue

            trait = _normalize_trait_name(path.stem)
            if not trait:
                continue

            image = Image.open(path).convert("RGB")
            image = _prepare_template_image(image=image, trait=trait, trait_rois=trait_rois)
            templates.setdefault(trait, []).append(image)
            template_count += 1

        return cls(templates, template_count)


def detect_visible_traits(
    cell_image: Image.Image,
    trait_templates: TraitTemplateStore,
    trait_rois: dict[str, Rect],
    thresholds: dict[str, float],
) -> set[str]:
    found: set[str] = set()
    scores: dict[str, float] = {}

    for trait, template_group in trait_templates.templates.items():
        roi_rect = trait_rois.get(trait)
        if roi_rect is None:
            continue

        region = crop_rect(cell_image, roi_rect)
        region_focus = extract_foreground(region, white_threshold=245, min_size=6)

        if trait == "dynamax" and _detect_dynamax_symbol(region):
            found.add("dynamax")
            scores[trait] = 1.0
            continue

        best_similarity = max(_masked_template_similarity(region_focus, template) for template in template_group)
        scores[trait] = best_similarity

        threshold = thresholds.get(trait, 0.75)
        if best_similarity < threshold:
            continue

        gate = _COLOR_GATES.get(trait)
        if gate is not None:
            ratio = _color_ratio(region_focus, gate)
            if ratio < float(gate["min_ratio"]):
                continue

        found.add(trait)

    # Shadow and purified are mutually exclusive states.
    if "shadow" in found and "purified" in found:
        if scores.get("shadow", 0.0) >= scores.get("purified", 0.0):
            found.discard("purified")
        else:
            found.discard("shadow")

    return found


def _prepare_template_image(image: Image.Image, trait: str, trait_rois: dict[str, Rect] | None) -> Image.Image:
    # If users provide full-slot examples, auto-crop to the trait ROI for consistency.
    if trait_rois is not None and trait in trait_rois:
        if image.width >= 180 and image.height >= 180:
            image = crop_rect(image, trait_rois[trait])

    return extract_foreground(image, white_threshold=245, min_size=6)


def _masked_template_similarity(region: Image.Image, template: Image.Image) -> float:
    # Fallback baseline for very sparse templates.
    baseline = grayscale_similarity(region, template)

    region_rgb = region.convert("RGB").resize(_COMPARE_SIZE, Image.Resampling.BILINEAR)
    template_rgb = template.convert("RGB").resize(_COMPARE_SIZE, Image.Resampling.BILINEAR)

    region_gray = list(region_rgb.convert("L").getdata())
    template_gray = list(template_rgb.convert("L").getdata())

    region_mask = [1 if min(px) < 240 else 0 for px in region_rgb.getdata()]
    template_mask = [1 if min(px) < 240 else 0 for px in template_rgb.getdata()]

    fg_idx = [i for i, m in enumerate(template_mask) if m == 1]
    if len(fg_idx) < 18:
        return baseline

    fg_mae = sum(abs(region_gray[i] - template_gray[i]) for i in fg_idx) / float(len(fg_idx))
    fg_sim = max(0.0, 1.0 - (fg_mae / 255.0))

    fg_presence = sum(region_mask[i] for i in fg_idx) / float(len(fg_idx))

    bg_idx = [i for i, m in enumerate(template_mask) if m == 0]
    if bg_idx:
        bg_noise = sum(region_mask[i] for i in bg_idx) / float(len(bg_idx))
    else:
        bg_noise = 0.0

    # Emphasize matching at template-symbol pixels and penalize extra clutter elsewhere.
    masked_score = max(0.0, min(1.0, (fg_sim * fg_presence) - (0.20 * bg_noise)))
    return max(0.0, min(1.0, (0.90 * masked_score) + (0.10 * baseline)))


def _detect_dynamax_symbol(image: Image.Image) -> bool:
    probe = image.convert("RGB").resize((80, 80), Image.Resampling.BILINEAR)
    pixels = list(probe.getdata())

    mask: list[int] = []
    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        hue = h * 360.0
        is_magenta = 300.0 <= hue <= 355.0 and s >= 0.25 and v >= 0.45
        mask.append(1 if is_magenta else 0)

    idx = [i for i, m in enumerate(mask) if m == 1]
    ratio = len(idx) / float(len(mask))
    if ratio < 0.009:
        return False

    xs = [i % 80 for i in idx]
    ys = [i // 80 for i in idx]
    width = max(xs) - min(xs) + 1
    height = max(ys) - min(ys) + 1
    if width < 20 or height < 8:
        return False

    bbox_area = float(width * height)
    density = len(idx) / bbox_area if bbox_area > 0 else 0.0
    if density > 0.50:
        return False

    return True


def _color_ratio(image: Image.Image, gate: dict[str, object]) -> float:
    probe = image.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    pixels = list(probe.getdata())

    h_ranges: list[tuple[float, float]] = list(gate["h_ranges"])  # type: ignore[assignment]
    s_min = float(gate["s_min"])
    v_min = float(gate["v_min"])
    v_max = float(gate["v_max"])

    matched = 0
    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        hue = h * 360.0

        if s < s_min or v < v_min or v > v_max:
            continue

        if any(lo <= hue <= hi for lo, hi in h_ranges):
            matched += 1

    return matched / float(len(pixels))


def _normalize_trait_name(stem: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    name = re.sub(r"_(icon|template|sample)$", "", name)
    name = re.sub(r"_?\d+$", "", name)

    aliases = {
        "dmax": "dynamax",
    }
    return aliases.get(name, name)
