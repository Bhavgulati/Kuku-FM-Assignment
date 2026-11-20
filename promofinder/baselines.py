"""Naive baselines, judged exactly like the tool's promos so the comparison is fair.

B1 'episode ending'  - the last ~45s of an episode (episodes are written to end on a
                       cliffhanger, so this is the strongest naive editor shortcut).
B2 'random window'   - a random ~45s clean window. If the judge cannot tell this apart
                       from the tool's picks, the judge is not discriminating.
B3 'rules only'      - heuristic.best_window: text-cue scoring, no AI.
"""
from __future__ import annotations

import random

from .heuristic import best_window
from .parse import Episode
from .timeline import Promo, segment_from_times
from .validate import validate


def sample_episodes(eps: list[Episode], n: int, seed: int) -> list[Episode]:
    if n <= 0 or n >= len(eps):
        return list(eps)
    rng = random.Random(seed)
    return sorted(rng.sample(eps, n), key=lambda e: e.number)


def make_baselines(eps: list[Episode], E: dict, pcfg: dict, bcfg: dict) -> tuple[list[Promo], list[Episode]]:
    sample = sample_episodes(eps, bcfg["sample_episodes"], bcfg["seed"])
    rng = random.Random(bcfg["seed"] + 1)
    w = bcfg["window_s"]
    out: list[Promo] = []
    for ep in sample:
        segs = {
            "B1": segment_from_times(ep, max(0.0, ep.duration - w), ep.duration),
        }
        start = rng.uniform(0.0, max(0.0, ep.duration - w))
        segs["B2"] = segment_from_times(ep, start, start + w)
        b3 = best_window(ep, pcfg["hook_window_s"])
        if b3 is not None:
            segs["B3"] = b3
        for src, seg in segs.items():
            p = Promo(promo_id=f"{ep.eid}-{src}", kind="baseline", source=src, segments=[seg])
            if not validate(p, E, pcfg):
                out.append(p)
    return out, sample
