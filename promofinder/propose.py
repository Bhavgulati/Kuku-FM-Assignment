"""Stage 1: per-episode promo proposals (LLM picks block IDs; code does the rest)."""
from __future__ import annotations

from .heuristic import intensity
from .llm.base import LLMClient, LLMError, QuotaExhausted, items_of
from .parse import Episode, fmt_time
from .prompting import fill, load_prompt
from .timeline import Promo, episode_listing, make_segment
from .validate import validate


def _norm_trope(t: str, tropes: list[str]) -> str:
    t = (t or "").strip().lower()
    return t if t in tropes else "other"


def _build(ep: Episode, item: dict, pid: str, E: dict, pcfg: dict) -> tuple[Promo | None, list[str]]:
    try:
        seg = make_segment(ep, str(item.get("start_block", "")), str(item.get("end_block", "")))
    except ValueError as e:
        return None, [str(e)]
    p = Promo(
        promo_id=pid,
        kind="single",
        source="llm",
        segments=[seg],
        hook=str(item.get("hook", "")).strip(),
        cliffhanger=str(item.get("cliffhanger", "")).strip(),
        why=str(item.get("why", "")).strip(),
        trope=_norm_trope(item.get("trope", ""), pcfg["tropes"]),
    )
    return p, validate(p, E, pcfg)


def hook_hints(ep: Episode, n: int, hook_s: float = 3.0, min_gap_s: float = 4.0) -> str:
    """Rules find the most visceral moments (same text cues as baseline B3); the LLM decides
    whether to open on one. Returns '' when disabled, which keeps the prompt identical to v1."""
    if n <= 0:
        return ""
    room = ep.duration - 20.0  # a clip opening later than this cannot reach 20s inside the episode
    ranked = sorted((b for b in ep.blocks if b.start <= room and intensity(b) > 0),
                    key=lambda b: (-intensity(b), b.start))
    picked = []
    for b in ranked:
        if all(abs(b.start - q.start) >= min_gap_s for q in picked):
            picked.append(b)
        if len(picked) == n:
            break
    if not picked:
        return ""
    lines = [f"{b.local_id} {fmt_time(b.start)} {b.display_label()}: {b.display_text()[:110]}"
             for b in sorted(picked, key=lambda b: b.start)]
    header = (
        "\n\nMOST VISCERAL MOMENTS, found by simple text rules (violent or shocking action, sound, intense "
        "delivery). These are often the strongest place to START a promo, so that the first "
        f"{int(hook_s)} seconds land without context. You decide: pick one if it can open a clip that also "
        "tells a clear story and ends on a cliffhanger.\n"
    )
    return header + "\n".join(lines)


def propose_episode(client: LLMClient, ep: Episode, E: dict, pcfg: dict) -> tuple[list[Promo], list[dict]]:
    """Returns (valid promos, rejected records). One repair round for rule violations."""
    system, user_t = load_prompt("propose_episode")
    k = pcfg["candidates_per_episode"]
    vals = dict(MIN_S=int(pcfg["min_s"]), MAX_S=int(pcfg["max_s"]), HOOK_S=int(pcfg["hook_window_s"]), K=k,
                TROPES=", ".join(pcfg["tropes"]))
    system = fill(system, **vals)
    user = fill(user_t, TITLE=ep.title, LOGLINE=ep.logline, CONTINUITY=ep.continuity or "(none)",
                LISTING=episode_listing(ep), HINTS_BLOCK=hook_hints(ep, pcfg.get("hook_hints", 0), pcfg["hook_window_s"]))
    rejected: list[dict] = []
    try:
        res = client.complete_json(system, user, tag=f"propose:{ep.eid}", temperature=pcfg["temperature"])
    except QuotaExhausted:
        raise
    except LLMError as e:
        return [], [{"episode": ep.number, "stage": "propose", "reason": f"LLM error: {e}"}]

    items = items_of(res, "promos")[:k]
    good: list[Promo] = []
    bad: list[tuple[int, dict, list[str]]] = []
    for n, item in enumerate(items, 1):
        p, errs = _build(ep, item, f"{ep.eid}-P{n}", E, pcfg)
        if p is not None and not errs:
            good.append(p)
        else:
            bad.append((n, item, errs))

    if bad:
        problems = "\n".join(
            f"- promo {n} (start {it.get('start_block')}, end {it.get('end_block')}): {'; '.join(errs)}"
            for n, it, errs in bad
        )
        repair_user = (
            user
            + "\n\nYOUR PREVIOUS ANSWER BROKE THESE RULES:\n"
            + problems
            + f"\nReturn corrected versions of ONLY these {len(bad)} promo(s), in the same JSON shape "
            + '{"promos": [...]}, in the same order. Fix them by choosing different block IDs. '
            + "If one cannot be fixed, leave it out."
        )
        try:
            res2 = client.complete_json(system, repair_user, tag=f"repair:{ep.eid}", temperature=pcfg["temperature"])
            fixed = items_of(res2, "promos")[: len(bad)]
        except QuotaExhausted:
            raise
        except LLMError as e:
            fixed = []
            rejected.append({"episode": ep.number, "stage": "repair", "reason": f"LLM error: {e}"})
        for (n, item, errs), fx in zip(bad, fixed + [None] * (len(bad) - len(fixed))):
            if fx is None:
                rejected.append({"episode": ep.number, "stage": "propose", "candidate": n, "proposal": item,
                                 "reason": "; ".join(errs), "repair": "not returned"})
                continue
            p, errs2 = _build(ep, fx, f"{ep.eid}-P{n}", E, pcfg)
            if p is not None and not errs2:
                p.repaired = True
                good.append(p)
            else:
                rejected.append({"episode": ep.number, "stage": "propose", "candidate": n, "proposal": item,
                                 "reason": "; ".join(errs), "repair": fx, "repair_reason": "; ".join(errs2)})
    good.sort(key=lambda p: p.promo_id)
    return good, rejected
