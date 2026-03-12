# PoGo Box Analyzer (MVP)

This project analyzes Pokemon GO box screenshots and exports a species-level spreadsheet with trait counts.

It is designed around your rules:
- One row per species key (`species + form + regional_variant`)
- Mega is **not** a separate species
- Mega is counted as `mega_capable` from a dedicated search pass
- `4*` (hundo) is counted from a dedicated search pass
- Traits are sourced from dedicated search passes (recommended), then merged by Pokemon identity across passes
- Costume is counted in the numeric `costume` column (and excluded from `normal`)

## Status

This is a configurable MVP. It works best when:
- screenshots are captured consistently
- the box is sorted by Pokemon number
- you maintain a local species icon catalog (`data/species_catalog/catalog.csv`)
- optional: you provide trait icon templates (`data/templates/traits/*.png`) for legacy all-pass symbol inference

## Install

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Input Layout

Default folder structure:

```text
input/
  all/
    001.png
    002.png
  query_dynamax/
  query_lucky/
  query_shadow/
  query_purified/
  query_shiny/
  query_costume/
  query_4star/
  query_mega/
  query_normal/
```

Optional manifest file (`input/manifest.csv`) with columns:
- `image_path`
- `pass_name`

If no manifest is present, the app auto-discovers image files by folder name.

## Species Catalog

File: `data/species_catalog/catalog.csv`

Columns:
- `image` (relative path under `data/species_catalog/`)
- `species`
- `form`
- `costume`
- `regional_variant`

Example row:

```csv
image,species,form,costume,regional_variant
icons/charizard.png,Charizard,,,
icons/meowth_alolan.png,Meowth,,,Alolan
```

You can keep adding references over time as new Pokemon/forms are released.

## Trait Templates (Optional)

This is optional when using dedicated search passes for traits.

Place trait templates in `data/templates/traits/`.

Accepted naming:
- exact trait names: `shiny.png`, `shadow.png`, `costume.png`, etc.
- numbered samples: `shiny1.png`, `shiny2.png`, `shadow1.png`, etc.

Template format:
- You can provide either tight symbol crops or full-slot example crops.
- If full-slot crops are provided, the analyzer auto-crops each template to that trait's ROI from config.

These are matched with masked similarity inside configurable ROIs.

## Run

```bash
pogo-box-analyzer run \
  --input-dir input \
  --output-dir output \
  --config config/default_config.json \
  --catalog data/species_catalog/catalog.csv \
  --catalog-images data/species_catalog \
  --trait-templates data/templates/traits
```

Or without installing script entrypoint:

```bash
python -m pogo_box_analyzer run
```

## Output

- `output/species_counts.csv`
- `output/unknown_slots.csv`
- `output/unknown_crops/` (icon crops that did not match your catalog)
- `output/run_summary.json`

`species_counts.csv` columns:
- `species,form,costume,regional_variant,total` (`costume` is a count, not a label)
- `normal,shiny,lucky,shadow,purified,dynamax,mega_capable,hundo_4star,0star,1star,2star,3star`

## Config

Default config: `config/default_config.json`

Tune these first:
- grid coordinates (`grid`) for your screenshot dimensions
- icon crop (`icon_roi`)
- trait search windows (`trait_rois`)
- thresholds (`trait_thresholds`, `species_match_threshold`)

Pass behavior is controlled in `pass_rules`:
- `all` contributes to `total` population
- `query_dynamax` adds `dynamax`
- `query_lucky` adds `lucky`
- `query_shadow` adds `shadow`
- `query_purified` adds `purified`
- `query_shiny` adds `shiny`
- `query_costume` adds `costume`
- `query_4star` adds `hundo_4star`
- `query_mega` adds `mega_capable`
- `query_normal` adds `normal`

## Practical Notes

- Nicknames do not break this approach because species matching is OCR/species-aware, not nickname-dependent.
- Overlap between passes is expected; instances are merged across passes using species + CP + icon hash similarity.
- New species/forms are supported by adding new reference images to your catalog.
- Unknown matches are exported for quick labeling and iterative improvement.
- Dedupe is conservative and only removes near-identical repeated slots within the same pass.

## Current Limitations

- Accuracy depends on your catalog quality and template quality.
- Grid coordinates are not auto-calibrated yet.
- No GUI review tool yet (CSV + unknown crops is the current review flow).

## Catalog Bootstrap

After a run with unknowns, generate a deduped labeling draft:

```bash
python -m pogo_box_analyzer bootstrap-catalog \
  --unknown-crops output/unknown_crops \
  --catalog-base data/species_catalog \
  --draft-csv data/species_catalog/catalog_draft.csv \
  --passes all
```

Then fill `species/form/costume/regional_variant` in `catalog_draft.csv` and copy completed rows into `catalog.csv`.

## Fandom Import (Bulk Catalog Seed)

If you saved [List of Pokemon](https://pokemongo.fandom.com/wiki/List_of_Pok%C3%A9mon) as a local webpage, you can bulk-seed the catalog:

```bash
python -m pogo_box_analyzer import-fandom \
  --html "data/source/fandom/List of Pokemon.html" \
  --assets-dir "data/source/fandom/List of Pokemon_files" \
  --catalog-base data/species_catalog \
  --import-csv data/species_catalog/catalog_fandom.csv \
  --catalog data/species_catalog/catalog.csv
```

Default behavior:
- excludes unreleased (greyed-out) entries
- excludes Mega/Primal/Gigantamax forms
- merges imported rows into `catalog.csv`

Outputs:
- `data/species_catalog/icons_fandom/`
- `data/species_catalog/catalog_fandom.csv`
- `data/species_catalog/fandom_import_summary.json`

## Mobile Web App (iPhone + Android)

Run the built-in phone web UI/API:

```bash
python -m pogo_box_analyzer serve-web --host 0.0.0.0 --port 8787
```

Then on your phone browser:
- connect to the same Wi-Fi as the server machine
- open `http://<your-computer-ip>:8787`
- upload mixed screenshots in one file picker, tap **Analyze**, then **Download CSV** or **Share CSV**

Notes:
- Output is CSV, which can be opened directly on iPhone/Android or shared to a computer.
- iPhone and Android both support file sharing from the browser share sheet.
- For external distribution (outside your local network), host this service on an HTTPS endpoint you control.

## Auto Search-Term Detection

In web mode, uploaded screenshots are treated as `auto` pass by default.

The analyzer reads OCR text in the screenshot search bar and maps each screenshot to:
- `all`
- `query_dynamax`
- `query_lucky`
- `query_shadow`
- `query_purified`
- `query_shiny`
- `query_costume`
- `query_4star`
- `query_mega`
- `query_normal`

If OCR cannot detect a search term, it falls back to `all`.

OCR backend behavior:
- Windows: native Windows OCR (PowerShell script)
- Non-Windows (Render/Linux): RapidOCR (`rapidocr-onnxruntime`)

Run summary includes:
- `auto_pass_screenshots`
- `auto_pass_detected`
- `auto_pass_fallback_all`

## Public Hosting Notes

For public access outside your local Wi-Fi, host `serve-web` behind HTTPS.

Common options:
- Render/Railway/Fly.io as a web service running `python -m pogo_box_analyzer serve-web --host 0.0.0.0 --port $PORT`
- Cloudflare Tunnel to expose your local machine securely with a public URL

Always keep private data in mind before sharing a public endpoint.

Deployment guide: see DEPLOY.md for public URL options (Cloudflare Tunnel and Render).


