"""Stage 3: the blind 'cold scroller' judge.

The judge never sees the show title, the loglines, or what the proposer claimed about
the promo, only what a viewer would see and hear. Three kinds of call:
  * hook:   ONLY the first N seconds, ~10 anonymised and shuffled openings per call (ours
            mixed with baselines), scored for stopping power.
  * blind:  the whole promo, one per call so nothing leaks between promos, giving
            understanding, confusions, open question, cliffhanger and stands-alone scores.
  * grade:  the viewer's understanding vs the true loglines: consistent / partial /
            contradicted (catches 'thinks they understood but misread it').
"""
from __future__ import annotations

import random

from .llm.base import LLMClient, items_of, one_dict
from .names import Masker
from .parallel import pmap
from .parse import Episode
from .prompting import fill, load_prompt
from .timeline import EPS, Promo, render, view_items

HEARD_MARGIN = 0.3  # a line must start this long before the hook window ends to count as heard


def _clamp(v, lo=1, hi=5):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def hook_render(promo: Promo, E: dict[int, Episode], hook_s: float, names: list[str] | None = None) -> str:
    items = [it for it in view_items(promo, E) if it.rel_start < hook_s - HEARD_MARGIN + EPS]
    return render(items, mask=Masker(names or [], items)) or "(nothing)"


def blind_render(promo: Promo, E: dict[int, Episode], names: list[str] | None = None) -> str:
    items = view_items(promo, E)
    return render(items, mask=Masker(names or [], items))


def judge_hooks(client: LLMClient, promos: list[Promo], E: dict, hook_s: float, batch: int, seed: int,
                workers: int, replica: int = 0, temperature: float = 0.2,
                names: list[str] | None = None) -> dict[str, dict]:
    system, user_t = load_prompt("judge_hook")
    system = fill(system, HOOK_S=int(hook_s))
    # Identical openings (e.g. a montage that starts where a single clip starts) are judged
    # once and share the score: seeing a repeat in the same batch makes the judge mark it down.
    groups: dict[str, list[Promo]] = {}
    for p in promos:
        groups.setdefault(hook_render(p, E, hook_s, names), []).append(p)
    order = list(groups.items())
    random.Random(seed + replica).shuffle(order)
    batches = [order[i : i + batch] for i in range(0, len(order), batch)]

    def run(bi_batch):
        bi, group = bi_batch
        ids = {f"H{n + 1:02d}": (text, members) for n, (text, members) in enumerate(group)}
        items = "\n\n".join(f"### {hid}\n{text}" for hid, (text, _) in ids.items())
        res = client.complete_json(system, fill(user_t, ITEMS=items), tag=f"judge_hook:r{replica}:b{bi:02d}",
                                   temperature=temperature, replica=replica)
        out = {}
        for r in items_of(res, "ratings"):
            hit = ids.get(str(r.get("id", "")).strip())
            if hit is not None:
                for p in hit[1]:
                    out[p.promo_id] = {"score": _clamp(r.get("score")), "reason": str(r.get("reason", "")).strip()}
        return out

    merged: dict[str, dict] = {}
    for res in pmap(run, list(enumerate(batches)), workers):
        if isinstance(res, Exception):
            print(f"  ! hook batch failed: {res}")
            continue
        merged.update(res)
    return merged


def judge_blind(client: LLMClient, promos: list[Promo], E: dict, workers: int, replica: int = 0,
                temperature: float = 0.2, names: list[str] | None = None) -> dict[str, dict]:
    system, user_t = load_prompt("judge_blind")

    def run(p: Promo):
        user = fill(user_t, DURATION=f"{p.duration:.0f}", RENDER=blind_render(p, E, names))
        r = one_dict(client.complete_json(system, user, tag=f"judge_blind:r{replica}:{p.promo_id}",
                                          temperature=temperature, replica=replica))
        conf = r.get("confusions") or []
        if isinstance(conf, str):
            conf = [conf] if conf.strip() else []
        return {
            "understood": str(r.get("understood", "")).strip(),
            "confusions": [str(c).strip() for c in conf if str(c).strip()],
            "open_question": str(r.get("open_question", "")).strip(),
            "cliffhanger": _clamp(r.get("cliffhanger")),
            "standalone": _clamp(r.get("standalone")),
            "would_watch_more": bool(r.get("would_watch_more")) if r.get("would_watch_more") is not None else None,
        }

    out = {}
    for p, res in zip(promos, pmap(run, promos, workers)):
        if isinstance(res, Exception):
            print(f"  ! blind judge failed for {p.promo_id}: {res}")
            continue
        out[p.promo_id] = res
    return out


def grade_comprehension(client: LLMClient, promos: list[Promo], blind: dict[str, dict], E: dict, batch: int,
                        workers: int, temperature: float = 0.2) -> dict[str, dict]:
    system, user_t = load_prompt("grade_comprehension")
    todo = [p for p in promos if blind.get(p.promo_id, {}).get("understood")]
    batches = [todo[i : i + batch] for i in range(0, len(todo), batch)]

    def run(bi_batch):
        bi, group = bi_batch
        ids = {f"G{n + 1:02d}": p for n, p in enumerate(group)}
        parts = []
        for gid, p in ids.items():
            truth = " ".join(f"[Episode {n}] {E[n].logline}" for n in p.episodes)
            parts.append(f"### {gid}\nTRUTH: {truth}\nVIEWER: {blind[p.promo_id]['understood']}")
        res = client.complete_json(system, fill(user_t, ITEMS="\n\n".join(parts)), tag=f"grade:b{bi:02d}",
                                   temperature=temperature)
        out = {}
        for g in items_of(res, "grades"):
            p = ids.get(str(g.get("id", "")).strip())
            grade = str(g.get("grade", "")).strip().lower()
            if p is not None and grade in ("consistent", "partial", "contradicted"):
                out[p.promo_id] = {"grade": grade, "note": str(g.get("note", "")).strip()}
        return out

    merged: dict[str, dict] = {}
    for res in pmap(run, list(enumerate(batches)), workers):
        if isinstance(res, Exception):
            print(f"  ! grade batch failed: {res}")
            continue
        merged.update(res)
    return merged
