from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class GridConfig:
    rows: int
    cols: int
    start_x: float
    start_y: float
    cell_w: float
    cell_h: float
    gap_x: float
    gap_y: float


@dataclass(frozen=True)
class Config:
    grid: GridConfig
    icon_roi: Rect
    trait_rois: dict[str, Rect]
    trait_thresholds: dict[str, float]
    species_match_threshold: float
    pass_rules: dict[str, dict[str, object]]
    trait_columns: list[str]
    visible_special_traits: list[str]
    dedupe_hash_max_distance: int


DEFAULT_CONFIG: dict[str, object] = {
    "grid": {
        "rows": 4,
        "cols": 3,
        "start_x": 0.055,
        "start_y": 0.175,
        "cell_w": 0.285,
        "cell_h": 0.17,
        "gap_x": 0.03,
        "gap_y": 0.022,
    },
    "icon_roi": {
        "x": 0.22,
        "y": 0.26,
        "w": 0.56,
        "h": 0.52,
    },
    "trait_rois": {
        "shiny": {"x": 0.10, "y": 0.18, "w": 0.24, "h": 0.22},
        "lucky": {"x": 0.16, "y": 0.18, "w": 0.70, "h": 0.50},
        "shadow": {"x": 0.08, "y": 0.49, "w": 0.23, "h": 0.24},
        "purified": {"x": 0.08, "y": 0.49, "w": 0.23, "h": 0.24},
        "dynamax": {"x": 0.60, "y": 0.12, "w": 0.30, "h": 0.26},
        "costume": {"x": 0.30, "y": 0.10, "w": 0.40, "h": 0.30},
    },
    "trait_thresholds": {
        "shiny": 0.55,
        "lucky": 0.72,
        "shadow": 0.25,
        "purified": 0.30,
        "dynamax": 0.55,
        "costume": 0.60,
    },
    "species_match_threshold": 0.68,
    "pass_rules": {
        "all": {
            "include_total": True,
            "include_visible_traits": False,
            "add_traits": [],
        },
        "query_dynamax": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["dynamax"],
        },
        "query_lucky": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["lucky"],
        },
        "query_shadow": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["shadow"],
        },
        "query_purified": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["purified"],
        },
        "query_shiny": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["shiny"],
        },
        "query_costume": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["costume"],
        },
        "query_4star": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["hundo_4star"],
        },
        "query_mega": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["mega_capable"],
        },
        "query_normal": {
            "include_total": False,
            "include_visible_traits": False,
            "add_traits": ["normal"],
        },
    },
    "trait_columns": [
        "normal",
        "shiny",
        "lucky",
        "shadow",
        "purified",
        "dynamax",
        "mega_capable",
        "hundo_4star",
        "0star",
        "1star",
        "2star",
        "3star",
    ],
    "visible_special_traits": ["shiny", "lucky", "shadow", "purified", "dynamax"],
    "dedupe_hash_max_distance": 2,
}


def _rect(value: dict[str, float]) -> Rect:
    return Rect(
        x=float(value["x"]),
        y=float(value["y"]),
        w=float(value["w"]),
        h=float(value["h"]),
    )


def load_config(config_path: Path | None = None) -> Config:
    data: dict[str, object]
    if config_path is None:
        data = DEFAULT_CONFIG
    else:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        data = _merge_dict(DEFAULT_CONFIG, loaded)

    grid_obj = data["grid"]
    assert isinstance(grid_obj, dict)

    grid = GridConfig(
        rows=int(grid_obj["rows"]),
        cols=int(grid_obj["cols"]),
        start_x=float(grid_obj["start_x"]),
        start_y=float(grid_obj["start_y"]),
        cell_w=float(grid_obj["cell_w"]),
        cell_h=float(grid_obj["cell_h"]),
        gap_x=float(grid_obj["gap_x"]),
        gap_y=float(grid_obj["gap_y"]),
    )

    icon_roi_obj = data["icon_roi"]
    assert isinstance(icon_roi_obj, dict)

    trait_rois_obj = data["trait_rois"]
    assert isinstance(trait_rois_obj, dict)
    trait_rois = {k: _rect(v) for k, v in trait_rois_obj.items()}

    trait_thresholds_obj = data["trait_thresholds"]
    assert isinstance(trait_thresholds_obj, dict)

    pass_rules_obj = data["pass_rules"]
    assert isinstance(pass_rules_obj, dict)

    trait_columns_obj = data["trait_columns"]
    assert isinstance(trait_columns_obj, list)

    visible_special_traits_obj = data["visible_special_traits"]
    assert isinstance(visible_special_traits_obj, list)

    return Config(
        grid=grid,
        icon_roi=_rect(icon_roi_obj),
        trait_rois=trait_rois,
        trait_thresholds={k: float(v) for k, v in trait_thresholds_obj.items()},
        species_match_threshold=float(data["species_match_threshold"]),
        pass_rules={k: dict(v) for k, v in pass_rules_obj.items()},
        trait_columns=[str(v) for v in trait_columns_obj],
        visible_special_traits=[str(v) for v in visible_special_traits_obj],
        dedupe_hash_max_distance=int(data["dedupe_hash_max_distance"]),
    )


def _merge_dict(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def write_default_config(path: Path) -> None:
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")

