from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int


_RAPID_OCR_ENGINE = None
_RAPID_OCR_LOCK = threading.Lock()


class SpeciesNameMatcher:
    def __init__(self, species_names: list[str]) -> None:
        uniq = sorted({name.strip() for name in species_names if name.strip()})
        self.species_names = uniq

        self._key_to_species: dict[str, str] = {}
        self._species_to_key: dict[str, str] = {}

        for species in uniq:
            key = _name_key(species)
            if not key:
                continue
            self._species_to_key[species] = key
            self._key_to_species.setdefault(key, species)

        # Helpful aliases for common OCR ambiguities.
        for alias, canonical in {
            "nidoranf": "Nidoran\u2640",
            "nidoranm": "Nidoran\u2642",
            "nidoran9": "Nidoran\u2640",
            "nidorano": "Nidoran\u2642",
            "nidoranu": "Nidoran\u2642",
            "nidoranfemale": "Nidoran\u2640",
            "nidoranmale": "Nidoran\u2642",
            "mrmime": "Mr. Mime",
            "mrrime": "Mr. Rime",
            "farfetchd": "Farfetch'd",
            "sirfetchd": "Sirfetch'd",
            "flabebe": "Flabebe",
            "type null": "Type: Null",
            "typenull": "Type: Null",
        }.items():
            if canonical in self._species_to_key:
                self._key_to_species.setdefault(alias, canonical)

    def match(self, raw_text: str) -> tuple[str | None, float]:
        query_key = _name_key(raw_text)
        if len(query_key) < 3:
            return None, 0.0

        direct = self._key_to_species.get(query_key)
        if direct is not None:
            return direct, 1.0

        if query_key.startswith("nidoran"):
            special = self._match_nidoran_variant(query_key)
            if special is not None:
                return special

        best_species: str | None = None
        best_score = 0.0
        second_best = 0.0

        for species, species_key in self._species_to_key.items():
            score = SequenceMatcher(a=query_key, b=species_key).ratio()
            if score > best_score:
                second_best = best_score
                best_score = score
                best_species = species
            elif score > second_best:
                second_best = score

        if best_species is None:
            return None, 0.0

        # Keep short strings strict; allow longer OCR strings to be noisier.
        if len(query_key) >= 8:
            min_score = 0.78
            min_margin = 0.02
        elif len(query_key) >= 6:
            min_score = 0.82
            min_margin = 0.03
        else:
            min_score = 0.88
            min_margin = 0.06

        if best_score < min_score:
            return None, best_score

        if (best_score - second_best) < min_margin:
            return None, best_score

        return best_species, best_score

    def _match_nidoran_variant(self, query_key: str) -> tuple[str | None, float] | None:
        female_key = self._species_to_key.get("Nidoran\u2640")
        male_key = self._species_to_key.get("Nidoran\u2642")
        if female_key is None or male_key is None:
            return None

        female_score = SequenceMatcher(a=query_key, b=female_key).ratio()
        male_score = SequenceMatcher(a=query_key, b=male_key).ratio()

        if female_score < 0.65 and male_score < 0.65:
            return None

        if female_score >= male_score:
            return "Nidoran\u2640", female_score
        return "Nidoran\u2642", male_score


def run_windows_ocr(image_path: Path, script_path: Path | None = None) -> list[OcrLine]:
    if script_path is None:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "windows_ocr.ps1"

    if not script_path.exists() or not image_path.exists():
        return []

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-ImagePath",
        str(image_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=True,
        )
    except Exception:
        return []

    stdout = proc.stdout.strip()
    if not stdout:
        return []

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    lines = payload.get("lines", [])
    out: list[OcrLine] = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        out.append(
            OcrLine(
                text=text,
                x=int(line.get("x", 0)),
                y=int(line.get("y", 0)),
                w=int(line.get("w", 0)),
                h=int(line.get("h", 0)),
            )
        )
    return out


def run_rapidocr(image_path: Path) -> list[OcrLine]:
    if not image_path.exists():
        return []

    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return []

    global _RAPID_OCR_ENGINE
    with _RAPID_OCR_LOCK:
        if _RAPID_OCR_ENGINE is None:
            try:
                _RAPID_OCR_ENGINE = RapidOCR()
            except Exception:
                return []
        engine = _RAPID_OCR_ENGINE

    try:
        ocr_input = _prepare_ocr_image(image_path)
        # Pokemon GO screenshots are upright; skipping cls reduces memory and latency.
        result, _ = engine(ocr_input, use_cls=False)
    except Exception:
        return []

    if not result:
        return []

    out: list[OcrLine] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue

        box = item[0]
        text = str(item[1]).strip()
        if not text:
            continue

        x, y, w, h = _bbox_from_points(box)
        out.append(OcrLine(text=text, x=x, y=y, w=w, h=h))

    return out



def _prepare_ocr_image(image_path: Path, max_side: int = 1280):
    try:
        with Image.open(image_path) as src:
            img = src.convert("RGB")
    except Exception:
        return str(image_path)

    w, h = img.size
    longest = max(w, h)
    if longest <= max_side:
        return img

    scale = max_side / float(longest)
    new_size = (
        max(1, int(round(w * scale))),
        max(1, int(round(h * scale))),
    )
    return img.resize(new_size, Image.Resampling.BILINEAR)

def run_ocr(image_path: Path, script_path: Path | None = None) -> list[OcrLine]:
    # Prefer native Windows OCR when available, then RapidOCR fallback.
    if os.name == "nt":
        lines = run_windows_ocr(image_path=image_path, script_path=script_path)
        if lines:
            return lines
        return run_rapidocr(image_path=image_path)

    # On non-Windows hosts (e.g. Render Linux), use RapidOCR.
    return run_rapidocr(image_path=image_path)


def _bbox_from_points(points) -> tuple[int, int, int, int]:
    xs: list[float] = []
    ys: list[float] = []

    if isinstance(points, (list, tuple)):
        for pt in points:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            try:
                px = float(pt[0])
                py = float(pt[1])
            except Exception:
                continue
            xs.append(px)
            ys.append(py)

    if not xs or not ys:
        return 0, 0, 0, 0

    min_x = min(xs)
    min_y = min(ys)
    max_x = max(xs)
    max_y = max(ys)
    x = int(round(min_x))
    y = int(round(min_y))
    w = int(round(max(0.0, max_x - min_x)))
    h = int(round(max(0.0, max_y - min_y)))
    return x, y, w, h


def pick_cell_cp_value(lines: list[OcrLine], cell_bbox: tuple[int, int, int, int]) -> int | None:
    x0, y0, x1, y1 = cell_bbox
    cw = max(1, x1 - x0)
    ch = max(1, y1 - y0)

    x_pad = int(round(cw * 0.06))
    expanded_x0 = x0 - x_pad
    expanded_x1 = x1 + x_pad
    cp_y_min = y0 + (0.00 * ch)
    cp_y_max = y0 + (0.34 * ch)

    candidates: list[tuple[float, int]] = []

    for line in lines:
        line_x0 = line.x
        line_y0 = line.y
        line_x1 = line.x + max(1, line.w)
        line_y1 = line.y + max(1, line.h)

        overlap_w = min(expanded_x1, line_x1) - max(expanded_x0, line_x0)
        if overlap_w <= 0:
            continue

        line_w = max(1.0, float(line_x1 - line_x0))
        if (overlap_w / line_w) < 0.45:
            continue

        cy = line_y0 + ((line_y1 - line_y0) / 2.0)
        if cy < cp_y_min or cy > cp_y_max:
            continue

        cp = _parse_cp_from_text(line.text)
        if cp is None:
            continue

        rel_y = (cy - y0) / ch
        score = 1.0
        if "cp" in line.text.lower():
            score += 0.8
        score += min(0.8, line.w / max(1.0, cw))
        score -= abs(rel_y - 0.12)

        candidates.append((score, cp))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def pick_cell_name_text(lines: list[OcrLine], cell_bbox: tuple[int, int, int, int]) -> str | None:
    x0, y0, x1, y1 = cell_bbox
    cw = max(1, x1 - x0)
    ch = max(1, y1 - y0)

    x_pad = int(round(cw * 0.08))
    expanded_x0 = x0 - x_pad
    expanded_x1 = x1 + x_pad
    name_y_min = y0 + (0.22 * ch)
    name_y_max = y1 + (0.08 * ch)

    candidates: list[tuple[float, str]] = []

    for line in lines:
        line_x0 = line.x
        line_y0 = line.y
        line_x1 = line.x + max(1, line.w)
        line_y1 = line.y + max(1, line.h)

        overlap_w = min(expanded_x1, line_x1) - max(expanded_x0, line_x0)
        if overlap_w <= 0:
            continue

        line_w = max(1.0, float(line_x1 - line_x0))
        if (overlap_w / line_w) < 0.45:
            continue

        cy = line_y0 + ((line_y1 - line_y0) / 2.0)
        if cy < name_y_min or cy > name_y_max:
            continue

        txt = _clean_ocr_text(line.text)
        letters = sum(1 for c in txt if c.isalpha())
        digits = sum(1 for c in txt if c.isdigit())
        if letters < 3:
            continue

        rel_y = (cy - y0) / ch
        score = float(letters) - (1.8 * digits)
        score -= 1.1 * abs(rel_y - 0.72)
        # Strongly de-prioritize names in the partially visible next row.
        if rel_y > 1.0:
            score -= 2.0 + (8.0 * (rel_y - 1.0))
        # Prefer wider text in the expected name area.
        score += min(5.0, (line.w / max(1.0, cw)) * 5.0)

        candidates.append((score, txt))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _parse_cp_from_text(text: str) -> int | None:
    cleaned = _clean_ocr_text(text).lower()
    cleaned = cleaned.replace("cp", " ")
    digits = re.sub(r"[^0-9]", "", cleaned)
    if not digits:
        return None

    try:
        cp = int(digits)
    except ValueError:
        return None

    if cp <= 0 or cp > 99999:
        return None
    return cp


def _clean_ocr_text(text: str) -> str:
    text = text.replace("•", " ")
    text = text.replace("-", "-")
    text = text.replace("'", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _name_key(text: str) -> str:
    text = _clean_ocr_text(text)
    text = text.replace("\u2640", "f")
    text = text.replace("\u2642", "m")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text
