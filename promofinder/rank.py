"""Stage 4: turn judge outputs into a posting order (code only, fully transparent)."""
from __future__ import annotations

from .timeline import Promo


def score(p: Promo, rcfg: dict) -> None:
    j = p.judge
    h = (j.get("hook") or {}).get("score")
    c = (j.get("blind") or {}).get("cliffhanger")
    s = (j.get("blind") or {}).get("standalone")
    grade = (j.get("grade") or {}).get("grade")
    p.scores = {"hook": h, "cliffhanger": c, "standalone": s, "comprehension": grade}
    if None in (h, c, s):
        p.scores.update(combined=None, final=None)
        return
    w = rcfg["weights"]
    combined = (w["hook"] * h + w["cliffhanger"] * c + w["standalone"] * s) / (w["hook"] + w["cliffhanger"] + w["standalone"])
    pen = rcfg["comprehension_penalty"].get(grade, 0.0) if grade else 0.0
    p.scores.update(combined=round(combined, 3), comprehension_penalty=pen, final=round(combined - pen, 3))


def overlap_fraction(a: Promo, b: Promo) -> float:
    ov = 0.0
    for x in a.segments:
        for y in b.segments:
            if x.episode == y.episode:
                ov += max(0.0, min(x.end, y.end) - max(x.start, y.start))
    return ov / max(1e-6, min(a.duration, b.duration))


def _reason(p: Promo, rank: int) -> str:
    j, s = p.judge, p.scores
    hook = j.get("hook") or {}
    blind = j.get("blind") or {}
    bits = [f"#{rank}: score {s['final']:.2f}/5 (hook {s['hook']}, cliffhanger {s['cliffhanger']}, stands alone {s['standalone']})."]
    if hook.get("reason"):
        bits.append(f"Cold viewer on the first 3s: \"{hook['reason']}\".")
    oq = blind.get("open_question")
    if oq and oq.lower() != "none":
        bits.append(f"Left asking: \"{oq}\".")
    if blind.get("confusions"):
        bits.append(f"Confused by: {'; '.join(blind['confusions'][:2])}.")
    if s.get("comprehension") and s["comprehension"] != "consistent":
        bits.append(f"Understanding check: {s['comprehension']} ({(j.get('grade') or {}).get('note', '')}).")
    return " ".join(bits)


def rank(promos: list[Promo], rcfg: dict) -> list[dict]:
    """Greedy diversified ordering. Returns rows: {promo, rank, shortlisted, duplicate_of, effective}."""
    for p in promos:
        score(p, rcfg)
    scored = [p for p in promos if p.scores.get("final") is not None]
    unscored = [p for p in promos if p.scores.get("final") is None]
    thr, tp = rcfg["overlap_threshold"], rcfg["trope_penalty"]
    remaining = list(scored)
    selected: list[tuple[Promo, float]] = []
    while remaining:
        best, best_key = None, None
        for p in remaining:
            if any(overlap_fraction(p, q) > thr for q, _ in selected):
                continue
            same = sum(1 for q, _ in selected if q.trope == p.trope and p.trope != "other")
            eff = p.scores["final"] - tp * same
            key = (-eff, p.depth, p.promo_id)
            if best_key is None or key < best_key:
                best, best_key = p, key
        if best is None:
            break
        selected.append((best, -best_key[0]))
        remaining.remove(best)

    rows: list[dict] = []
    for i, (p, eff) in enumerate(selected, 1):
        rows.append({"promo": p, "rank": i, "shortlisted": i <= rcfg["shortlist"], "duplicate_of": None,
                     "effective": round(eff, 3), "reason": _reason(p, i)})
    n = len(rows)
    for p in sorted(remaining, key=lambda p: (-p.scores["final"], p.promo_id)):
        n += 1
        dup = max(selected, key=lambda qe: overlap_fraction(p, qe[0]))[0]
        rows.append({"promo": p, "rank": n, "shortlisted": False, "duplicate_of": dup.promo_id,
                     "effective": None, "reason": f"Overlaps {dup.promo_id}, which scored higher."})
    for p in unscored:
        n += 1
        rows.append({"promo": p, "rank": n, "shortlisted": False, "duplicate_of": None, "effective": None,
                     "reason": "Not ranked: judging incomplete (see failures)."})
    return rows
