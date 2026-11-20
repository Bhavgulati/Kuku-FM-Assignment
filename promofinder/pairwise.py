"""Head-to-head judging: "which of these two would you keep watching?"

The 1–5 scores turned out noisy: re-judging the same hook gave the same score only 30–47%
of the time, and cliffhanger scores barely separate anything. Comparisons are usually more
consistent. Every pair is judged twice, with the order swapped, in different calls. A win
only counts if it survives the swap; otherwise the pair is a tie. This cancels the judge's
preference for the first or second slot, and also measures it (`consistency`).

The judge answers three questions per pair, matching the brief: hook (first 3s only),
ending (an open question beats a resolution) and clear (follows with no context). The first
version asked one holistic "which makes you watch the show?" question instead, and the longer
promo won 80% of decided pairs ("fuller narrative", "more complete arc"), which contradicts the
cliffhanger rule. So "overall" is now computed in code: the promo that wins more of the three
criteria in that order, with equal weights, as in the 1-5 ranking. length_bias() keeps
measuring the problem in every run.

Used three ways:
  1. tournament(): re-rank the top of the list with a Swiss tournament + Bradley–Terry fit
  2. versus(): the tool's pick vs each baseline on the same episode
  3. all_pairs(): story-map montages vs spine montages
"""
from __future__ import annotations

import math

from .judge import blind_render
from .llm.base import LLMClient, items_of
from .parallel import pmap
from .prompting import fill, load_prompt
from .timeline import Promo

CRITERIA = ("hook", "ending", "clear")
KEYS = CRITERIA + ("overall",)


def pack(jobs: list[tuple[str, str]], batch: int) -> list[list[int]]:
    """Greedily put job indices into calls of at most `batch` jobs, so that no promo
    appears twice in one call. This also keeps a pair's two orders in different calls."""
    calls: list[list[int]] = []
    members: list[set[str]] = []
    for i, (a, b) in enumerate(jobs):
        for c, m in zip(calls, members):
            if len(c) < batch and a not in m and b not in m:
                c.append(i)
                m.update((a, b))
                break
        else:
            calls.append([i])
            members.append({a, b})
    return calls


def _pick(v, first: str, second: str) -> str | None:
    """'1' / '2' / 'tie' -> promo id / 'tie'; anything unreadable -> None (not judged)."""
    v = str(v or "").strip().lower().replace("promo", "").strip()
    return first if v == "1" else second if v == "2" else "tie" if v == "tie" else None


def _majority(v: dict, x: str, y: str) -> str | None:
    """One order's overall verdict: whoever wins more of the three criteria."""
    got = [v.get(k) for k in CRITERIA]
    if all(g is None for g in got):
        return None
    nx, ny = got.count(x), got.count(y)
    return x if nx > ny else y if ny > nx else "tie"


def judge_pairs(client: LLMClient, pairs: list[tuple[Promo, Promo]], E: dict, names: list[str] | None, *,
                batch: int, workers: int, hook_s: float, temperature: float, tag: str) -> list[dict]:
    """Judge each pair in both orders. Returns one record per pair: {a, b, reasons, and for each
    of hook / ending / clear / overall: the winning promo id, 'tie', or None if a verdict is
    missing, plus <key>_consistent}."""
    system, user_t = load_prompt("judge_pair")
    system = fill(system, HOOK_S=int(hook_s))
    renders = {p.promo_id: blind_render(p, E, names) for pair in pairs for p in pair}
    records: list[dict | None] = [None] * len(pairs)
    jobs: list[tuple[Promo, Promo]] = []
    for k, (a, b) in enumerate(pairs):
        if renders[a.promo_id] == renders[b.promo_id]:  # same footage: nothing to compare, no call
            records[k] = {"a": a.promo_id, "b": b.promo_id, "reasons": ["identical footage"],
                          **{key: "tie" for key in KEYS}, **{f"{key}_consistent": None for key in KEYS}}
            continue
        jobs += [(a, b), (b, a)]
    calls = pack([(x.promo_id, y.promo_id) for x, y in jobs], batch)

    def run(ci_call):
        ci, idxs = ci_call
        ids = {f"P{n + 1}": jobs[i] for n, i in enumerate(idxs)}
        items = "\n\n".join(
            f"### {pid}\nPROMO 1 ({x.duration:.0f}s):\n{renders[x.promo_id]}\n\n"
            f"PROMO 2 ({y.duration:.0f}s):\n{renders[y.promo_id]}" for pid, (x, y) in ids.items())
        res = client.complete_json(system, fill(user_t, ITEMS=items), tag=f"pair:{tag}:c{ci:02d}",
                                   temperature=temperature)
        out = {}
        for it in items_of(res, "results"):
            job = ids.get(str(it.get("id", "")).strip())
            if job is not None:
                x, y = job
                v = {key: _pick(it.get(key), x.promo_id, y.promo_id) for key in CRITERIA}
                v["overall"] = _majority(v, x.promo_id, y.promo_id)
                v["reason"] = str(it.get("reason", "")).strip()
                out[(x.promo_id, y.promo_id)] = v
        return out

    verdicts: dict = {}
    for res in pmap(run, list(enumerate(calls)), workers):
        if isinstance(res, Exception):
            print(f"  ! pair batch failed: {res}")
            continue
        verdicts.update(res)
    for k, (a, b) in enumerate(pairs):
        if records[k] is not None:
            continue
        v1, v2 = verdicts.get((a.promo_id, b.promo_id)), verdicts.get((b.promo_id, a.promo_id))
        rec = {"a": a.promo_id, "b": b.promo_id, "reasons": [v and v["reason"] for v in (v1, v2)]}
        for key in KEYS:
            w1, w2 = (v1 or {}).get(key), (v2 or {}).get(key)
            if w1 is None or w2 is None:
                rec[key], rec[f"{key}_consistent"] = None, None
            else:
                rec[key] = w1 if w1 == w2 else "tie"  # a win must survive the swap
                rec[f"{key}_consistent"] = w1 == w2
        records[k] = rec
    return records


def bradley_terry(ids: list[str], results: list[dict], key: str = "overall", prior: float = 1.0,
                  iters: int = 300) -> dict[str, float]:
    """Bradley–Terry strengths by the MM algorithm (Hunter 2004). A win counts 1, a tie 0.5
    for each side. `prior` adds a virtual tie with an average opponent (strength 1) for every
    promo, so an unbeaten promo keeps a finite strength. 1.0 = average."""
    known = set(ids)
    wins = {i: prior / 2 for i in ids}
    games: dict[str, dict[str, int]] = {i: {} for i in ids}
    for r in results:
        o, a, b = r.get(key), r["a"], r["b"]
        if o is None or a not in known or b not in known:
            continue
        games[a][b] = games[a].get(b, 0) + 1
        games[b][a] = games[b].get(a, 0) + 1
        if o == a:
            wins[a] += 1
        elif o == b:
            wins[b] += 1
        else:
            wins[a] += 0.5
            wins[b] += 0.5
    p = {i: 1.0 for i in ids}
    for _ in range(iters):
        p = {i: wins[i] / (sum(n / (p[i] + p[j]) for j, n in games[i].items()) + prior / (p[i] + 1.0))
             for i in ids}
    return p


def record(ids: list[str], results: list[dict], key: str = "overall") -> dict[str, dict]:
    rec = {i: {"wins": 0, "ties": 0, "losses": 0} for i in ids}
    for r in results:
        o = r.get(key)
        if o is None:
            continue
        for me, other in ((r["a"], r["b"]), (r["b"], r["a"])):
            if me in rec:
                rec[me]["wins" if o == me else "losses" if o == other else "ties"] += 1
    return rec


def consistency(results: list[dict], key: str = "overall") -> dict:
    """How often the judge gave the same verdict when the order was swapped."""
    xs = [r[f"{key}_consistent"] for r in results if r.get(f"{key}_consistent") is not None]
    return {"pairs": len(xs), "consistent_pct": round(100.0 * sum(xs) / len(xs), 1) if xs else None}


def tournament(client: LLMClient, pool: list[Promo], E: dict, names: list[str] | None, *, rounds: int, batch: int,
               workers: int, hook_s: float, temperature: float) -> dict:
    """Swiss tournament: each round pairs neighbours in the current standings (round 1 uses
    the incoming order) without repeating a pairing, then the Bradley–Terry fit is redone."""
    ids = [p.promo_id for p in pool]
    by_id = {p.promo_id: p for p in pool}
    seed_pos = {pid: n for n, pid in enumerate(ids)}
    played: set[frozenset] = set()
    results: list[dict] = []
    strength = {i: 1.0 for i in ids}
    for rnd in range(rounds):
        free = sorted(ids, key=lambda i: (-strength[i], seed_pos[i]))
        pairs = []
        while len(free) >= 2:
            a = free.pop(0)
            j = next((k for k, b in enumerate(free) if frozenset((a, b)) not in played), None)
            if j is None:
                continue  # everyone left has already met a; a sits this round out
            b = free.pop(j)
            played.add(frozenset((a, b)))
            pairs.append((by_id[a], by_id[b]))
        if not pairs:
            break
        results += judge_pairs(client, pairs, E, names, batch=batch, workers=workers, hook_s=hook_s,
                               temperature=temperature, tag=f"rank:r{rnd + 1}")
        strength = bradley_terry(ids, results)
    return {"strength": strength, "record": record(ids, results), "results": results,
            "consistency": consistency(results), "hook_consistency": consistency(results, "hook"),
            "by_criterion_consistency": {k: consistency(results, k) for k in KEYS}}


def versus(client: LLMClient, ours: dict[int, Promo], base_by_src: dict[str, dict[int, Promo]], E: dict,
           names: list[str] | None, *, batch: int, workers: int, hook_s: float, temperature: float) -> dict:
    """The tool's pick vs each baseline on the same episode."""
    pairs, srcs = [], []
    for src in sorted(base_by_src):
        for ep, p in sorted(ours.items()):
            b = base_by_src[src].get(ep)
            if b is not None:
                pairs.append((p, b))
                srcs.append(src)
    res = judge_pairs(client, pairs, E, names, batch=batch, workers=workers, hook_s=hook_s, temperature=temperature,
                      tag="eval")
    out: dict = {}
    for src in sorted(set(srcs)):
        rs = [r for r, s in zip(res, srcs) if s == src]
        out[src] = {"pairs": rs, **{k: _tally(rs, k) for k in KEYS}, "consistency": consistency(rs),
                    "hook_consistency": consistency(rs, "hook")}
    return out


def all_pairs(client: LLMClient, left: list[Promo], right: list[Promo], E: dict, names: list[str] | None, *,
              batch: int, workers: int, hook_s: float, temperature: float, tag: str) -> dict:
    """Every promo in `left` against every promo in `right` (a = left side)."""
    pairs = [(a, b) for a in left for b in right]
    res = judge_pairs(client, pairs, E, names, batch=batch, workers=workers, hook_s=hook_s, temperature=temperature,
                      tag=tag)
    return {"pairs": res, **{k: _tally(res, k) for k in KEYS}, "consistency": consistency(res)}


def _tally(rs: list[dict], key: str) -> dict:
    """Counts from the point of view of side a (the tool / the left group)."""
    judged = [r for r in rs if r.get(key) is not None]
    w = sum(r[key] == r["a"] for r in judged)
    l = sum(r[key] == r["b"] for r in judged)
    n = len(judged)
    return {"n": n, "win": w, "tie": n - w - l, "loss": l,
            "win_pct": round(100.0 * w / n, 1) if n else None,
            "sign_test_p": sign_test(w, l)}


def length_bias(results: list[dict], durations: dict[str, float], min_gap_s: float = 5.0) -> dict:
    """Among decided pairs whose lengths differ by >= min_gap_s: how often did the longer promo
    win? About 50% means length does not drive the verdict."""
    out = {}
    for key in KEYS:
        dec = [r for r in results if r.get(key) not in (None, "tie") and r["a"] in durations
               and r["b"] in durations and abs(durations[r["a"]] - durations[r["b"]]) >= min_gap_s]
        longer = sum(durations[r[key]] == max(durations[r["a"]], durations[r["b"]]) for r in dec)
        out[key] = {"decided": len(dec), "longer_won": longer,
                    "longer_won_pct": round(100.0 * longer / len(dec), 1) if dec else None}
    return out


def sign_test(w: int, l: int) -> float | None:
    """Two-sided exact sign test on decided pairs (ties dropped)."""
    n = w + l
    if n == 0:
        return None
    k = max(w, l)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)
