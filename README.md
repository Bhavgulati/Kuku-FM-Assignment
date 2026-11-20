# Promo Finder

Promo Finder reads a microdrama's screenplay files (one `.txt` per episode) and finds the **best 20–60 second promo clips** in them. It returns single continuous clips and multi-segment montages. Each promo comes with exact start and end timestamps for every segment, the hook, the cliffhanger and one line on why it works, and the promos are **ranked in the order you would post them**. The tool also checks its own work: a *blind* AI viewer that has never seen the show judges every promo, and the tool's picks are scored against naive baselines in exactly the same way.

The output is a shortlist for an editor, so they no longer have to watch every episode. They open `report.html`, review the top 15, and scrub straight to the timestamps.

---

## Quick start

Requires Python 3.10+.

```bash
cd promo-finder
python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 1. API key (free from Google AI Studio). .env is gitignored.
cp .env.example .env          # then paste your key into .env

# 2. Check the input parses (no AI, instant)
python -m promofinder parse --input "path/to/show folder"

# 3. See which models your key can use, and set one in config.yaml (llm.model)
python -m promofinder models

# 4. Run everything
python -m promofinder run --input "path/to/show folder" --out outputs/my_show
```

Quick trial on a few episodes: `--episodes 1-5`. With no key at all, `--provider offline` runs the whole pipeline with a deterministic stub. That checks the plumbing only and says nothing about quality.

**Default models.** `gemini-3.5-flash-lite` proposes and judges. On a free key, each Flash model was capped at about 20 requests per day, and a 50-episode show needs about 55 proposer calls plus about 280 judge calls. With a paid key, set `llm.model: gemini-3.5-flash` (stronger proposer) and `judge.model: gemini-3.5-flash-lite`.

**Free-tier friendly.** Every model response is cached in `.cache/llm/`. A rerun is free and produces identical output. If the daily quota runs out mid-run, the tool stops cleanly (exit code 3), and running the same command later resumes where it stopped. `runtime.max_rpm` in `config.yaml` keeps you under the per-minute limit.

## What you get (`outputs/<run>/`)

| File | What it is |
|---|---|
| `report.html` | The editor view: ranked cards with segment timestamps, hook, cliffhanger and why it works, judge scores, the blind viewer's reaction and confusions, flags, and the script excerpt with the first 3 seconds highlighted. Self-contained; opens offline. |
| `promos_ranked.csv` / `.json` | Every valid promo in posting order (`shortlisted` = top N), with all fields. |
| `candidates_all.json` | Everything the model proposed, including the **rejected** proposals and the reason each was rejected. |
| `baselines.json` | The naive baseline promos (B1/B2/B3) with their scores. |
| `quality.md` / `.json` | Rule pass rates, tool vs baselines, judge test-retest, worst picks and misunderstandings, token and call counts. |
| `run_meta.json` | Models used, prompt hashes, full config, timings, usage. |
| `llm_raw/` | The unedited response of every model call. |

## How it works

```
 .txt scripts ──► parse (code) ──► propose per episode (LLM picks BLOCK IDs, never timestamps)
                                    │
                                    ├─► validate + snap (code): 20–60s, ≤3 segments, no cut mid-line,
                                    │      one repair round with the exact error, else drop and log
                                    ├─► montages across episodes (LLM over a "series spine")
                                    └─► baselines (code): B1 episode ending, B2 random, B3 rules only
                                                   │
                      blind judge (LLM, sees only what a viewer sees):
                        hook   = first 3s only, ~10 shuffled openings per call (ours mixed with baselines)
                        blind  = whole promo, one per call: understood / confusions / open question /
                                 cliffhanger 1–5 / stands-alone 1–5
                        grade  = viewer's understanding vs. true loglines: consistent / partial / contradicted
                                                   │
                      rank (code): equal-weight mean − misunderstanding penalty, overlap dedupe,
                                   mild trope variety, ties → earlier episode ──► report
```

- **What AI does:** choosing moments (proposals and montages) and judging them (hook, cliffhanger, stands alone, comprehension).
- **What code does:** parsing, all timestamp maths, cut snapping, the rules, dedupe, ranking, baselines and metrics. The model never writes a timestamp. It names block IDs (`B027`), and code converts them, snapping so that no cut lands in the middle of a line of dialogue. Every output timestamp therefore traces back to a line of the script.
- **Where the human stays:** the editor approves the shortlist in `report.html`. The tool narrows "watch 50 episodes" down to "review 15 candidates with timestamps".

## Swapping the model

Edit `config.yaml`, or pass flags. Nothing else changes.

```bash
# Gemini (default provider): pick any model `promofinder models` lists
python -m promofinder run --input SHOW --model <gemini-model>

# Any OpenAI-compatible endpoint: Groq, OpenRouter, Ollama, vLLM, or an API Kuku provides
python -m promofinder run --input SHOW --provider openai_compat \
    --base-url https://api.groq.com/openai/v1 --model <model> --api-key-env GROQ_API_KEY
python -m promofinder run --input SHOW --provider openai_compat \
    --base-url http://localhost:11434/v1 --model <ollama-model>          # local, no key

# Judge with a different (e.g. stronger) model than the proposer
python -m promofinder run --input SHOW --judge-model <other-model>
```

A new kind of backend is one class with a `call(system, user, temperature, tag) -> (text, usage)` method in `promofinder/llm/providers.py`. The prompts are plain files in `promofinder/prompts/`, contain nothing specific to a show, and can be edited without touching code.

## Running on a new show

Point `--input` at a folder with one `.txt` per episode in the same screenplay format: a header, scenes, and `**LABEL:**` + `Start: (MM:SS.ss) | End: (MM:SS.ss)` + text blocks. Field names are matched case-insensitively. Run `parse` first: it prints counts and any format warnings with file:line, and anything it can't read is skipped with a warning rather than crashing.

## Tests

```bash
python -m pytest -q
```

The tests use small synthetic scripts in `tests/fixtures/` and need no network. The test for the real input pack runs only if the pack is present next to this folder (or wherever `PROMOFINDER_PACK` points).

## Confidentiality

Kuku's input pack is confidential and is **not** part of this repo (`.gitignore` excludes it). `outputs/` contains excerpts of the scripts, so it is gitignored too. Share run outputs through the submission link, not in a public repo.

## Layout

```
promofinder/
  parse.py        screenplay .txt → Episode / Scene / Block
  timeline.py     clean cut points, block IDs → snapped segments, viewer render
  validate.py     hard rules + informational flags
  propose.py      stage 1, per-episode proposals (+ repair round)
  montage.py      stage 2, cross-episode montages
  baselines.py    B1/B2/B3      heuristic.py   rules-only picker (B3)
  judge.py        blind hook / full-view / comprehension judges
  rank.py         scoring + diversified posting order
  quality.py      metrics       report.py      JSON / CSV / quality.md / report.html
  pipeline.py     orchestration __main__.py    CLI
  llm/base.py     cache, throttle, retries, JSON repair, usage
  llm/providers.py  gemini · openai_compat · offline
  prompts/*.md    all prompts
docs/             solution doc + build log
```
