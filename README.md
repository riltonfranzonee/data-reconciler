# Data reconciler

Matches organisation names to the Research Organization Registry (ROR), through
an OpenRefine-compatible API or batch commands. Accepted ROR identifiers can
then be used to join CORDIS project participants to OpenAlex.

Candidates are found by exact lookup, word-prefix search and character trigrams.
The matcher scores their names and locations, then checks the score and the gap
between candidates to decide whether to accept a match.

## Install and test

Install [uv](https://docs.astral.sh/uv/), then run the commands below. The Python
version and dependencies are pinned in `.python-version` and `uv.lock`.

```bash
git clone https://github.com/riltonfranzonee/data-reconciler.git
cd data-reconciler
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen pytest --cov=ror_reconcile --cov-fail-under=75 -q
```

The tests use local fixtures and mocked API responses.

## Try the service locally

Build a small demonstration index from the test fixture:

```bash
uv run --frozen ror-ingest \
  --ror-dump tests/fixtures/ror_fixture.json \
  --db data/processed/demo.sqlite

uv run --frozen ror-serve \
  --db data/processed/demo.sqlite \
  --host 127.0.0.1 --port 8765
```

In another terminal, send a reconciliation request:

```bash
curl -sS -X POST http://127.0.0.1:8765/ \
  --data-urlencode 'queries={"q0":{"query":"University College London","properties":[{"pid":"country","v":"GB"},{"pid":"city","v":"London"}]}}'
```

The leading candidate should be University College London with `match: true`.
This demo uses seven test records. Build the full ROR index below to work with
real data or run the evaluation.

## Build the full ROR index

The evaluation uses ROR v2.8, dated 2 June 2026. Download that release and ingest it:

```bash
mkdir -p data/raw
curl --fail --location \
  'https://zenodo.org/api/records/20512981/files/v2.8-2026-06-02-ror-data.zip/content' \
  -o data/raw/v2.8-2026-06-02-ror-data.zip

uv run --frozen ror-ingest \
  --ror-dump data/raw/v2.8-2026-06-02-ror-data.zip \
  --db data/processed/ror.sqlite
```

The index should contain 127,138 organisations. The ZIP's SHA-256 is
`243877a330c811d0d71c01ba5126c07f5cd710c721b20b7af8dacabb9243b618`.

## Use with OpenRefine

```bash
uv run --frozen ror-serve \
  --db data/processed/ror.sqlite \
  --host 127.0.0.1 --port 8765
```

Import `data/samples/demo_openrefine.csv` into OpenRefine, add
`http://127.0.0.1:8765/` as a reconciliation service, and reconcile the `name`
column. Map the `country` column to the service's country property. Accepted
identifiers can be exported with `cell.recon.match.id`.

## Reproduce the batch workflow

Run the supplied 300-organisation cohort against the full ROR index. These
commands write the results to `runs/`:

```bash
mkdir -p runs
uv run --frozen ror-reconcile-cordis \
  --db data/processed/ror.sqlite \
  --input data/processed/horizon-cohort-300.csv \
  --output runs/cordis-ror.csv \
  --evidence-output runs/cordis-ror-evidence.jsonl

cp data/processed/horizon-cohort-300-openalex-cache.json runs/openalex-cache.json
uv run --frozen ror-enrich-openalex \
  --input runs/cordis-ror.csv \
  --output runs/cordis-ror-openalex.csv \
  --cache runs/openalex-cache.json \
  --from-year 2021 --to-year 2026

uv run --frozen python scripts/analyse_cohort.py \
  --input runs/cordis-ror-openalex.csv \
  --organization-output runs/organizations.csv \
  --group-output runs/country-activity.csv \
  --summary-output runs/analysis-summary.json
```

The output has 564 participation rows, with 111 organisations linked to ROR
and OpenAlex. The included cache supplies the OpenAlex responses for those
organisations. For new organisations, the client can read
`OPENALEX_API_KEY` and `OPENALEX_MAILTO` from the environment.

## Run the evaluation

Use the full ROR index and the saved labels and baseline responses:

```bash
mkdir -p runs
cp data/processed/ror-api-baseline-cache-test.json runs/ror-baseline-cache.json
uv run --frozen ror-eval \
  --db data/processed/ror.sqlite \
  --gold data/processed/horizon-gold-test.csv \
  --report runs/test-evaluation.json \
  --cases-output runs/test-evaluation-cases.csv \
  --ror-baseline-cache runs/ror-baseline-cache.json \
  --ror-baseline-output runs/test-ror-baseline.json
```

On the supplied 75-case, country-balanced benchmark, expect 28 correct links,
eight unresolved known matches and 39 correctly rejected no-match cases.
The baseline takes the first result from ROR's hosted name search.

To repeat the development experiments:

```bash
uv run --frozen ror-eval \
  --db data/processed/ror.sqlite \
  --gold data/processed/horizon-gold-dev.csv \
  --report runs/dev-evaluation.json \
  --cases-output runs/dev-evaluation-cases.csv \
  --ablation-output runs/dev-ablation.json \
  --threshold-sweep-output runs/dev-threshold-sweep.json
```

The grid tests name thresholds from 0.78 to 0.84 and final-score thresholds
from 0.82 to 0.94, in steps of 0.02. The ablation adds one component at a time.
Label decisions are recorded in `horizon-gold-adjudicated.csv`. The development
and test cases are in `horizon-gold-dev.csv` and `horizon-gold-test.csv`.

The following commands compute exact binomial intervals and plot the saved
results. Charts are written to `outputs/figures/`.

```bash
uv run --frozen python scripts/compute_exact_binomial_bounds.py \
  --cases data/reports/horizon-test-evaluation-cases.csv \
  --output runs/test-exact-binomial.json
uv run --frozen python scripts/plot_evaluation.py
```

## Data sources and licence

Code is MIT licensed. [ROR metadata](https://ror.org/about/terms/) and
[OpenAlex data](https://developers.openalex.org/) are CC0.

The cohort uses [CORDIS Horizon Europe data](https://doi.org/10.2906/112117098108/20)
from the European Union, downloaded on 17 July 2026 and selected, normalised and
linked by this project. See the [CORDIS reuse terms](https://cordis.europa.eu/about/legal/en).
OpenAlex responses were saved in July and August 2026, so the 2026 counts cover
only part of the year.
