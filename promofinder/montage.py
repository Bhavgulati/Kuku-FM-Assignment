"""Stage 2: multi-segment montages across the series (one LLM call over a 'series spine')."""
from __future__ import annotations

from .llm.base import LLMClient, LLMError, QuotaExhausted, items_of
from .parse import Episode, fmt_time
from .prompting import fill, load_prompt
from .timeline import Promo, make_segment
from .validate import validate

MAX_SPINE_CHARS = 400_000  # ~100k tokens; above this only lines near highlights are kept


def _spine(eps: list[Episode], near: dict[int, set[int]] | None = None, radius: int = 6) -> str:
    out: list[str] = []
    for ep in eps:
        out.append(f"=== EPISODE {ep.number} === {ep.logline}")
        keep = None
        if near is not None:
            keep = {i for c in near.get(ep.number, set()) for i in range(c - radius, c + radius + 1)}
        for b in ep.blocks:
            if b.kind == "CAMERA":
                continue
            if keep is not None and b.idx not in keep:
                continue
            out.append(f"{b.local_id} {fmt_time(b.start)}–{fmt_time(b.end)} {b.display_label()}: {b.display_text()}")
    return "\n".join(out)


def _highlights(singles: list[Promo]) -> str:
    lines = []
    for p in singles:
        s = p.segments[0]
        lines.append(f"E{s.episode}: {s.start_block.split('-')[1]}→{s.end_block.split('-')[1]} | hook: {p.hook} | "
                     f"cliffhanger: {p.cliffhanger}")
    return "\n".join(lines) or "(none)"


def _build(item: dict, pid: str, E: dict, pcfg: dict) -> tuple[Promo | None, list[str]]:
    segs = []
    raw = item.get("segments") or []
    if not isinstance(raw, list) or not raw:
        return None, ["no segments given"]
    for n, s in enumerate(raw, 1):
        try:
            ep = E.get(int(s.get("episode")))
        except (TypeError, ValueError):
            ep = None
        if ep is None:
            return None, [f"segment {n}: episode {s.get('episode')!r} is not in the input"]
        try:
            segs.append(make_segment(ep, str(s.get("start_block", "")), str(s.get("end_block", ""))))
        except ValueError as e:
            return None, [f"segment {n}: {e}"]
    t = (item.get("trope") or "").strip().lower()
    p = Promo(
        promo_id=pid, kind="montage", source="llm", segments=segs,
        hook=str(item.get("hook", "")).strip(), cliffhanger=str(item.get("cliffhanger", "")).strip(),
        why=str(item.get("why", "")).strip(), trope=t if t in pcfg["tropes"] else "other",
    )
    errs = validate(p, E, pcfg)
    if len(segs) < 2:
        errs.append("a montage needs at least 2 segments")
    return p, errs


def propose_montages(client: LLMClient, eps: list[Episode], singles: list[Promo], E: dict,
                     pcfg: dict) -> tuple[list[Promo], list[dict]]:
    m = pcfg["montages"]
    if m <= 0:
        return [], []
    system, user_t = load_prompt("propose_montage")
    system = fill(system, MIN_S=int(pcfg["min_s"]), MAX_S=int(pcfg["max_s"]), HOOK_S=int(pcfg["hook_window_s"]),
                  MAX_SEG=pcfg["max_segments"], MIN_SEG_S=int(pcfg["min_segment_s"]), M=m,
                  TROPES=", ".join(pcfg["tropes"]))
    spine = _spine(eps)
    if len(spine) > MAX_SPINE_CHARS:  # very long show / small-context model: keep lines near the highlights
        near: dict[int, set[int]] = {}
        for p in singles:
            s = p.segments[0]
            ep = E[s.episode]
            near.setdefault(s.episode, set()).update({ep.block(s.start_block).idx, ep.block(s.end_block).idx})
        spine = _spine(eps, near)
    user = fill(user_t, HIGHLIGHTS=_highlights(singles), SPINE=spine)
    rejected: list[dict] = []
    try:
        res = client.complete_json(system, user, tag="montage:series", temperature=pcfg["temperature"])
    except QuotaExhausted:
        raise
    except LLMError as e:
        return [], [{"stage": "montage", "reason": f"LLM error: {e}"}]
    items = items_of(res, "montages")[:m]
    good, bad = [], []
    for n, it in enumerate(items, 1):
        p, errs = _build(it, f"M{n}", E, pcfg)
        (good.append(p) if p is not None and not errs else bad.append((n, it, errs)))
    if bad:
        problems = "\n".join(f"- montage {n}: {'; '.join(errs)}" for n, _, errs in bad)
        repair_user = (user + "\n\nYOUR PREVIOUS ANSWER BROKE THESE RULES:\n" + problems +
                       f"\nReturn corrected versions of ONLY these {len(bad)} montage(s), same JSON shape "
                       '{"montages": [...]}, same order. Leave out any that cannot be fixed.')
        try:
            res2 = client.complete_json(system, repair_user, tag="montage:repair", temperature=pcfg["temperature"])
            fixed = items_of(res2, "montages")[: len(bad)]
        except QuotaExhausted:
            raise
        except LLMError as e:
            fixed = []
            rejected.append({"stage": "montage-repair", "reason": f"LLM error: {e}"})
        for (n, it, errs), fx in zip(bad, fixed + [None] * (len(bad) - len(fixed))):
            if fx is None:
                rejected.append({"stage": "montage", "candidate": n, "proposal": it, "reason": "; ".join(errs),
                                 "repair": "not returned"})
                continue
            p, errs2 = _build(fx, f"M{n}", E, pcfg)
            if p is not None and not errs2:
                p.repaired = True
                good.append(p)
            else:
                rejected.append({"stage": "montage", "candidate": n, "proposal": it, "reason": "; ".join(errs),
                                 "repair": fx, "repair_reason": "; ".join(errs2)})
    good.sort(key=lambda p: int(p.promo_id[1:]))
    return good, rejected
