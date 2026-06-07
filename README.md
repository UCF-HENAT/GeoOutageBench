# GeoOutageBench

GeoOutageBench is a benchmark and data-preparation repository for evaluating
ontology-grounded geospatial, temporal, and multimodal question answering over
power-outage resilience knowledge graphs.

The repository contains:

- A benchmark workload with 48 natural-language questions and 72 gold SPARQL
  interpretations.
- Evaluation scripts for the three GeoOutageBench tasks.
- Turtle (`.ttl`) files for several public geospatial and hazard data sources.
- Generators for rebuilding Turtle files from Census, CDC, NOAA, HURDAT,
  EAGLE-I, NASA Black Marble, and outage-map source data.
- Utilities for preparing NL2SPARQL prediction files and maintaining the
  benchmark JSON.

## Repository Layout

```text
GeoOutageBench/
  Benchmark/
    geooutagebench_questions.json       # Main benchmark file
    nl2sparql_prompt.txt                # Prompt template for NL2SPARQL models
    task1_allowed.txt                   # Allowed ontology terms for Task 1
    sparql_queries/                     # Legacy per-query JSON files
    eval_outputs/                       # Example/evaluation output files
    evaluate_task1_nl2temporalsparql.py
    evaluate_task2_ontocheck.py
    evaluate_task3_spatiotemporal_kgqa.py
    make_nl2sparql_prediction_template.py
    generate_geooutagebench_ontology.py

  Counties/
    counties.ttl
    generate_counties_ttl.py
    sameas-mapping.json

  States/
    states.ttl
    generate_states_ttl.py

  CDC/
    svi_2000.ttl ... svi_2022.ttl
    generate_svi_ttl.py

  HURDAT/
    hurdat.ttl
    HurdatKG.py
    hurdat_lookup.py

  StormEvents/
    StormEventsKG.py

  EAGLE-I/
    filter_eaglei.py
    generate_customer_outage_records_ttl.py

  BlackMarble/
    download.py
    pickle_to_image.py
    generate_ntlimage_ttl.py

  OutageMap/
    visualize_outage_maps.py
    generate_outagemap_ttl.py

  satellites/
    satellites.ttl

  utils/
    geooutage_common.py
    polygon_to_hash.py

  requirements.txt                      # Python package dependencies
```

## Quick Start

Clone the repository and fetch Git LFS files. Turtle files are stored with Git
LFS.

```bash
git lfs install
git clone https://github.com/UCF-HENAT/GeoOutageBench.git
cd GeoOutageBench
git lfs pull
```

Create a Python environment. Python 3.10 or newer is recommended.

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
```

Install the Python packages used by the benchmark, evaluators, Turtle
generators, and documented image/Black Marble workflows:

```bash
python -m pip install -r requirements.txt
```

Command examples below use Bash-style `\` line continuations. In Windows
PowerShell, replace each trailing `\` with a backtick or put the command on one
line.


## Data and Git LFS Notes

The repository includes several ready-to-use TTL files:

- `Counties/counties.ttl`
- `States/states.ttl`
- `CDC/svi_2000.ttl`, `CDC/svi_2010.ttl`, `CDC/svi_2014.ttl`,
  `CDC/svi_2016.ttl`, `CDC/svi_2018.ttl`, `CDC/svi_2020.ttl`,
  `CDC/svi_2022.ttl`
- `HURDAT/hurdat.ttl`
- `satellites/satellites.ttl`

Large raw source datasets are not all committed. The EAGLE-I CSVs, NOAA Storm
Events CSVs, NASA Black Marble HDF5/pickle/image products, generated outage-map
images, and complete experiment-specific KG bundles must be supplied from their respective sources or
generated locally.

GeoOutageKG 1.1 Turtle files are also available from the OSF repository at
https://doi.org/10.17605/OSF.IO/QVD8B. In OSF, the GeoOutageBench Turtle files
are located in the `GeoOutageKG 1.1 - GeoOutageBench` folder, which is organized
as:

```text
GeoOutageKG 1.1 - GeoOutageBench/
  CDC/                             # CDC SVI Turtle files
  counties.ttl                     # County resources and geometries
  doe_eaglei_outagerecord/         # EAGLE-I customer outage record Turtle files
  hurdat.ttl                       # HURDAT hurricane track Turtle file
  noaa_stormevents/                # NOAA Storm Events Turtle files
  ntlimage.ttl                     # NASA Black Marble nighttime-light image Turtle file
  outagemap.ttl                    # Outage prediction map Turtle file
  satellites.ttl                   # Satellite metadata Turtle file
  states.ttl                       # State resources and geometries
```

The OSF repository also includes a `GeoOutageKG 1.0` folder containing legacy
Turtle files from the first version of GeoOutageKG, originally published in
2025.

If a `.ttl` file appears as a short text pointer instead of RDF content, run:

```bash
git lfs pull
```

## Knowledge Graph Initialization

GeoOutageBench evaluations can use either a SPARQL endpoint or local RDF files.

For small smoke tests, pass local RDF files directly to the evaluators:

```bash
python Benchmark/evaluate_task1_nl2temporalsparql.py \
  --predictions Benchmark/eval_outputs/nl2sparql_predictions_template.json \
  --rdf-file Counties/counties.ttl \
  --rdf-file States/states.ttl \
  --rdf-file CDC/svi_2022.ttl \
  --rdf-file HURDAT/hurdat.ttl \
  --model-output-dir smoke_test
```

For full Task 3 query execution, load the complete GeoOutage knowledge graph
into a SPARQL engine such as GraphDB. A complete endpoint should include:

- The GeoOutage/GeoResilience ontology Turtle file.
- The GeoOutageBench ontology profile, if using the restricted benchmark
  schema.
- County and state TTLs.
- CDC SVI TTLs for the relevant years.
- HURDAT hurricane TTL.
- Storm Events TTLs, if questions use NOAA storm records.
- EAGLE-I customer outage record TTLs.
- Black Marble nighttime-light image TTLs.
- Outage map TTLs.
- Satellite metadata TTL from `satellites/satellites.ttl`.

GraphDB endpoints usually have this shape:

```text
http://localhost:7200/repositories/<repository-id>
```

The Task 3 evaluator can build that endpoint from:

```bash
--graphdb-url http://localhost:7200 --graphdb-repository geooutage
```

If GraphDB requires credentials, set:

```bash
export GRAPHDB_USER=<username>
export GRAPHDB_PASSWORD=<password>
```

On Windows PowerShell:

```powershell
$env:GRAPHDB_USER = "<username>"
$env:GRAPHDB_PASSWORD = "<password>"
```

## Creating Turtle Files

The scripts below are shown from the repository root. Paths are explicit because
some scripts have current-directory-sensitive defaults.

### Counties

Generate county resources from the Census TIGER county shapefile. By default,
the script reads the Census 2025 county ZIP URL.

```bash
python Counties/generate_counties_ttl.py \
  --output Counties/counties.ttl \
  --sameas-mapping Counties/sameas-mapping.json
```

Generate only selected states:

```bash
python Counties/generate_counties_ttl.py \
  --state FL GA AL \
  --output Counties/counties_southeast.ttl \
  --sameas-mapping Counties/sameas-mapping.json
```

Useful options:

- `--input`: local shapefile ZIP/path or remote TIGER ZIP URL.
- `--state`: one or more postal abbreviations.
- `--bbox`: write bounding boxes instead of full polygon WKT.
- `--geohash-precision`: precision for `mds:Geohash` values.

### States

Generate state, territory, and District of Columbia resources from the Census
TIGER state shapefile:

```bash
python States/generate_states_ttl.py \
  --output States/states.ttl
```

Generate selected states only:

```bash
python States/generate_states_ttl.py \
  --state FL GA AL \
  --output States/states_southeast.ttl
```

Useful options mirror the county generator: `--input`, `--state`, `--bbox`,
`--sameas-mapping`, and `--geohash-precision`.

### CDC Social Vulnerability Index

The CDC generator supports county SVI releases for 2000, 2010, 2014, 2016,
2018, 2020, and 2022. If `--input` is omitted, the script tries known CDC URL
patterns.

```bash
python CDC/generate_svi_ttl.py \
  --year 2022 \
  --counties Counties/counties.ttl \
  --output CDC/svi_2022.ttl
```

Use a downloaded CSV or ZIP instead:

```bash
python CDC/generate_svi_ttl.py \
  --year 2020 \
  --input data/cdc/SVI2020_US_county.csv \
  --counties Counties/counties.ttl \
  --output CDC/svi_2020.ttl
```

The `--counties` file is optional, but recommended. It maps CDC FIPS codes to
the same county URIs used elsewhere in the KG.

### HURDAT Hurricanes

`HURDAT/HurdatKG.py` downloads/parses HURDAT data through `hurdat_lookup.py`
and writes `hurdat.ttl` to the current directory. Run it from `HURDAT/` if you
want to overwrite the included file.

```bash
cd HURDAT
python HurdatKG.py
cd ..
```

### NOAA Storm Events

Generate storm-event TTL from a NOAA StormEvents detail CSV:

```bash
python StormEvents/StormEventsKG.py data/stormevents/StormEvents_details.csv \
  --out StormEvents/stormevents_2024.ttl \
  --counties Counties/counties.ttl \
  --states States/states.ttl
```

Useful options:

- `--counties`: resolve county FIPS values to `goo:County` URIs.
- `--states`: resolve state FIPS values to `goo:State` URIs.
- `--max_rows`: process a small subset for testing.
- `--fmt`: RDFLib output format, such as `ttl`, `xml`, or `json-ld`.

### EAGLE-I Customer Outage Records

The generator expects yearly CSV files at:

```text
<root_dir>/eagle-i/24237376/florida_data/eaglei_outages_<year>.csv
```

Each CSV should contain at least:

- `county`
- `fips_code`
- `run_start_time`
- `customers_out` or `sum`

Filter a raw EAGLE-I file to Florida:

```bash
python EAGLE-I/filter_eaglei.py \
  --file data/raw/eaglei_outages_2024.csv \
  --state Florida \
  --output-dir data/eagle-i/24237376/florida_data
```

Generate yearly outage-record TTL files. `--end_year` is exclusive.

```bash
python EAGLE-I/generate_customer_outage_records_ttl.py \
  --root_dir data \
  --start_year 2014 \
  --end_year 2026 \
  --out_dir EAGLE-I
```

This writes files such as `EAGLE-I/outagerecord_2024.ttl`.

### NASA Black Marble Nighttime-Light Images

Black Marble raw-data preparation requires NASA Earthdata access. Set an
Earthdata token before using `BlackMarble/download.py`:

```bash
export EARTH_DATA_TOKEN=<token>
```

On Windows PowerShell:

```powershell
$env:EARTH_DATA_TOKEN = "<token>"
```

The downloader currently reads `tl_2020_us_zcta510.zip` from the current
directory during startup. Place that Census ZCTA ZIP in the repository root or
adjust the script path before running.

Download daily VNP46A2 data split by Florida county:

```bash
python BlackMarble/download.py 2025-01-01 2025-01-31 \
  --product VNP46A2 \
  --split_into county
```

Convert pickled xarray outputs to PNG images:

```bash
python BlackMarble/pickle_to_image.py \
  --dir county_VNP46A2 \
  --overwrite
```

Generate nighttime-light image TTL. The generator expects image filenames like
`YYYY_MM_DD.png` under county-name subdirectories and a county-to-FIPS JSON file.

```bash
python BlackMarble/generate_ntlimage_ttl.py \
  --root_dir data \
  --ntl_dir county_VNP46A2_imgs \
  --outage_map_dir data/outage_maps \
  --fips_json data/fips_codes.json \
  --out_path BlackMarble/ntlimage.ttl
```

### Outage Maps

Generate outage-map TTL from outage-map image directories:

```bash
python OutageMap/generate_outagemap_ttl.py \
  --root_dir data \
  --outage_map_dir data/outage_maps \
  --ntl_dir county_VNP46A2_imgs \
  --fips_json data/fips_codes.json \
  --out_path OutageMap/outagemap.ttl
```

The image directory convention is the same as the Black Marble TTL generator:
county-name subdirectories containing files named `YYYY_MM_DD.png`, `.jpg`,
`.jpeg`, `.tif`, or `.tiff`.

### GeoOutageBench Ontology Profile

Task 1 is designed to evaluate models under a constrained ontology profile.
`Benchmark/generate_geooutagebench_ontology.py` creates that profile by reading:

- `GeoResilienceOnto_Beta.ttl` at the repository root.
- `Benchmark/task1_allowed.txt`.
- `Benchmark/geooutagebench_questions.json`.

Place the full ontology at `GeoResilienceOnto_Beta.ttl`, then run:

```bash
python Benchmark/generate_geooutagebench_ontology.py
```

The script writes:

```text
GeoResilienceOnto_GeoOutageBench.ttl
```

Use this profile as the schema context for NL2SPARQL prompting and as the
ontology file for Task 2 when you want benchmark-scope ontology scoring.

## Preparing Predictions

Create a blank NL2SPARQL prediction file:

```bash
python Benchmark/make_nl2sparql_prediction_template.py \
  --mode blank \
  --output Benchmark/eval_outputs/my_model_predictions.json
```

Fill each `sparql_query` field with a model-generated query. For ambiguous
questions, include multiple interpretations:

```json
{
  "qid": 1000000,
  "question": "Which counties have the highest number of customer outages reported at one time?",
  "sparql_query": "PREFIX goo: <https://ucf-henat.github.io/GeoOutageOnto/#>\nSELECT ...",
  "sparql_interpretations": [
    {
      "label": "Default interpretation",
      "ambiguity_dimensions": ["spatial"],
      "rationale": "Ranks outage records by represented county.",
      "sparql_query": "PREFIX goo: <https://ucf-henat.github.io/GeoOutageOnto/#>\nSELECT ..."
    }
  ]
}
```

Accepted SPARQL prediction file shapes include:

```json
{
  "1000000": {
    "sparql_query": "SELECT ..."
  },
  "1000002": {
    "sparql_queries": ["SELECT ...", "SELECT ..."]
  }
}
```

or:

```json
{
  "predictions": [
    {
      "qid": 1000000,
      "sparql_query": "SELECT ..."
    }
  ]
}
```

For Task 3 answer-mode evaluation, predicted answers may be supplied as:

```json
{
  "1000000": {
    "answers": [
      {
        "county": "gokg:MiamiDadeCountyFL",
        "count": 1777800
      }
    ]
  }
}
```

## Running the Three Benchmark Tasks

### Task 1: NL2TemporalSPARQL

Task 1 evaluates predicted SPARQL strings against the benchmark gold
interpretations. It reports exact match, ambiguity-aware interpretation recall,
class/property F1, spatial and temporal constraint F1, syntax validity, and
optional executability.

Run structural evaluation without a KG:

```bash
python Benchmark/evaluate_task1_nl2temporalsparql.py \
  --predictions Benchmark/eval_outputs/my_model_predictions.json \
  --model-output-dir my_model
```

Run with local RDF files for executability checks:

```bash
python Benchmark/evaluate_task1_nl2temporalsparql.py \
  --predictions Benchmark/eval_outputs/my_model_predictions.json \
  --rdf-file GeoResilienceOnto_GeoOutageBench.ttl \
  --rdf-file Counties/counties.ttl \
  --rdf-file States/states.ttl \
  --rdf-file CDC/svi_2022.ttl \
  --model-output-dir my_model
```

Run with a SPARQL endpoint:

```bash
python Benchmark/evaluate_task1_nl2temporalsparql.py \
  --predictions Benchmark/eval_outputs/my_model_predictions.json \
  --endpoint http://localhost:7200/repositories/geooutage \
  --timeout 300 \
  --model-output-dir my_model
```

Outputs are written to:

```text
Benchmark/eval_outputs/task1_nl2temporalsparql/<model-output-dir>/
  task1_per_question.csv
  task1_per_question.json
  task1_summary.json
```

### Task 2: Ontology Utility with OntoCheck

Task 2 scores ontology utility for benchmark SPARQL queries using OntoCheck.
It reports relevance and accuracy. Install `ontocheck` before running this
task.

Score the benchmark gold interpretations:

```bash
python Benchmark/evaluate_task2_ontocheck.py \
  --benchmark Benchmark/geooutagebench_questions.json \
  --ttl_file GeoResilienceOnto_GeoOutageBench.ttl \
  --model-output-dir gold
```

Score model-predicted SPARQL interpretations:

```bash
python Benchmark/evaluate_task2_ontocheck.py \
  --benchmark Benchmark/geooutagebench_questions.json \
  --predictions Benchmark/eval_outputs/my_model_predictions.json \
  --ttl_file GeoResilienceOnto_GeoOutageBench.ttl \
  --count-missing-as-zero \
  --model-output-dir my_model
```

Score the legacy per-query directory:

```bash
python Benchmark/evaluate_task2_ontocheck.py \
  --gold-source directory \
  --directory Benchmark/sparql_queries \
  --ttl_file GeoResilienceOnto_GeoOutageBench.ttl \
  --model-output-dir legacy_directory
```

Outputs are written to:

```text
Benchmark/eval_outputs/task2_ontocheck/<model-output-dir>/
  task2_per_result.csv
  task2_per_result.json
  task2_summary.csv
  task2_summary.json
  task2_missing_predictions.csv      # only when applicable
  task2_missing_predictions.json     # only when applicable
```

### Task 3: Spatiotemporal KGQA

Task 3 evaluates returned answers rather than only SPARQL text. It supports two
prediction modes:

- `answers`: compare supplied answer rows against the benchmark JSON answers.
- `queries`: execute predicted SPARQL queries and compare returned rows.

Evaluate answer rows without a KG endpoint:

```bash
python Benchmark/evaluate_task3_spatiotemporal_kgqa.py \
  --predictions Benchmark/eval_outputs/my_model_answers.json \
  --prediction-kind answers \
  --model-output-dir my_model
```

Evaluate predicted queries against GraphDB and execute gold SPARQL for gold
answers:

```bash
python Benchmark/evaluate_task3_spatiotemporal_kgqa.py \
  --predictions Benchmark/eval_outputs/my_model_predictions.json \
  --prediction-kind queries \
  --gold-from-execution \
  --graphdb-url http://localhost:7200 \
  --graphdb-repository geooutage \
  --timeout 3600 \
  --model-output-dir my_model
```

Evaluate predicted queries against local RDF files:

```bash
python Benchmark/evaluate_task3_spatiotemporal_kgqa.py \
  --predictions Benchmark/eval_outputs/my_model_predictions.json \
  --prediction-kind queries \
  --gold-from-execution \
  --rdf-file GeoResilienceOnto_GeoOutageBench.ttl \
  --rdf-file Counties/counties.ttl \
  --rdf-file States/states.ttl \
  --rdf-file CDC/svi_2022.ttl \
  --rdf-file HURDAT/hurdat.ttl \
  --model-output-dir my_model_local
```

Useful Task 3 options:

- `--prediction-kind auto`: infer answer-vs-query mode from each prediction.
- `--endpoint`: pass a complete SPARQL endpoint directly.
- `--graphdb-user` and `--graphdb-password`: override GraphDB credentials.
- `--skip-questions`: skip qids or 1-based positions, comma-separated or
  repeated.
- `--print-rows`: print normalized gold/predicted sample rows for debugging.
- `--quiet`: suppress per-question progress output.

Outputs are written to:

```text
Benchmark/eval_outputs/task3_spatiotemporal_kgqa/<model-output-dir>/
  task3_per_question.csv
  task3_per_question.json
  task3_summary.json
```

Task 3 metrics include answer precision, recall, F1, Hits@1/5/10, MRR,
nDCG@10, spatial correctness, temporal correctness, and spatiotemporal
relevance score.

## NL2SPARQL Prompting Workflow

A typical model-evaluation workflow is:

1. Generate a blank prediction template.
2. Provide the model with:
   - `Benchmark/nl2sparql_prompt.txt`
   - `Benchmark/task1_allowed.txt`
   - `GeoResilienceOnto_GeoOutageBench.ttl` or the full ontology context
   - The blank prediction JSON
3. Save the model-completed JSON under `Benchmark/eval_outputs/`.
4. Run Task 1 for structural SPARQL quality.
5. Run Task 2 for ontology utility, if OntoCheck is installed.
6. Run Task 3 against a complete KG endpoint for answer quality.

For oracle smoke tests, generate predictions from gold queries:

```bash
python Benchmark/make_nl2sparql_prediction_template.py \
  --mode gold-original \
  --output Benchmark/eval_outputs/oracle_gold_original.json

python Benchmark/make_nl2sparql_prediction_template.py \
  --mode gold-all \
  --output Benchmark/eval_outputs/oracle_gold_all.json
```

## Adding or Updating Benchmark Questions

Use `Benchmark/add_geooutagebench_question.py` to append a question and refresh
summary/catalog counts. A metadata JSON object is usually easier than passing
every field on the command line.

Dry run:

```bash
python Benchmark/add_geooutagebench_question.py \
  --question "Which counties had outage records during the storm window?" \
  --metadata path/to/question_metadata.json \
  --dry-run
```

Write to the benchmark file:

```bash
python Benchmark/add_geooutagebench_question.py \
  --question "Which counties had outage records during the storm window?" \
  --metadata path/to/question_metadata.json
```

Required metadata includes answer type, graph query, primary category, query
complexity, ambiguity types, SPARQL query, template metadata, template
variables, and spatiotemporal axis.

## Troubleshooting

`ModuleNotFoundError` for local modules:

- Run commands from the repository root when following this README.
- Make sure the virtual environment is activated.

Git LFS pointer files instead of Turtle RDF:

- Run `git lfs install`, then `git lfs pull`.

Task 2 fails with `OntoCheck is required`:

- Install the `ontocheck` package in the active environment.

Task 1 reports zero executability:

- This is expected if neither `--endpoint` nor `--rdf-file` is supplied.
- Structural metrics still run without a KG.

Task 3 query mode returns empty or zero scores:

- Confirm that a complete KG is loaded.
- Confirm that endpoint credentials are correct.
- Use `--print-rows` to inspect returned columns and normalized row values.
- Use `--skip-questions` for known expensive queries during debugging.

Black Marble download fails at startup:

- Confirm `EARTH_DATA_TOKEN` is set.
- Confirm the package providing `blackmarble.download.BlackMarbleDownloader`
  is installed.
- Place `tl_2020_us_zcta510.zip` in the working directory or update the script
  path.

Relative path surprises:

- Prefer the explicit root-level commands in this README.
- If running a script from inside a subdirectory, adjust output paths
  accordingly.

## License

GeoOutageBench is licensed under the MIT License. See [LICENSE](LICENSE) for
details.

## Citation

If you use the GeoOutageKG data products, as we have, please cite
GeoOutageKG:

```bibtex
@InProceedings{geooutagekg,
  author={Frakes, Ethan and Wu, Yinghui and French, Roger H. and Li, Mengjie},
  editor={Garijo, Daniel and Kirrane, Sabrina and Salatino, Angelo and Shimizu, Cogan and Acosta, Maribel and Nuzzolese, Andrea Giovanni and Ferrada, Sebasti{\'a}n and Soulard, Thibaut and Kozaki, Kouji and Takeda, Hideaki and Gentile, Anna Lisa},
  title={{GeoOutageKG}: A Multimodal Geospatiotemporal Knowledge Graph for Multiresolution Power Outage Analysis},
  booktitle={The Semantic Web -- ISWC 2025},
  year={2025},
  month={10},
  publisher={Springer Nature Switzerland},
  address={Cham},
  pages={221--239},
  isbn={978-3-032-09530-5},
  doi={10.1007/978-3-032-09530-5_13},
  eprint={2507.22878},
  eprinttype={arxiv},
  eprintclass={cs.IR}
}
```
