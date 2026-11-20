"""Write run outputs: ranked JSON/CSV, all candidates, baselines, quality.md, report.html."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .parse import Episode, fmt_time
from .timeline import Promo, render, view_items


def source_excerpt(p: Promo, E: dict[int, Episode], hook_s: float = 3.0) -> list[dict]:
    """Per block: episode timestamps (what an editor scrubs to) + promo-relative time."""
    rows = []
    for it in view_items(p, E):
        b = it.block
        rows.append({
            "segment": it.segment_index + 1,
            "episode": p.segments[it.segment_index].episode,
            "block": b.id,
            "src_start": fmt_time(b.start),
            "src_end": fmt_time(b.end),
            "promo_t": round(it.rel_start, 1),
            "in_hook_window": it.rel_start < hook_s,
            "kind": b.kind,
            "label": b.display_label(),
            "text": b.display_text(),
        })
    return rows


def promo_dict(p: Promo, E: dict[int, Episode], hook_s: float, row: dict | None = None) -> dict:
    blind = p.judge.get("blind") or {}
    d = {
        "rank": row["rank"] if row else None,
        "shortlisted": row["shortlisted"] if row else None,
        "promo_id": p.promo_id,
        "kind": p.kind,
        "source": p.source,
        "segments": [s.to_dict() for s in p.segments],
        "segments_label": p.segments_label(),
        "duration_s": p.duration,
        "episode_depth": p.depth,
        "trope": p.trope,
        "hook": p.hook,
        "cliffhanger": p.cliffhanger,
        "why_it_works": p.why,
        "scores": p.scores,
        "cold_viewer": {
            "hook_reason": (p.judge.get("hook") or {}).get("reason"),
            "understood": blind.get("understood"),
            "confusions": blind.get("confusions"),
            "open_question": blind.get("open_question"),
            "would_watch_more": blind.get("would_watch_more"),
            "comprehension_note": (p.judge.get("grade") or {}).get("note"),
        },
        "flags": p.flags,
        "repaired": p.repaired,
        "hook_quote_check": p.hook_claim,
        "head_to_head": p.scores.get("pairwise"),
        "rank_1to5": row.get("rank_1to5") if row else None,
        "duplicate_of": row.get("duplicate_of") if row else None,
        "rank_reason": row.get("reason") if row else None,
        "viewer_render": render(view_items(p, E)),
        "source_excerpt": source_excerpt(p, E, hook_s),
    }
    return d


CSV_COLS = ["rank", "shortlisted", "promo_id", "kind", "segments", "duration_s", "episode_depth", "trope", "hook",
            "cliffhanger", "why_it_works", "score_final", "hook_score", "cliffhanger_score", "standalone_score",
            "comprehension", "would_watch_more", "cold_viewer_open_question", "cold_viewer_confusions", "flags",
            "head_to_head", "rank_1to5", "duplicate_of", "rank_reason"]


def write_csv(path: Path, dicts: list[dict]):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for d in dicts:
            s, cv = d["scores"], d["cold_viewer"]
            w.writerow([d["rank"], d["shortlisted"], d["promo_id"], d["kind"], d["segments_label"], d["duration_s"],
                        d["episode_depth"], d["trope"], d["hook"], d["cliffhanger"], d["why_it_works"], s.get("final"),
                        s.get("hook"), s.get("cliffhanger"), s.get("standalone"), s.get("comprehension"),
                        cv.get("would_watch_more"), cv.get("open_question"), " | ".join(cv.get("confusions") or []),
                        ",".join(d["flags"]), _h2h(d.get("head_to_head")), d.get("rank_1to5"), d["duplicate_of"],
                        d["rank_reason"]])


def _h2h(h: dict | None) -> str:
    return "" if not h else f"{h['wins']}-{h['ties']}-{h['losses']} (strength {h['strength']:.2f})"


def _fmt(v):
    return "–" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def _pf(v):
    return "–" if v is None else f"{v:.0f}%"


def quality_md(q: dict, meta: dict) -> str:
    L = ["# Quality report", ""]
    L.append(f"Run: `{meta['run_id']}` · show folder: `{meta['input_name']}` · episodes: {meta['episodes']} · "
             f"proposer: `{meta['proposer_model']}` · judge: `{meta['judge_model']}`")
    L += ["", "No human labels were used. Every score below comes from the blind LLM judge, which sees only what a viewer "
          "would see. The judge scores the tool's promos and the naive baselines in exactly the same way.", ""]
    v = q["validity"]
    L += ["## 1. Hard rules (code)", "",
          f"- LLM returned {v['proposals_returned_by_llm']} proposals. {v['valid_promos']} valid promos "
          f"({v['valid_singles']} single clips, {v['valid_montages']} montages), "
          f"{v['repaired_then_valid']} of them fixed in the repair round. {v['dropped']} dropped.",
          f"- Drop reasons: {v['drop_reasons'] or 'none'}",
          f"- Final promos: {v['hard_rules_on_final_promos']}.", ""]
    L += ["## 2. Tool vs naive baselines (blind judge, 1–5)", "",
          "| Group | n | Hook (first 3s) | Cliffhanger | Stands alone | Combined | Would watch more | Misunderstood | Avg length |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, g in q["groups"].items():
        L.append(f"| {name} | {g['n']} | {_fmt(g['hook'])} | {_fmt(g['cliffhanger'])} | {_fmt(g['standalone'])} | "
                 f"{_fmt(g['combined'])} | {_pf(g['would_watch_more_pct'])} | {_pf(g['misunderstood_pct'])} | "
                 f"{_fmt(g['duration_mean_s'])}s |")
    L += ["", f"Baselines are run on a seeded sample of episodes: {q['sample_episodes']}.", "",
          "Per-episode head-to-head on the combined score (sampled episodes):", "",
          "| Comparison | Episodes | Win | Tie | Loss | Win % |", "|---|---|---|---|---|---|"]
    for name, h in q["head_to_head"].items():
        L.append(f"| {name} | {h['episodes']} | {h['win']} | {h['tie']} | {h['loss']} | {_pf(h['win_pct'])} |")
    r = q.get("retest") or {}
    L += ["", "## 3. Judge reliability (test-retest)", ""]
    if r:
        L.append(f"The same {r.get('n_promos')} promos were judged a second time, with hooks re-batched in a different "
                 "order and company (so this also measures sensitivity to batch context).")
        L += ["", "| Score | n | Exact agreement | Within ±1 | Mean abs diff |", "|---|---|---|---|---|"]
        for k in ("hook", "cliffhanger", "standalone"):
            a = r.get(k, {})
            L.append(f"| {k} | {a.get('n', 0)} | {_pf(a.get('exact_pct'))} | {_pf(a.get('within_1_pct'))} | "
                     f"{_fmt(a.get('mean_abs_diff'))} |")
    else:
        L.append("Not run.")
    L += _pairwise_md(q.get("pairwise") or {})
    L += ["", "## 4. Where it picks badly (automatic lists)", "",
          f"- Flags across the tool's promos: {q['flags'] or 'none'}",
          f"- Proposer's quoted hook line vs the actual cut (code check; `ok` = plays in the first 3s): "
          f"{q.get('hook_claims') or 'no quoted hooks'}",
          f"- Promos where the cold viewer listed at least one confusion: {q['confused_count']} of {q['validity']['valid_promos']}",
          ""]
    if q["misunderstood"]:
        L += ["### The viewer misread the situation (comprehension partial/contradicted)", ""]
        for m in q["misunderstood"]:
            L.append(f"- **{m['promo_id']}** ({m['segments']}): {m['comprehension']}. Viewer thought: "
                     f"\"{m['understood']}\". Grader: {m['grade_note']}")
        L.append("")
    L += ["### Lowest-scoring promos the tool proposed", ""]
    for m in q["worst"]:
        conf = "; ".join(m["confusions"] or []) or "none"
        L.append(f"- **{m['promo_id']}** ({m['segments']}): final {_fmt(m['final'])} (hook {m['hook']}, cliff "
                 f"{m['cliffhanger']}, alone {m['standalone']}). Hook note: \"{m['hook_reason']}\". Confusions: {conf}")
    losses = q["head_to_head"].get("first-listed vs B1", {}).get("losses", [])
    if losses:
        L += ["", "### Episodes where the naive 'episode ending' baseline beat the tool's first pick", ""]
        for x in losses:
            L.append(f"- Episode {x['episode']}: {x['tool']} {x['tool_score']} vs {x['baseline']} {x['baseline_score']}")
    u = meta["usage"]
    L += ["", "## 5. Cost of this run", "",
          f"- API calls: {u['api_calls']} (cache hits: {u['cache_hits']}), retries: {u['retries']}, JSON repairs: {u['json_repairs']}",
          f"- Tokens: {u['input_tokens']:,} in / {u['output_tokens']:,} out (includes cached calls' original usage)",
          f"- Wall time: {meta['seconds_total']:.0f}s", "", "| Stage | Calls | Cache hits | Input tokens | Output tokens |",
          "|---|---|---|---|---|"]
    for k, s in sorted(u["by_stage"].items()):
        L.append(f"| {k} | {s['calls']} | {s['cache_hits']} | {s['input_tokens']:,} | {s['output_tokens']:,} |")
    return "\n".join(L) + "\n"


def _tally_cell(t: dict) -> str:
    if not t or not t.get("n"):
        return "–"
    p = t.get("sign_test_p")
    return (f"{t['win']}–{t['tie']}–{t['loss']} ({_pf(t['win_pct'])} won" + (f", p={p:.2f}" if p is not None else "") + ")")


def _pairwise_md(pw: dict) -> list[str]:
    if not pw:
        return []
    L = ["", "## 3b. Head-to-head judging (pairwise)", "",
         "Each pair is judged twice with the order swapped, in different calls, on three criteria: hook (first "
         "3s), ending (an open question beats a resolution) and clear (follows with no context). Overall = whoever "
         "wins more criteria. A win counts only if it survives the swap; otherwise it is a tie. Results are "
         "win–tie–loss from the first side's point of view; p is a two-sided sign test on the decided pairs.", ""]
    t = pw.get("tournament")
    if t:
        c, hc = t["consistency"], t["hook_consistency"]
        L += [f"- Re-ranking: Swiss tournament on the top {t['pool']} candidates, {t['comparisons']} pairs. "
              f"Same verdict after the swap: {_pf(c['consistent_pct'])} overall, {_pf(hc['consistent_pct'])} on hooks.",
              f"- Agreement with the 1–5 order (Kendall tau): {_fmt(t['kendall_tau_vs_1to5'])}. Entered the shortlist: "
              f"{', '.join(t['shortlist_changed']) or 'none'}. Left it: {', '.join(t['shortlist_dropped']) or 'none'}."]
    vs = pw.get("vs_baselines")
    if vs:
        L += ["", "| Tool's first pick vs | Overall | Hook (first 3s) | Ending | Clear | Same overall verdict after swap |",
              "|---|---|---|---|---|---|"]
        for src, d in vs.items():
            L.append(f"| {src} | {_tally_cell(d['overall'])} | {_tally_cell(d['hook'])} | "
                     f"{_tally_cell(d.get('ending'))} | {_tally_cell(d.get('clear'))} | "
                     f"{_pf(d['consistency']['consistent_pct'])} |")
    mm = pw.get("storymap_vs_spine")
    if mm:
        L += ["", f"- Story-map montages vs spine montages (every pair): overall {_tally_cell(mm['overall'])}, "
                  f"hook {_tally_cell(mm['hook'])}, same verdict after swap {_pf(mm['consistency']['consistent_pct'])}."]
    lb = pw.get("length_bias")
    if lb:
        L += ["", "Length check: among decided pairs whose lengths differ by at least 5s, how often the LONGER promo "
                  "won (about 50% = length does not drive the verdict):", "",
              "| Comparison | Overall | Hook | Ending | Clear |", "|---|---|---|---|---|"]
        for name, d in lb.items():
            L.append(f"| {name} | " + " | ".join(
                f"{_pf(d[k]['longer_won_pct'])} of {d[k]['decided']}" for k in ("overall", "hook", "ending", "clear"))
                + " |")
    return L


def write_all(out: Path, rows: list[dict], baselines: list[Promo], rejected: list[dict], q: dict, meta: dict,
              E: dict[int, Episode], hook_s: float):
    ranked = [promo_dict(r["promo"], E, hook_s, r) for r in rows]
    (out / "promos_ranked.json").write_text(json.dumps(ranked, ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(out / "promos_ranked.csv", ranked)
    (out / "candidates_all.json").write_text(json.dumps(
        {"accepted": [{k: d[k] for k in ("promo_id", "kind", "segments_label", "duration_s", "hook", "cliffhanger",
                                         "why_it_works", "trope", "flags", "repaired")} for d in ranked],
         "rejected": rejected}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "baselines.json").write_text(json.dumps([promo_dict(b, E, hook_s) for b in baselines], ensure_ascii=False,
                                                   indent=1), encoding="utf-8")
    (out / "quality.json").write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    qmd = quality_md(q, meta)
    (out / "quality.md").write_text(qmd, encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.html").write_text(html_report(ranked, q, meta), encoding="utf-8")


# ---------------------------------------------------------------- HTML -----

CSS = """
:root{--bg:#f7f6f3;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e4e2dc;--accent:#b4232c;--chip:#f0eee8;
--good:#1f7a4d;--warn:#a35b00;--bad:#b4232c;--hl:#fff4cc}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--ink:#ececef;--muted:#9a9aa2;--line:#2e2e33;
--accent:#ff6b6b;--chip:#2a2a2f;--good:#5cc98f;--warn:#f0b24a;--bad:#ff6b6b;--hl:#3a3420}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:28px 16px 8px;max-width:1100px;margin:0 auto}h1{margin:0 0 4px;font-size:26px}h2{font-size:19px;margin:28px 0 10px}
.sub{color:var(--muted);font-size:13px}main{max-width:1100px;margin:0 auto;padding:0 16px 60px}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:14px 0}
.controls select,.controls label{font:inherit;font-size:13px;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.top{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}.rank{font-weight:700;font-size:22px;color:var(--accent);min-width:44px}
.ttl{flex:1;min-width:240px}.ttl b{font-size:16px}.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.chip{background:var(--chip);border-radius:999px;padding:1px 9px;font-size:12px;color:var(--muted)}
.chip.flag{color:var(--warn)}.seg{font-family:ui-monospace,Consolas,monospace;font-size:13px;background:var(--chip);border-radius:6px;padding:2px 6px;display:inline-block;margin:2px 4px 2px 0}
.scores{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;min-width:240px}
.sc{font-size:12px;color:var(--muted)}.bar{height:6px;background:var(--chip);border-radius:3px;overflow:hidden;margin-top:3px}
.bar i{display:block;height:100%;background:var(--accent)}.final{font-size:20px;font-weight:700}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px}@media(max-width:720px){.grid2{grid-template-columns:1fr}}
.k{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-top:6px}
.cv{border-left:3px solid var(--line);padding-left:10px}details{margin-top:10px}summary{cursor:pointer;color:var(--muted);font-size:13px}
table.x{width:100%;border-collapse:collapse;font-size:13px}table.x td,table.x th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
tr.hl td{background:var(--hl)}td.mono{font-family:ui-monospace,Consolas,monospace;white-space:nowrap}
.tblwrap{overflow-x:auto}.why{margin-top:8px}.reason{font-size:13px;color:var(--muted);margin-top:8px}
.good{color:var(--good)}.bad{color:var(--bad)}.warnc{color:var(--warn)}
"""

JS = """
function apply(){const k=document.getElementById('fk').value,t=document.getElementById('ft').value,s=document.getElementById('fs').checked;
document.querySelectorAll('.card[data-kind]').forEach(c=>{const ok=(!k||c.dataset.kind===k)&&(!t||c.dataset.trope===t)&&(!s||c.dataset.short==='1');c.style.display=ok?'':'none'})}
document.querySelectorAll('#fk,#ft,#fs').forEach(e=>e.addEventListener('change',apply));apply();
"""


def _e(s) -> str:
    return html.escape("" if s is None else str(s))


def _bar(label, v):
    w = 0 if v is None else int(v) * 20
    return f'<div class="sc">{label} <b>{_e(v if v is not None else "–")}</b>/5<div class="bar"><i style="width:{w}%"></i></div></div>'


def _card(d: dict) -> str:
    s, cv = d["scores"], d["cold_viewer"]
    segs = "".join(f'<span class="seg">Ep {x["episode"]} · {x["start"]} → {x["end"]} ({x["duration_s"]:.1f}s)</span>'
                   for x in d["segments"])
    chips = [f'<span class="chip">{_e(d["kind"])}</span>', f'<span class="chip">{_e(d["trope"])}</span>',
             f'<span class="chip">depth: Ep {d["episode_depth"]}</span>', f'<span class="chip">{d["duration_s"]:.1f}s</span>']
    chips += [f'<span class="chip flag">{_e(f)}</span>' for f in d["flags"]]
    if d.get("duplicate_of"):
        chips.append(f'<span class="chip flag">overlaps {_e(d["duplicate_of"])}</span>')
    if d.get("head_to_head"):
        chips.append(f'<span class="chip">head-to-head {_e(_h2h(d["head_to_head"]))}</span>')
    if d.get("source") == "storymap":
        chips.append('<span class="chip">from story map</span>')
    comp = s.get("comprehension")
    comp_cls = {"consistent": "good", "partial": "warnc", "contradicted": "bad"}.get(comp, "")
    conf = "".join(f"<li>{_e(c)}</li>" for c in (cv.get("confusions") or [])) or "<li>none</li>"
    rows = "".join(
        f'<tr class="{"hl" if r["in_hook_window"] else ""}"><td class="mono">{r["promo_t"]:.1f}s</td>'
        f'<td class="mono">Ep{r["episode"]} {r["src_start"]}</td><td>{_e(r["label"])}</td><td>{_e(r["text"])}</td></tr>'
        for r in d["source_excerpt"])
    final = s.get("final")
    return f"""
<div class="card" data-kind="{_e(d['kind'])}" data-trope="{_e(d['trope'])}" data-short="{'1' if d['shortlisted'] else '0'}">
 <div class="top"><div class="rank">#{_e(d['rank'])}</div>
  <div class="ttl"><b>{_e(d['promo_id'])}</b><div>{segs}</div><div class="chips">{''.join(chips)}</div></div>
  <div><div class="final">{_e(f'{final:.2f}' if final is not None else '–')}</div><div class="sc">final /5</div></div>
  <div class="scores">{_bar('Hook', s.get('hook'))}{_bar('Cliffhanger', s.get('cliffhanger'))}{_bar('Stands alone', s.get('standalone'))}</div>
 </div>
 <div class="grid2">
  <div><div class="k">Hook (first 3s)</div>{_e(d['hook'])}<div class="k">Cliffhanger</div>{_e(d['cliffhanger'])}
   <div class="k">Why it works</div>{_e(d['why_it_works'])}</div>
  <div class="cv"><div class="k">Blind cold viewer</div>
   <div><i>First 3s:</i> {_e(cv.get('hook_reason'))}</div><div><i>Understood:</i> {_e(cv.get('understood'))}</div>
   <div><i>Left asking:</i> {_e(cv.get('open_question'))}</div><div><i>Confused by:</i><ul>{conf}</ul></div>
   <div><i>Understanding check:</i> <span class="{comp_cls}">{_e(comp or '–')}</span> {_e(cv.get('comprehension_note'))}</div>
   <div><i>Would watch more:</i> {_e(cv.get('would_watch_more'))}</div></div>
 </div>
 <div class="reason">{_e(d.get('rank_reason'))}</div>
 <details><summary>Script excerpt: highlighted rows are the first 3 seconds; times are promo time and episode source time</summary>
  <div class="tblwrap"><table class="x"><tr><th>Promo</th><th>Source</th><th>Who / what</th><th>Text</th></tr>{rows}</table></div></details>
</div>"""


def html_report(ranked: list[dict], q: dict, meta: dict) -> str:
    tropes = sorted({d["trope"] for d in ranked})
    groups_rows = "".join(
        f"<tr><td>{_e(k)}</td><td>{g['n']}</td><td>{_fmt(g['hook'])}</td><td>{_fmt(g['cliffhanger'])}</td>"
        f"<td>{_fmt(g['standalone'])}</td><td>{_fmt(g['combined'])}</td><td>{_pf(g['misunderstood_pct'])}</td></tr>"
        for k, g in q["groups"].items())
    short = [d for d in ranked if d["shortlisted"]]
    rest = [d for d in ranked if not d["shortlisted"]]
    v = q["validity"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Promo Finder report</title><style>{CSS}</style></head><body>
<header><h1>Promo Finder: {_e(meta['input_name'])}</h1>
<div class="sub">Run {_e(meta['run_id'])} · {meta['episodes']} episodes · proposer {_e(meta['proposer_model'])} · judge {_e(meta['judge_model'])} ·
{v['valid_promos']} valid promos ({v['valid_singles']} clips, {v['valid_montages']} montages) · {v['dropped']} dropped by rule checks</div></header>
<main>
<h2>Quality at a glance (blind judge, 1–5; same judging for tool and baselines)</h2>
<div class="tblwrap"><table class="x"><tr><th>Group</th><th>n</th><th>Hook</th><th>Cliffhanger</th><th>Stands alone</th><th>Combined</th><th>Misunderstood</th></tr>{groups_rows}</table></div>
<div class="sub" style="margin-top:6px">Full numbers, reliability and failure lists: quality.md</div>
<div class="controls"><select id="fk"><option value="">all kinds</option><option value="single">single clips</option><option value="montage">montages</option></select>
<select id="ft"><option value="">all tropes</option>{''.join(f'<option>{_e(t)}</option>' for t in tropes)}</select>
<label><input type="checkbox" id="fs"> shortlist only</label></div>
<h2>Post first: shortlist ({len(short)})</h2>{''.join(_card(d) for d in short)}
<h2>All other candidates ({len(rest)})</h2>{''.join(_card(d) for d in rest)}
</main><script>{JS}</script></body></html>"""
