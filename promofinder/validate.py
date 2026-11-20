"""Hard rules every promo must pass (code, not AI), plus informational flags."""
from __future__ import annotations

import re

from .parse import Episode
from .timeline import EPS, Promo, dialogue_in_progress, view_items, window


def validate(promo: Promo, eps_by_num: dict[int, Episode], pcfg: dict) -> list[str]:
    """Return a list of human/LLM-readable rule violations (empty = valid)."""
    errs: list[str] = []
    segs = promo.segments
    if not segs:
        return ["no segments"]
    if promo.kind == "single" and len(segs) != 1:
        errs.append("a single promo must have exactly one segment")
    if len(segs) > pcfg["max_segments"]:
        errs.append(f"{len(segs)} segments; at most {pcfg['max_segments']} allowed")
    for i, s in enumerate(segs, 1):
        ep = eps_by_num.get(s.episode)
        if ep is None:
            errs.append(f"segment {i}: episode {s.episode} not in the input")
            continue
        if s.start < -EPS or s.end > ep.duration + EPS:
            errs.append(f"segment {i}: outside episode {s.episode} (0–{ep.duration:.1f}s)")
        if len(segs) > 1 and s.duration < pcfg["min_segment_s"]:
            errs.append(f"segment {i} lasts {s.duration:.1f}s; each segment must be at least {pcfg['min_segment_s']:.0f}s")
        for t, which in ((s.start, "start"), (s.end, "end")):
            b = dialogue_in_progress(ep, t)
            if b is not None:  # cannot happen via make_segment; kept as a guard
                errs.append(f"segment {i}: {which} cuts through dialogue line {b.local_id}")
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            if a.episode == b.episode and min(a.end, b.end) - max(a.start, b.start) > EPS:
                errs.append(f"segments {i + 1} and {j + 1} repeat the same footage")
    d = promo.duration
    if d < pcfg["min_s"] - EPS or d > pcfg["max_s"] + EPS:
        errs.append(f"total length is {d:.1f}s; it must be {pcfg['min_s']:.0f}–{pcfg['max_s']:.0f}s")
    return errs


def _words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split()


def hook_claim_check(promo: Promo, eps_by_num: dict[int, Episode], hook_s: float) -> str | None:
    """Does the line the proposer QUOTES as its hook really play in the first seconds?
    Returns None when the hook has no quote (nothing to check), else one of:
    'ok' | 'later_in_clip' | 'outside_clip' | 'not_in_script'. Matching uses the first 3 words,
    so quotes that merge two consecutive script lines still match."""
    quotes = re.findall(r'["“]([^"”]{8,})["”]', promo.hook or "")
    if not quotes:
        return None
    key = " ".join(_words(quotes[0])[:3])
    if len(key.split()) < 2:
        return None
    items = view_items(promo, eps_by_num)

    def said(its):
        return any(key in " ".join(_words(it.block.text)) for it in its if it.block.is_dialogue)

    if said(window(items, 0.0, hook_s)):
        return "ok"
    if said(items):
        return "later_in_clip"
    for s in promo.segments:
        if any(b.is_dialogue and key in " ".join(_words(b.text)) for b in eps_by_num[s.episode].blocks):
            return "outside_clip"
    return "not_in_script"


def flags(promo: Promo, eps_by_num: dict[int, Episode], pcfg: dict) -> list[str]:
    out: list[str] = []
    items = view_items(promo, eps_by_num)
    first = window(items, 0.0, pcfg["hook_window_s"])
    if first and all(it.block.kind == "CAMERA" for it in first):
        out.append("hook_is_camera_only")  # visual-only hook: can't verify from text how it lands
    if not any(it.block.is_dialogue for it in first):
        out.append("no_line_in_first_3s")
    s0, sl = promo.segments[0], promo.segments[-1]
    if s0.start <= EPS:
        out.append("opens_at_episode_start")  # episode openings often resolve the previous cliffhanger
    if sl.end >= eps_by_num[sl.episode].duration - EPS:
        out.append("ends_at_episode_end")
    if len(promo.segments) > 1:
        order = [(s.episode, s.start) for s in promo.segments]
        if order != sorted(order):
            out.append("non_chronological")
        if len(promo.episodes) > 1:
            out.append("cross_episode")
    if promo.repaired:
        out.append("repaired")
    hc = promo.hook_claim = hook_claim_check(promo, eps_by_num, pcfg["hook_window_s"])
    if hc and hc != "ok":
        out.append(f"hook_quote_{hc}")  # the proposer's own description of the hook does not match the cut
    return out
