"""Quality numbers: rule checks, tool vs naive baselines, judge reliability, failure lists."""
from __future__ import annotations

from collections import Counter

from .timeline import Promo


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def group_stats(promos: list[Promo]) -> dict:
    graded = [p.scores.get("comprehension") for p in promos if p.scores.get("comprehension")]
    wwm = [(p.judge.get("blind") or {}).get("would_watch_more") for p in promos]
    wwm = [x for x in wwm if x is not None]
    return {
        "n": len(promos),
        "hook": _mean(p.scores.get("hook") for p in promos),
        "cliffhanger": _mean(p.scores.get("cliffhanger") for p in promos),
        "standalone": _mean(p.scores.get("standalone") for p in promos),
        "combined": _mean(p.scores.get("combined") for p in promos),
        "would_watch_more_pct": _pct(sum(wwm), len(wwm)),
        "misunderstood_pct": _pct(sum(g != "consistent" for g in graded), len(graded)),
        "duration_mean_s": _mean(p.duration for p in promos),
    }


def head_to_head(ours: dict[int, Promo], base: dict[int, Promo]) -> dict:
    w = t = l = 0
    losses = []
    for ep, p in ours.items():
        b = base.get(ep)
        if b is None or p.scores.get("combined") is None or b.scores.get("combined") is None:
            continue
        d = p.scores["combined"] - b.scores["combined"]
        if d > 1e-9:
            w += 1
        elif d < -1e-9:
            l += 1
            losses.append({"episode": ep, "tool": p.promo_id, "tool_score": p.scores["combined"],
                           "baseline": b.promo_id, "baseline_score": b.scores["combined"]})
        else:
            t += 1
    n = w + t + l
    return {"episodes": n, "win": w, "tie": t, "loss": l, "win_pct": _pct(w, n), "losses": losses}


def agreement(pairs: list[tuple[int | None, int | None]]) -> dict:
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return {"n": 0}
    return {
        "n": len(pairs),
        "exact_pct": _pct(sum(a == b for a, b in pairs), len(pairs)),
        "within_1_pct": _pct(sum(abs(a - b) <= 1 for a, b in pairs), len(pairs)),
        "mean_abs_diff": round(sum(abs(a - b) for a, b in pairs) / len(pairs), 2),
    }


def compute(singles: list[Promo], montages: list[Promo], baselines: list[Promo], sample_eps: list[int],
            rejected: list[dict], raw_proposal_count: int, retest: dict | None) -> dict:
    sample = set(sample_eps)
    first_listed: dict[int, Promo] = {}
    best_of: dict[int, Promo] = {}
    for p in singles:
        ep = p.segments[0].episode
        if ep in sample:
            if ep not in first_listed or p.promo_id < first_listed[ep].promo_id:
                first_listed[ep] = p
            c = p.scores.get("combined")
            if c is not None and (ep not in best_of or c > (best_of[ep].scores.get("combined") or -1)):
                best_of[ep] = p
    by_src: dict[str, dict[int, Promo]] = {}
    for b in baselines:
        by_src.setdefault(b.source, {})[b.segments[0].episode] = b

    groups = {
        "Tool: all single clips": group_stats(singles),
        "Tool: first-listed clip per sampled episode": group_stats(list(first_listed.values())),
        "Tool: montages": group_stats(montages),
        "B1 episode ending (last ~45s)": group_stats(list(by_src.get("B1", {}).values())),
        "B2 random 45s window": group_stats(list(by_src.get("B2", {}).values())),
        "B3 rules only (no AI)": group_stats(list(by_src.get("B3", {}).values())),
    }
    h2h = {
        f"first-listed vs {src}": head_to_head(first_listed, by_src.get(src, {})) for src in ("B1", "B2", "B3")
    }
    h2h["best-of-episode vs B1 (optimistic: picked using the judge)"] = head_to_head(best_of, by_src.get("B1", {}))

    ours = singles + montages
    flag_counts = Counter(f for p in ours for f in p.flags)
    claims = Counter(p.hook_claim for p in ours if p.hook_claim)
    repaired = sum(p.repaired for p in ours)
    drop_reasons = Counter()
    for r in rejected:
        reason = r.get("repair_reason") or r.get("reason") or ""
        key = ("duration" if "total length" in reason else "bad block id" if "do not exist" in reason
               else "segment too short" if "at least" in reason else "not after start" if "not after" in reason
               else "LLM error" if "LLM error" in reason else "other")
        drop_reasons[key] += 1

    worst = sorted([p for p in ours if p.scores.get("final") is not None], key=lambda p: p.scores["final"])[:8]
    misunderstood = [p for p in ours if p.scores.get("comprehension") in ("partial", "contradicted")]
    confused = [p for p in ours if (p.judge.get("blind") or {}).get("confusions")]

    q = {
        "validity": {
            "proposals_returned_by_llm": raw_proposal_count,
            "valid_promos": len(ours),
            "valid_singles": len(singles),
            "valid_montages": len(montages),
            "repaired_then_valid": repaired,
            "dropped": len(rejected),
            "drop_reasons": dict(drop_reasons),
            "hard_rules_on_final_promos": "100% pass by construction (20-60s, <=3 segments, no cut inside a dialogue line, every timestamp from a source block)",
        },
        "groups": groups,
        "head_to_head": h2h,
        "flags": dict(flag_counts),
        "hook_claims": dict(claims),
        "judged_coverage": {
            "hook": sum(1 for p in ours if p.scores.get("hook") is not None),
            "blind": sum(1 for p in ours if p.scores.get("cliffhanger") is not None),
            "graded": sum(1 for p in ours if p.scores.get("comprehension")),
            "total": len(ours),
        },
        "retest": retest or {},
        "worst": [_brief(p) for p in worst],
        "misunderstood": [_brief(p) for p in misunderstood],
        "confused_count": len(confused),
        "sample_episodes": sorted(sample),
    }
    return q


def _brief(p: Promo) -> dict:
    b = p.judge.get("blind") or {}
    return {
        "promo_id": p.promo_id,
        "segments": p.segments_label(),
        "final": p.scores.get("final"),
        "hook": p.scores.get("hook"),
        "cliffhanger": p.scores.get("cliffhanger"),
        "standalone": p.scores.get("standalone"),
        "comprehension": p.scores.get("comprehension"),
        "grade_note": (p.judge.get("grade") or {}).get("note"),
        "understood": b.get("understood"),
        "confusions": b.get("confusions"),
        "hook_reason": (p.judge.get("hook") or {}).get("reason"),
    }
