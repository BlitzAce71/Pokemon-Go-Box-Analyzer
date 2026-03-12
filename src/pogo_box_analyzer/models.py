from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SpeciesKey:
    species: str
    form: str = ""
    costume: str = ""
    regional_variant: str = ""


@dataclass
class Observation:
    species_key: SpeciesKey
    screenshot_path: Path
    pass_name: str
    slot_index: int
    cp: int | None = None
    icon_hash: int | None = None
    visible_traits: set[str] = field(default_factory=set)
    hidden_traits: set[str] = field(default_factory=set)
    include_total: bool = False
    include_visible_traits: bool = False
    best_species_score: float | None = None


@dataclass
class UnknownObservation:
    screenshot_path: Path
    pass_name: str
    slot_index: int
    best_candidate: str
    best_score: float