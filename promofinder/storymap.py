"""Story map: key beats per episode -> setup/payoff links across the series -> montages.

The spine builder (montage.py) asks a single call to find cross-episode stories in raw
script lines. Here that work is split into three steps:
  1. beats:  per group of episodes, the key beats (setup, secret, conflict, reveal, twist,
             cliffhanger, emotional) as block-ID ranges. Code converts them to snapped segments.
  2. links:  one call over the compact beat map, giving setup -> payoff links and storylines.
  3. build:  one call that builds montages from whole beats. Code supplies every beat's length,
             so the model only adds up numbers; code validates and runs one repair round.
The map is written to story_map.json so an editor can see why a montage was built.
"""
from __future__ import annotations

from dataclasses import dataclass

from .llm.base import LLMClient, LLMError, QuotaExhausted, items_of
from .montage import _build, _spine
from .parallel import pmap
from .parse import Episode, fmt_time
from .prompting import fill, load_prompt
from .timeline import Promo, Segment, make_segment

KINDS = ("setup", "secret", "conflict", "reveal", "twist", "cliffhanger", "emotional")


@dataclass
class Beat:
    id: str  # "E03.2" = episode 3, beat 2 (story order)
    episode: int
    start_block: str  # local block id, e.g. "B012"
    end_block: str
    seg: Segment
    kind: str
    summary: str
    needs: str

    def line(self) -> str:
        return f"{self.id} [{self.kind}] ({self.seg.duration:.1f}s) {self.summary} | needs: {self.needs}"

    def to_dict(self) -> dict:
        return {"id": self.id, "episode": self.episode, "start": fmt_time(self.seg.start), "end": fmt_time(self.seg.end),
                "duration_s": self.seg.duration, "start_block": self.seg.start_block, "end_block": self.seg.end_block,
                "kind": self.kind, "summary": self.summary, "needs": self.needs}


def extract_beats(client: LLMClient, eps: list[Episode], E: dict[int, Episode], pcfg: dict,
                  scfg: dict, workers: int) -> tuple[list[Beat], list[dict]]:
    system, user_t = load_prompt("story_beats")
    n = max(1, int(scfg["batch_episodes"]))
    groups = [eps[i : i + n] for i in range(0, len(eps), n)]

    def run(gi_group):
        gi, group = gi_group
        res = client.complete_json(system, fill(user_t, EPISODES=_spine(group)), tag=f"beats:g{gi:02d}",
                                   temperature=scfg["temperature"])
        return items_of(res, "beats")

    raw: list[tuple[set[int], dict]] = []
    rejected: list[dict] = []
    for group, res in zip(groups, pmap(run, list(enumerate(groups)), workers)):
        if isinstance(res, Exception):
            rejected.append({"stage": "beats", "episodes": [e.number for e in group], "reason": f"LLM error: {res}"})
            continue
        raw += [({e.number for e in group}, it) for it in res]

    kept: list[tuple[int, str, str, Segment, dict]] = []
    for allowed, it in raw:
        try:
            epn = int(it.get("episode"))
        except (TypeError, ValueError):
            epn = None
        why = None
        if epn not in allowed:
            why = f"episode {it.get('episode')!r} was not in this batch"
        else:
            sb, eb = str(it.get("start_block", "")).strip(), str(it.get("end_block", "")).strip()
            try:
                seg = make_segment(E[epn], sb, eb)
            except ValueError as e:
                why = str(e)
            else:
                if seg.duration < pcfg["min_segment_s"]:
                    why = f"beat lasts {seg.duration:.1f}s; at least {pcfg['min_segment_s']:.0f}s needed"
                elif seg.duration > scfg["max_beat_s"]:
                    why = f"beat lasts {seg.duration:.1f}s; at most {scfg['max_beat_s']:.0f}s"
        if why:
            rejected.append({"stage": "beats", "beat": it, "reason": why})
            continue
        kept.append((epn, sb, eb, seg, it))

    beats: list[Beat] = []
    kept.sort(key=lambda x: (x[0], x[3].start, x[3].end))
    count: dict[int, int] = {}
    for epn, sb, eb, seg, it in kept:
        if any(b.episode == epn and b.seg.start == seg.start and b.seg.end == seg.end for b in beats):
            continue  # the same stretch listed twice
        count[epn] = count.get(epn, 0) + 1
        kind = str(it.get("kind", "")).strip().lower()
        beats.append(Beat(f"E{epn:02d}.{count[epn]}", epn, sb, eb, seg, kind if kind in KINDS else "conflict",
                          str(it.get("summary", "")).strip(), str(it.get("needs", "")).strip() or "nothing"))
    return beats, rejected


def link_beats(client: LLMClient, beats: list[Beat], scfg: dict) -> tuple[list[dict], list[dict]]:
    if len(beats) < 2:
        return [], []
    system, user_t = load_prompt("story_links")
    system = fill(system, L=int(scfg["links"]))
    try:
        res = client.complete_json(system, fill(user_t, BEATS="\n".join(b.line() for b in beats)),
                                   tag="links:series", temperature=scfg["temperature"])
    except QuotaExhausted:
        raise
    except LLMError as e:
        print(f"  ! story links failed ({e}); building story-map montages without links")
        return [], []
    by_id = {b.id: b for b in beats}
    links = []
    for it in items_of(res, "links"):
        s, p = str(it.get("setup", "")).strip(), str(it.get("payoff", "")).strip()
        if s in by_id and p in by_id and (by_id[s].episode, by_id[s].seg.start) < (by_id[p].episode, by_id[p].seg.start):
            links.append({"setup": s, "payoff": p, "why": str(it.get("why", "")).strip()})
    threads = []
    if isinstance(res, dict):
        for t in items_of({"threads": res.get("threads")}, "threads"):
            ids = [str(x).strip() for x in (t.get("beats") or []) if str(x).strip() in by_id]
            if ids:
                threads.append({"name": str(t.get("name", "")).strip(), "beats": ids})
    return links[: int(scfg["links"])], threads


def _to_promo(item: dict, pid: str, by_id: dict[str, Beat], E: dict, pcfg: dict) -> tuple[Promo | None, list[str]]:
    ids = [str(x).strip() for x in (item.get("beats") or [])] if isinstance(item.get("beats"), list) else []
    if not ids:
        return None, ["no beats given"]
    missing = [x for x in ids if x not in by_id]
    if missing:
        return None, [f"beat id(s) {', '.join(missing)} are not in the map"]
    segs = [{"episode": by_id[x].episode, "start_block": by_id[x].start_block, "end_block": by_id[x].end_block}
            for x in ids]
    p, errs = _build({**item, "segments": segs}, pid, E, pcfg)
    if p is not None:
        p.source = "storymap"
    return p, errs


def storymap_montages(client: LLMClient, beats: list[Beat], links: list[dict], E: dict, pcfg: dict,
                      scfg: dict) -> tuple[list[Promo], list[dict]]:
    m = pcfg["montages"]
    if m <= 0 or len(beats) < 2:
        return [], []
    system, user_t = load_prompt("propose_storymap_montage")
    system = fill(system, MIN_S=int(pcfg["min_s"]), MAX_S=int(pcfg["max_s"]), HOOK_S=int(pcfg["hook_window_s"]),
                  MAX_SEG=pcfg["max_segments"], M=m, TROPES=", ".join(pcfg["tropes"]))
    link_lines = "\n".join(f"{l['setup']} → {l['payoff']}: {l['why']}" for l in links) or "(none found)"
    user = fill(user_t, LINKS=link_lines, BEATS="\n".join(b.line() for b in beats))
    by_id = {b.id: b for b in beats}
    rejected: list[dict] = []
    try:
        res = client.complete_json(system, user, tag="smontage:series", temperature=pcfg["temperature"])
    except QuotaExhausted:
        raise
    except LLMError as e:
        return [], [{"stage": "storymap-montage", "reason": f"LLM error: {e}"}]
    good, bad = [], []
    for n, it in enumerate(items_of(res, "montages")[:m], 1):
        p, errs = _to_promo(it, f"S{n}", by_id, E, pcfg)
        (good.append(p) if p is not None and not errs else bad.append((n, it, errs)))
    if bad:
        problems = "\n".join(f"- montage {n}: {'; '.join(errs)}" for n, _, errs in bad)
        repair_user = (user + "\n\nYOUR PREVIOUS ANSWER BROKE THESE RULES:\n" + problems +
                       f"\nReturn corrected versions of ONLY these {len(bad)} montage(s), same JSON shape "
                       '{"montages": [...]}, same order. Leave out any that cannot be fixed.')
        try:
            fixed = items_of(client.complete_json(system, repair_user, tag="smontage:repair",
                                                  temperature=pcfg["temperature"]), "montages")[: len(bad)]
        except QuotaExhausted:
            raise
        except LLMError as e:
            fixed = []
            rejected.append({"stage": "storymap-montage-repair", "reason": f"LLM error: {e}"})
        for (n, it, errs), fx in zip(bad, fixed + [None] * (len(bad) - len(fixed))):
            if fx is None:
                rejected.append({"stage": "storymap-montage", "candidate": n, "proposal": it,
                                 "reason": "; ".join(errs), "repair": "not returned"})
                continue
            p, errs2 = _to_promo(fx, f"S{n}", by_id, E, pcfg)
            if p is not None and not errs2:
                p.repaired = True
                good.append(p)
            else:
                rejected.append({"stage": "storymap-montage", "candidate": n, "proposal": it,
                                 "reason": "; ".join(errs), "repair": fx, "repair_reason": "; ".join(errs2)})
    good.sort(key=lambda p: int(p.promo_id[1:]))
    return good, rejected


def build(client: LLMClient, eps: list[Episode], E: dict, pcfg: dict, scfg: dict,
          workers: int) -> tuple[list[Promo], list[dict], dict]:
    beats, rej = extract_beats(client, eps, E, pcfg, scfg, workers)
    links, threads = link_beats(client, beats, scfg)
    montages, mrej = storymap_montages(client, beats, links, E, pcfg, scfg)
    story = {"beats": [b.to_dict() for b in beats], "links": links, "threads": threads,
             "rejected_beats": rej}
    return montages, mrej, story
