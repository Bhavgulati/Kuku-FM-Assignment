"""End-to-end run with the offline provider (no network, no key)."""
from __future__ import annotations

import json
from pathlib import Path

from promofinder.config import load_config
from promofinder.pipeline import run

FIX = Path(__file__).parent / "fixtures" / "show"


def test_offline_pipeline(tmp_path):
    cfg = load_config(overrides={"llm": {"provider": "offline", "model": "offline"},
                                 "runtime": {"cache_dir": str(tmp_path / "cache"), "workers": 1},
                                 "baselines": {"sample_episodes": 2}, "judging": {"retest_sample": 2}})
    out = run(str(FIX), str(tmp_path / "out"), cfg)
    for name in ("promos_ranked.json", "promos_ranked.csv", "candidates_all.json", "baselines.json", "quality.md",
                 "report.html", "run_meta.json"):
        assert (out / name).exists(), name
    ranked = json.loads((out / "promos_ranked.json").read_text(encoding="utf-8"))
    assert ranked, "offline proposer should yield at least one promo"
    for d in ranked:
        assert 20 <= d["duration_s"] <= 60
        assert d["segments"][0]["start"].count(":") == 1
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["usage"]["api_calls"] > 0
    # story map + head-to-head stages ran and were written out
    story = json.loads((out / "story_map.json").read_text(encoding="utf-8"))
    assert story["beats"] and all(b["duration_s"] >= 3 for b in story["beats"])
    assert any(d["source"] == "storymap" for d in ranked)
    pw = json.loads((out / "quality.json").read_text(encoding="utf-8"))["pairwise"]
    assert pw["tournament"]["pool"] >= 2 and set(pw["vs_baselines"]) == {"B1", "B2", "B3"}
    assert any(d["head_to_head"] for d in ranked)
    # second run is served entirely from cache
    out2 = run(str(FIX), str(tmp_path / "out2"), cfg)
    meta2 = json.loads((out2 / "run_meta.json").read_text(encoding="utf-8"))
    u1, u2 = meta["usage"], meta2["usage"]
    assert u2["api_calls"] == 0 and u2["cache_hits"] == u1["api_calls"] + u1["cache_hits"]
