"""Command line.

  python -m promofinder run    --input "<folder of .txt scripts>" [--out outputs/<name>] [options]
  python -m promofinder models [--provider gemini|openai_compat] [--base-url ...]
  python -m promofinder parse  --input "<folder>"      # format check + stats, no AI
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter

from .config import ROOT, load_config, load_dotenv
from .llm.base import LLMError, QuotaExhausted
from .llm.providers import make_provider
from .parse import load_show, parse_episode_range


def _overrides(a) -> dict:
    o: dict = {"llm": {}, "judge": {}, "promo": {}, "runtime": {}}
    if a.provider:
        o["llm"]["provider"] = a.provider
    if a.model:
        o["llm"]["model"] = a.model
    if a.base_url:
        o["llm"]["base_url"] = a.base_url
    if a.api_key_env:
        o["llm"]["api_key_env"] = a.api_key_env
    if getattr(a, "judge_model", None):
        o["judge"]["model"] = a.judge_model
    if getattr(a, "candidates", None):
        o["promo"]["candidates_per_episode"] = a.candidates
    if getattr(a, "hook_hints", None) is not None:
        o["promo"]["hook_hints"] = a.hook_hints
    if getattr(a, "max_rpm", None):
        o["runtime"]["max_rpm"] = a.max_rpm
    if getattr(a, "workers", None):
        o["runtime"]["workers"] = a.workers
    return {k: v for k, v in o.items() if v}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    ap = argparse.ArgumentParser(prog="promofinder", description="Find and rank promo clips in microdrama screenplays.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--config", help="path to config.yaml (default: promo-finder/config.yaml)")
        p.add_argument("--provider", help="gemini | openai_compat | offline")
        p.add_argument("--model", help="model name for the provider")
        p.add_argument("--base-url", help="endpoint for openai_compat (e.g. https://api.groq.com/openai/v1)")
        p.add_argument("--api-key-env", help="name of the env var holding the API key")

    r = sub.add_parser("run", help="run the full pipeline")
    common(r)
    r.add_argument("--input", required=True, help="folder with one .txt screenplay per episode")
    r.add_argument("--out", help="output folder (default: outputs/run_<timestamp>)")
    r.add_argument("--episodes", help="subset, e.g. '1-5,9'")
    r.add_argument("--judge-model", help="use a different model for judging")
    r.add_argument("--candidates", type=int, help="max promos proposed per episode")
    r.add_argument("--hook-hints", type=int, help="visceral moments suggested to the proposer (0 = off)")
    r.add_argument("--max-rpm", type=float, help="requests per minute cap")
    r.add_argument("--workers", type=int, help="parallel requests")
    r.add_argument("--no-montage", action="store_true")
    r.add_argument("--no-baselines", action="store_true")
    r.add_argument("--no-retest", action="store_true")

    m = sub.add_parser("models", help="list models the configured key can use")
    common(m)

    pp = sub.add_parser("parse", help="parse-only format check (no AI)")
    pp.add_argument("--input", required=True)

    a = ap.parse_args(argv)

    if a.cmd == "parse":
        eps = load_show(a.input)
        kinds = Counter(b.kind for e in eps for b in e.blocks)
        print(f"{len(eps)} episodes, {sum(len(e.scenes) for e in eps)} scenes, blocks: {dict(kinds)}")
        print(f"duration: {min(e.duration for e in eps):.0f}–{max(e.duration for e in eps):.0f}s per episode, "
              f"{sum(e.duration for e in eps) / 60:.1f} min total")
        ws = [w for e in eps for w in e.warnings]
        print(f"{len(ws)} warnings" + ("".join(f"\n  {w}" for w in ws[:30])))
        return 0

    cfg = load_config(a.config, _overrides(a))
    if a.cmd == "models":
        llm = cfg["llm"]
        key = os.environ.get(llm.get("api_key_env") or "", None)
        try:
            prov = make_provider(llm["provider"], llm.get("model") or "", key, llm.get("base_url"), llm.get("extra"))
            for name in prov.list_models():
                print(name)
        except LLMError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0

    from .pipeline import run  # imported late so `parse`/`models` stay light

    out = a.out or str(ROOT / "outputs" / time.strftime("run_%Y%m%d_%H%M%S"))
    try:
        run(a.input, out, cfg, parse_episode_range(a.episodes), do_montage=not a.no_montage,
            do_baselines=not a.no_baselines, do_retest=not a.no_retest)
    except QuotaExhausted as e:
        print(f"\nSTOPPED: {e}\nEverything finished so far is cached; run the same command again later to resume.",
              file=sys.stderr)
        return 3
    except LLMError as e:
        print(f"\nLLM error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
