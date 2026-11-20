"""Orchestrates a full run: parse -> propose -> montage -> baselines -> judge -> rank -> retest -> report."""
from __future__ import annotations

import json
import os
import platform
import random
import time
from pathlib import Path

from . import report
from .baselines import make_baselines
from .config import ROOT, judge_llm_cfg
from .judge import grade_comprehension, judge_blind, judge_hooks
from .llm.base import LLMClient
from .llm.providers import make_provider
from .montage import propose_montages
from .names import extract_names
from .parallel import pmap
from .parse import load_show
from .prompting import prompt_hashes
from .propose import propose_episode
from .quality import agreement, compute
from .rank import rank, score
from .timeline import Promo
from .validate import flags


def build_client(llm_cfg: dict, cfg: dict, raw_dir: Path) -> LLMClient:
    rt = cfg["runtime"]
    key_env = llm_cfg.get("api_key_env")
    provider = make_provider(llm_cfg["provider"], llm_cfg.get("model") or "", os.environ.get(key_env) if key_env else None,
                             llm_cfg.get("base_url"), llm_cfg.get("extra"), timeout=rt["timeout_s"])
    if provider.name != "offline" and not provider.model:
        raise SystemExit("No model configured: set llm.model in config.yaml or pass --model "
                         "(list what your key can use with: python -m promofinder models)")
    cache = Path(rt["cache_dir"])
    if not cache.is_absolute():
        cache = ROOT / cache
    return LLMClient(provider, cache, raw_dir, max_rpm=None if provider.name == "offline" else rt["max_rpm"],
                     max_retries=rt["max_retries"])


def _log(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def run(input_dir: str, out_dir: str, cfg: dict, episodes: set[int] | None = None, do_montage: bool = True,
        do_baselines: bool = True, do_retest: bool = True) -> Path:
    t0 = time.time()
    pcfg, jcfg, bcfg, rcfg, rt = cfg["promo"], cfg["judging"], cfg["baselines"], cfg["ranking"], cfg["runtime"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "llm_raw"

    eps = load_show(input_dir, episodes)
    E = {e.number: e for e in eps}
    warnings = [w for e in eps for w in e.warnings]
    _log(f"Parsed {len(eps)} episodes, {sum(len(e.blocks) for e in eps)} blocks, {len(warnings)} parse warnings")

    proposer = build_client(cfg["llm"], cfg, raw_dir)
    jl = judge_llm_cfg(cfg)
    judge = proposer if jl == cfg["llm"] else build_client(jl, cfg, raw_dir)
    workers = rt["workers"]

    # 1. per-episode proposals
    _log(f"Stage 1/6: proposing up to {pcfg['candidates_per_episode']} promos per episode with {proposer.model_id}")
    singles: list[Promo] = []
    rejected: list[dict] = []
    for ep, res in zip(eps, pmap(lambda ep: propose_episode(proposer, ep, E, pcfg), eps, workers)):
        if isinstance(res, Exception):
            rejected.append({"episode": ep.number, "stage": "propose", "reason": f"error: {res}"})
            continue
        good, bad = res
        singles += good
        rejected += bad
    raw_count = len(singles) + sum(1 for r in rejected if r.get("candidate"))
    _log(f"  {len(singles)} valid single-clip promos, {len(rejected)} rejected")

    # 2. montages
    montages: list[Promo] = []
    if do_montage and pcfg["montages"] > 0:
        _log("Stage 2/6: building cross-episode montages")
        montages, mrej = propose_montages(proposer, eps, singles, E, pcfg)
        rejected += mrej
        raw_count += len(montages) + sum(1 for r in mrej if r.get("candidate"))
        _log(f"  {len(montages)} valid montages, {len(mrej)} rejected")

    # 3. baselines
    baselines: list[Promo] = []
    sample_nums: list[int] = []
    if do_baselines and bcfg["enabled"]:
        baselines, sample = make_baselines(eps, E, pcfg, bcfg)
        sample_nums = [e.number for e in sample]
        _log(f"Stage 3/6: {len(baselines)} baseline promos (B1/B2/B3) on {len(sample)} sampled episodes")

    ours = singles + montages
    for p in ours + baselines:
        p.flags = flags(p, E, pcfg)

    # 4. blind judging (ours and baselines mixed together)
    everyone = ours + baselines
    _log(f"Stage 4/6: blind judging {len(everyone)} promos with {judge.model_id}")
    names = extract_names(judge, eps if episodes is None else load_show(input_dir))  # cast = whole show
    _log(f"  masking {len(names)} character names the viewer has not heard: {', '.join(names) or '-'}")
    hooks = judge_hooks(judge, everyone, E, pcfg["hook_window_s"], jcfg["hook_batch_size"], jcfg["seed"], workers,
                        temperature=jcfg["temperature"], names=names)
    blind = judge_blind(judge, everyone, E, workers, temperature=jcfg["temperature"], names=names)
    grades = grade_comprehension(judge, everyone, blind, E, jcfg["grade_batch_size"], workers,
                                 temperature=jcfg["temperature"])
    for p in everyone:
        p.judge = {"hook": hooks.get(p.promo_id), "blind": blind.get(p.promo_id), "grade": grades.get(p.promo_id)}

    # 5. rank (baselines are scored for comparison but never ranked for posting)
    _log("Stage 5/6: ranking")
    rows = rank(ours, rcfg)
    for b in baselines:
        score(b, rcfg)

    # 6. judge test-retest on a sample of the tool's promos
    retest = None
    if do_retest and jcfg["retest_sample"] > 0 and ours:
        judged = [p for p in ours if p.scores.get("final") is not None]
        rs = random.Random(jcfg["seed"]).sample(judged, min(jcfg["retest_sample"], len(judged)))
        _log(f"Stage 6/6: test-retest of the judge on {len(rs)} promos")
        h2 = judge_hooks(judge, rs, E, pcfg["hook_window_s"], jcfg["hook_batch_size"], jcfg["seed"], workers,
                         replica=1, temperature=jcfg["temperature"], names=names)
        b2 = judge_blind(judge, rs, E, workers, replica=1, temperature=jcfg["temperature"], names=names)
        retest = {
            "n_promos": len(rs),
            "hook": agreement([(p.scores["hook"], (h2.get(p.promo_id) or {}).get("score")) for p in rs]),
            "cliffhanger": agreement([(p.scores["cliffhanger"], (b2.get(p.promo_id) or {}).get("cliffhanger")) for p in rs]),
            "standalone": agreement([(p.scores["standalone"], (b2.get(p.promo_id) or {}).get("standalone")) for p in rs]),
            "promo_ids": [p.promo_id for p in rs],
        }

    q = compute(singles, montages, baselines, sample_nums, rejected, raw_count, retest)
    meta = {
        "run_id": out.name,
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
        "seconds_total": round(time.time() - t0, 1),
        "input_name": Path(input_dir).resolve().name,
        "episodes": len(eps),
        "episode_numbers": [e.number for e in eps],
        "proposer_model": proposer.model_id,
        "judge_model": judge.model_id,
        "masked_names": names,
        "prompt_sha256_12": prompt_hashes(),
        "config": cfg,
        "parse_warnings": warnings,
        "usage": proposer.usage.to_dict() if judge is proposer else {
            "proposer": proposer.usage.to_dict(), "judge": judge.usage.to_dict(), **_sum_usage(proposer, judge)},
        "python": platform.python_version(),
    }
    report.write_all(out, rows, baselines, rejected, q, meta, E, pcfg["hook_window_s"])
    _log(f"Done in {meta['seconds_total']:.0f}s -> {out}")
    return out


def _sum_usage(a: LLMClient, b: LLMClient) -> dict:
    ua, ub = a.usage.to_dict(), b.usage.to_dict()
    out = {k: ua[k] + ub[k] for k in ("api_calls", "cache_hits", "input_tokens", "output_tokens", "retries",
                                      "json_repairs", "api_seconds")}
    stages = json.loads(json.dumps(ua["by_stage"]))
    for k, v in ub["by_stage"].items():
        s = stages.setdefault(k, {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0})
        for kk in s:
            s[kk] += v[kk]
    out["by_stage"] = stages
    return out
