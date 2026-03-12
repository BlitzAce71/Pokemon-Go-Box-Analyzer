from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .image_ops import hamming_distance
from .models import Observation, SpeciesKey


@dataclass
class AggregateRow:
    species_key: SpeciesKey
    total: int = 0
    costume: int = 0
    traits: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class _MergedInstance:
    species_key: SpeciesKey
    cp: int | None
    icon_hash: int | None
    include_total: bool = False
    traits: set[str] = field(default_factory=set)


_NON_NORMAL_TRAITS = {
    "shiny",
    "lucky",
    "shadow",
    "purified",
    "dynamax",
    "costume",
    "mega_capable",
    "hundo_4star",
    "0star",
    "1star",
    "2star",
    "3star",
}


def aggregate_observations(
    observations: list[Observation],
    trait_columns: list[str],
    visible_special_traits: list[str],
) -> dict[SpeciesKey, AggregateRow]:
    # Merges cross-pass hits for the same Pokemon instance using species + CP + icon hash similarity.
    buckets: dict[SpeciesKey, list[_MergedInstance]] = {}
    has_explicit_normal_pass = any("normal" in obs.hidden_traits for obs in observations)
    has_explicit_costume_pass = any("costume" in obs.hidden_traits for obs in observations)

    ordered = sorted(
        observations,
        key=lambda obs: (
            0 if obs.include_total else 1,
            obs.pass_name,
            obs.screenshot_path.as_posix(),
            obs.slot_index,
        ),
    )

    for obs in ordered:
        aggregate_key = SpeciesKey(
            species=obs.species_key.species,
            form=obs.species_key.form,
            costume="",
            regional_variant=obs.species_key.regional_variant,
        )

        bucket = buckets.setdefault(aggregate_key, [])
        instance = _find_matching_instance(bucket, obs)
        if instance is None:
            instance = _MergedInstance(species_key=aggregate_key, cp=obs.cp, icon_hash=obs.icon_hash)
            bucket.append(instance)
        else:
            if instance.cp is None and obs.cp is not None:
                instance.cp = obs.cp
            if instance.icon_hash is None and obs.icon_hash is not None:
                instance.icon_hash = obs.icon_hash

        instance.include_total = instance.include_total or obs.include_total
        instance.traits.update(obs.hidden_traits)
        instance.traits.update(obs.visible_traits)

        # Costume label in catalog still contributes when explicit costume pass is not available.
        if obs.species_key.costume.strip() and not has_explicit_costume_pass:
            instance.traits.add("costume")

    rows: dict[SpeciesKey, AggregateRow] = {}

    for bucket in buckets.values():
        for inst in bucket:
            row = rows.get(inst.species_key)
            if row is None:
                row = AggregateRow(species_key=inst.species_key)
                for trait in trait_columns:
                    row.traits[trait] = 0
                rows[inst.species_key] = row

            if inst.include_total:
                row.total += 1

            has_costume = "costume" in inst.traits
            if has_costume:
                row.costume += 1

            for trait in inst.traits:
                if trait in {"costume", "normal"}:
                    continue
                if trait in row.traits:
                    row.traits[trait] += 1

            if "normal" in row.traits:
                if has_explicit_normal_pass:
                    if "normal" in inst.traits:
                        row.traits["normal"] += 1
                elif inst.include_total and not any(t in inst.traits for t in _NON_NORMAL_TRAITS):
                    row.traits["normal"] += 1

    return rows


def _find_matching_instance(bucket: list[_MergedInstance], obs: Observation) -> _MergedInstance | None:
    if not bucket:
        return None

    best: _MergedInstance | None = None
    best_score = float("-inf")

    for inst in bucket:
        score = 0.0

        if obs.cp is not None and inst.cp is not None:
            if obs.cp != inst.cp:
                continue
            score += 3.0
        elif obs.cp is None and inst.cp is None:
            score += 0.4
        else:
            score += 1.2

        if obs.icon_hash is not None and inst.icon_hash is not None:
            dist = hamming_distance(obs.icon_hash, inst.icon_hash)
            if dist > 12 and not (obs.cp is not None and inst.cp is not None and obs.cp == inst.cp):
                continue
            score += max(0.0, 2.0 - (dist / 6.0))
        elif obs.icon_hash is not None or inst.icon_hash is not None:
            score += 0.3

        if score > best_score:
            best_score = score
            best = inst

    if best is None:
        return None

    min_score = 2.3 if obs.cp is not None else 2.8
    if best_score < min_score:
        return None

    return best


def write_species_csv(
    destination: Path,
    rows: dict[SpeciesKey, AggregateRow],
    trait_columns: list[str],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "species",
        "form",
        "costume",
        "regional_variant",
        "total",
        *trait_columns,
    ]

    ordered = sorted(
        rows.values(),
        key=lambda r: (r.species_key.species, r.species_key.form, r.species_key.regional_variant),
    )

    with destination.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for row in ordered:
            data = {
                "species": row.species_key.species,
                "form": row.species_key.form,
                "costume": row.costume,
                "regional_variant": row.species_key.regional_variant,
                "total": row.total,
            }
            for trait in trait_columns:
                data[trait] = row.traits.get(trait, 0)
            writer.writerow(data)
