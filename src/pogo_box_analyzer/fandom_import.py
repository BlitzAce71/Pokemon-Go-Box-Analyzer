from __future__ import annotations

import csv
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote


REGIONAL_LABELS = {
    "alolan": "Alolan",
    "galarian": "Galarian",
    "hisuian": "Hisuian",
    "paldean": "Paldean",
}

SKIP_FORM_KEYWORDS = {
    "mega",
    "primal",
    "gigantamax",
    "gmax",
}


@dataclass(frozen=True)
class FandomPokemonEntry:
    species: str
    form_text: str
    image_src: str
    is_greyed_out: bool


class _FandomListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[FandomPokemonEntry] = []

        self.in_item = False
        self.item_depth = 0
        self.name_depth = 0
        self.form_depth = 0

        self._species_parts: list[str] = []
        self._form_parts: list[str] = []
        self._image_src = ""
        self._is_greyed_out = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: ("" if v is None else v) for k, v in attrs}

        if tag == "div":
            cls = attr.get("class", "")
            classes = set(cls.split())

            if not self.in_item and "pogo-list-item" in classes:
                self._start_item(classes)
                return

            if self.in_item:
                self.item_depth += 1
                if "pogo-list-item-name" in classes:
                    self.name_depth = 1
                elif "pogo-list-item-form" in classes:
                    self.form_depth = 1
                elif self.name_depth > 0:
                    self.name_depth += 1
                elif self.form_depth > 0:
                    self.form_depth += 1
            return

        if self.in_item and tag == "img":
            if attr.get("data-relevant") == "1" and not self._image_src:
                self._image_src = attr.get("src", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if not self.in_item:
            return

        if tag == "div":
            if self.name_depth > 0:
                self.name_depth -= 1
            if self.form_depth > 0:
                self.form_depth -= 1

            self.item_depth -= 1
            if self.item_depth == 0:
                self._finish_item()

    def handle_data(self, data: str) -> None:
        if not self.in_item:
            return

        value = data.strip()
        if not value:
            return

        if self.name_depth > 0:
            self._species_parts.append(value)
        elif self.form_depth > 0:
            self._form_parts.append(value)

    def _start_item(self, classes: set[str]) -> None:
        self.in_item = True
        self.item_depth = 1
        self.name_depth = 0
        self.form_depth = 0
        self._species_parts = []
        self._form_parts = []
        self._image_src = ""
        self._is_greyed_out = "greyed-out" in classes

    def _finish_item(self) -> None:
        species = _clean_space(" ".join(self._species_parts))
        form_text = _clean_space(" ".join(self._form_parts))

        if species and self._image_src:
            self.entries.append(
                FandomPokemonEntry(
                    species=species,
                    form_text=form_text,
                    image_src=self._image_src,
                    is_greyed_out=self._is_greyed_out,
                )
            )

        self.in_item = False
        self.item_depth = 0
        self.name_depth = 0
        self.form_depth = 0


def import_fandom_catalog(
    html_path: Path,
    assets_dir: Path,
    catalog_base_dir: Path,
    output_catalog_csv: Path,
    include_not_released: bool = False,
    skip_mega_like_forms: bool = True,
) -> dict[str, int | str]:
    parser = _FandomListParser()
    parser.feed(html_path.read_text(encoding="utf-8", errors="ignore"))

    icons_dir = catalog_base_dir / "icons_fandom"
    icons_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    seen_identity: set[tuple[str, str, str, str]] = set()

    missing_assets = 0
    skipped_not_released = 0
    skipped_mega_like = 0
    skipped_invalid = 0
    deduped = 0

    for entry in parser.entries:
        if not include_not_released and entry.is_greyed_out:
            skipped_not_released += 1
            continue

        form_norm, regional_variant = _normalize_form(entry.form_text)

        if skip_mega_like_forms and _has_skip_form_keyword(form_norm):
            skipped_mega_like += 1
            continue

        image_name = _decode_local_image_name(entry.image_src)
        if not image_name:
            skipped_invalid += 1
            continue

        src_file = _resolve_asset_path(assets_dir, image_name)
        if src_file is None:
            missing_assets += 1
            continue

        identity_key = (entry.species, form_norm, "", regional_variant)
        if identity_key in seen_identity:
            deduped += 1
            continue
        seen_identity.add(identity_key)

        output_name = _build_output_file_name(entry.species, form_norm, regional_variant, src_file.suffix)
        dest_file = icons_dir / output_name
        dest_file = _dedupe_file_name(dest_file)
        shutil.copyfile(src_file, dest_file)

        rows.append(
            {
                "image": f"icons_fandom/{dest_file.name}",
                "species": entry.species,
                "form": form_norm,
                "costume": "",
                "regional_variant": regional_variant,
            }
        )

    rows_sorted = sorted(rows, key=lambda r: (r["species"], r["form"], r["regional_variant"], r["image"]))

    output_catalog_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_catalog_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["image", "species", "form", "costume", "regional_variant"],
        )
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow(row)

    return {
        "html_items_parsed": len(parser.entries),
        "catalog_rows_written": len(rows_sorted),
        "missing_assets": missing_assets,
        "skipped_not_released": skipped_not_released,
        "skipped_mega_like": skipped_mega_like,
        "skipped_invalid": skipped_invalid,
        "deduped": deduped,
        "catalog_csv": str(output_catalog_csv),
        "icons_dir": str(icons_dir),
    }


def merge_catalog_files(base_catalog_csv: Path, imported_catalog_csv: Path, output_catalog_csv: Path) -> dict[str, int | str]:
    base_rows = _read_catalog_rows(base_catalog_csv)
    imported_rows = _read_catalog_rows(imported_catalog_csv)

    merged: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in base_rows:
        merged[_identity_key(row)] = row

    added = 0
    for row in imported_rows:
        key = _identity_key(row)
        if key not in merged:
            merged[key] = row
            added += 1

    merged_rows = sorted(merged.values(), key=lambda r: (r["species"], r["form"], r["costume"], r["regional_variant"], r["image"]))

    output_catalog_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_catalog_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["image", "species", "form", "costume", "regional_variant"],
        )
        writer.writeheader()
        for row in merged_rows:
            writer.writerow(row)

    return {
        "base_rows": len(base_rows),
        "imported_rows": len(imported_rows),
        "added_rows": added,
        "merged_rows": len(merged_rows),
        "output_catalog": str(output_catalog_csv),
    }


def _read_catalog_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    rows: list[dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(
                {
                    "image": (row.get("image") or "").strip(),
                    "species": (row.get("species") or "").strip(),
                    "form": (row.get("form") or "").strip(),
                    "costume": (row.get("costume") or "").strip(),
                    "regional_variant": (row.get("regional_variant") or "").strip(),
                }
            )
    return [r for r in rows if r["image"] and r["species"]]


def _identity_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (row["species"], row["form"], row["costume"], row["regional_variant"])


def _decode_local_image_name(image_src: str) -> str:
    image_src = image_src.strip()
    if not image_src:
        return ""

    if "/" in image_src:
        image_name = image_src.rsplit("/", 1)[-1]
    else:
        image_name = image_src

    image_name = image_name.split("?", 1)[0]
    return unquote(image_name)


def _resolve_asset_path(assets_dir: Path, image_name: str) -> Path | None:
    candidates = [
        assets_dir / image_name,
        assets_dir / image_name.replace("%", "%25"),
        assets_dir / image_name.replace("?", "%3F"),
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def _normalize_form(form_text: str) -> tuple[str, str]:
    text = _clean_space(form_text)
    if not text:
        return "", ""

    regional_variant = ""
    for token, label in REGIONAL_LABELS.items():
        pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
        if pattern.search(text):
            regional_variant = label
            text = pattern.sub("", text)
            break

    text = re.sub(r"\bforms?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bformes?\b", "", text, flags=re.IGNORECASE)
    text = _clean_space(text)

    return text, regional_variant


def _has_skip_form_keyword(form_text: str) -> bool:
    lowered = form_text.lower()
    return any(keyword in lowered for keyword in SKIP_FORM_KEYWORDS)


def _build_output_file_name(species: str, form: str, regional_variant: str, suffix: str) -> str:
    parts = [species]
    if regional_variant:
        parts.append(regional_variant)
    if form:
        parts.append(form)

    slug = "_".join(_slugify(part) for part in parts if part)
    if not slug:
        slug = "pokemon"

    suffix = suffix if suffix else ".webp"
    return f"{slug}{suffix.lower()}"


def _dedupe_file_name(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    idx = 2
    while True:
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def _slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def write_import_summary(path: Path, summary: dict[str, int | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
