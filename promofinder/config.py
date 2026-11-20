"""Config loading: defaults <- config.yaml <- CLI flags. Also a tiny .env loader."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "llm": {"provider": "gemini", "model": "", "api_key_env": "GEMINI_API_KEY", "base_url": None, "extra": {}},
    "judge": {},
    "runtime": {"max_rpm": 8, "workers": 4, "max_retries": 6, "timeout_s": 300, "cache_dir": ".cache/llm"},
    "promo": {
        "min_s": 20.0,
        "max_s": 60.0,
        "min_segment_s": 3.0,
        "max_segments": 3,
        "hook_window_s": 3.0,
        "candidates_per_episode": 3,
        "montages": 5,
        "hook_hints": 6,
        "tropes": [
            "betrayal", "humiliation", "secret identity", "rescue/protector", "romance",
            "danger/threat", "revenge/comeuppance", "villain scheme", "other",
        ],
        "temperature": 0.4,
    },
    "judging": {"hook_batch_size": 10, "grade_batch_size": 10, "temperature": 0.2, "retest_sample": 20, "seed": 7},
    "baselines": {"enabled": True, "sample_episodes": 20, "window_s": 45.0, "seed": 7},
    "ranking": {
        "weights": {"hook": 1.0, "cliffhanger": 1.0, "standalone": 1.0},
        "comprehension_penalty": {"consistent": 0.0, "partial": 0.25, "contradicted": 1.0},
        "trope_penalty": 0.15,
        "overlap_threshold": 0.5,
        "shortlist": 15,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: Path | None = None) -> None:
    """KEY=VALUE lines -> os.environ (does not override variables already set)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else ROOT / "config.yaml"
    if p.exists():
        cfg = _merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    if overrides:
        cfg = _merge(cfg, overrides)
    return cfg


def judge_llm_cfg(cfg: dict) -> dict:
    """The judge uses the main llm settings unless `judge:` overrides some of them.
    `extra` is replaced as a whole (provider settings rarely carry over between models)."""
    j = cfg.get("judge") or {}
    out = _merge(cfg["llm"], {k: v for k, v in j.items() if k != "extra"})
    if "extra" in j:
        out["extra"] = copy.deepcopy(j["extra"] or {})
    return out
