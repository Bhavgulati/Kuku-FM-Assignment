# Promo Finder: finding promos in microdrama screenplays
**Problem 1 (Find the Promos)** · [Name] · [Roll no.] · [Branch] · Prototype: [GitHub link] · Raw outputs: [Drive link] · Video: [link]

**In one line:** a re-runnable command-line tool that reads a show's screenplay files and returns *ranked* 20–60s promos, both single clips and cross-episode montages. Each promo comes with exact segment timestamps, the hook, the cliffhanger and why it works. The tool then **grades its own picks with a blind AI "cold viewer"** that also grades naive baselines, so the claims below are measured rather than asserted.

## Why Problem 1
[Confirm in your own words.] It is one person (the promo editor), one weekly step (watching about 100 minutes of a show to pick clips), and one output anyone can check (timestamps). Kuku releases shows every week, so the step repeats constantly. The screenplays already carry a timestamp on every line, which means a tool can do the first pass and leave the final choice to the editor.

## How it works: what is AI, what is code, where the human stays
1. **Parse (code).** Each `.txt` file becomes a timeline of 3,727 timed blocks (camera, action, sound, dialogue). The parser tolerates format drift: Episode 6 writes its field names in upper case.
2. **Propose (AI + rules).** Per episode, the LLM reads the script and proposes up to 3 clips **by block ID, never by timestamp**. Code first lists the 6 most visceral moments it finds with simple text rules (strikes, screams, "!" lines, intense delivery tags) as suggested openings. The AI decides the story and where the cliffhanger cut goes.
3. **Validate and snap (code).** Block IDs are turned into timestamps, and cuts are snapped so they **never split a line of dialogue**. Code enforces 20–60s, at most 3 segments and segments of at least 3s. A broken proposal goes back to the model once with the exact error ("74.3s > 60s"); if it is still broken, it is dropped and logged. Every output timestamp traces back to a script line.
4. **Montages (AI).** One call over a "series spine" (all dialogue and action lines) builds 2–3-segment promos across episodes, such as a setup in Ep 1 followed by its twist in Ep 3. Code validates them in the same way.
5. **Blind judge (AI).** A model that never sees the title, the loglines or the proposer's claims reads only what a viewer would see and hear.
    - It rates the **first 3 seconds on their own**, about 10 shuffled openings per call, with ours mixed in with the baselines.
    - It writes what it understood, what confused it and what it is left asking, and scores the cliffhanger and stands-alone 1–5.
    - A grader then checks its understanding against the true loglines.
    - **Names that are never spoken in the clip are masked** ("Character A"). The first test showed why: an unmasked label let the judge see through a disguise the clip itself keeps hidden.
6. **Rank (code).** An equal-weight mean of hook, cliffhanger and stands-alone, minus a misunderstanding penalty, with overlap dedupe and a mild variety push. Ties go to the earlier episode.
7. **Human in the loop.** The editor opens `report.html` and reviews the top 15. Each card shows the timestamps, the script excerpt with the first 3 seconds highlighted, the blind viewer's reaction and any flags.

**Swapping the model** is one config line, or `--model` / `--provider` / `--base-url`. The tool includes adapters for Gemini and for any OpenAI-compatible endpoint, and the judge can be a different model from the proposer. Prompts are plain files. Every response is cached, so reruns are free and an interrupted run resumes.

## Assumptions (where the brief is silent)
- Script timestamps match the final video, since no video was provided. Whether a hook *lands visually* can't be verified from text, so camera-only openings are flagged.
- Promo length is the sum of its segments, 20–60s. Montages have at most 3 segments of at least 3s each, played in the listed order. A cut never splits a spoken line.
- Hook, cliffhanger and stands-alone count equally, because the brief gives no priority. Spoilers are allowed, since the show's title already gives away the twist. How deep the episode is (how far a new viewer must watch to reach the moment) is shown, not penalized.
- Audience: US, English-speaking, scrolling with no context. "Stands alone" means only what is shown or spoken counts.

## How I know it's good, and where it isn't
No human labels were used. The same blind judge scores the tool and three baselines on 20 sampled episodes: **B1** the last ~45s of the episode (the strongest naive shortcut, since episodes end on cliffhangers), **B2** a random window, and **B3** rules only, with no AI.

| Blind judge, 1–5 | Hook (3s) | Cliffhanger | Stands alone | Combined | Misread |
|---|---|---|---|---|---|
| **v2: AI + rule hints** (final) | ⟨pending⟩ | ⟨pending⟩ | ⟨pending⟩ | ⟨pending⟩ | ⟨pending⟩ |
| v1: AI only (98 clips) | 2.86 | 3.80 | 3.16 | 3.27 | ⟨regrade⟩ |
| B1 episode ending | 2.35 | 3.75 | 3.20 | 3.10 | ⟨regrade⟩ |
| B2 random window | 2.20 | 3.40 | 3.50 | 3.03 | ⟨regrade⟩ |
| B3 rules only | 3.55 | 3.65 | 3.15 | 3.45 | ⟨regrade⟩ |

- **v1 lost to simple rules on the hook.** The AI-only proposer beat the naive baselines on hooks (2.86 vs 2.35 and 2.20), but **lost to the rules-only picker (3.55)**. Reading the openings side by side shows why: the AI opens a beat *before* the shock. In Ep 24 it opens on *"You thought? A new maid…"* (judge: 2) and the riding-crop strike comes 10s later, while B3 opens on the strike (5). **v2 therefore makes the rules suggest openings and lets the AI build the story.** Clips that open on a rule-flagged moment rose from 37% to 65%. ⟨v2 result pending⟩
- **A code check on the AI's own claims.** The proposer quotes its hook line, and code checks that the line really plays in the first 3s. v1: 48/66 correct. In E10-P1 the claimed hook line sits at 00:25.5 but the clip starts at 00:28.0, and 4 quoted lines don't exist in the script at all. v2: 35/43. These are flagged for the editor.
- **The judge is noisy on hooks.** Re-judging 20 promos in different batches: hooks 47% exact, 100% within ±1; cliffhanger 90%; stands-alone 70%. Rank differences below ~0.5 mean nothing. Cliffhanger scores barely separate the groups (3.4–3.8), because nearly every scripted moment is tense.
- **Where it picks badly.** Quiet bonding episodes give weak hooks (E08 *"Boring dialogue about plants"*, judge 1; E17). The AI sometimes opens on scenery (E37: wedding invitations, judge 1). Early montages need context (stands-alone 2.33). 37% of v1 proposals broke the 20–60s rule and had to be repaired (9 dropped), because the model is weak at timestamp arithmetic, which is exactly why code owns the timestamps. The naive "episode ending" baseline beat the AI's first pick in 9 of 20 episodes in v1.
- **Model strength matters.** ⟨Flash vs Flash-Lite proposer on Eps 1–11 and 13, same judge: pending⟩

## Scale and cost
- **One show** (50 episodes, 103 min): about 300 API calls, about 0.8M input and 55k output tokens, about 26 min wall time on the free tier. That includes all the evaluation (baselines, re-test); production needs roughly 60% of it. Episodes are independent, so the work parallelises.
- **Paid cost (estimate):** Gemini 3.5 Flash-Lite lists $0.30 per 1M input tokens and $2.50 per 1M output tokens (Google AI Studio pricing page, 20 Nov 2026). That gives **≈ $0.40 per show** including evaluation. Using gemini-3.8-flash ($0.75 / $3.75) as proposer and judge would be about $0.80. Ten shows a week costs under $10. The editor's time drops from watching about 100 minutes to reviewing about 15 ranked cards.
- **Real limit = quota, not money.** This free key allowed about 20 requests per day per Flash model and about 500 per day for Flash-Lite, so the stronger Flash proposer covered only 12 episodes. A paid key removes the limit. The cache makes any interruption resumable.
- **Next steps (not built):** log the editor's accept/reject decisions to tune the weights and add few-shot examples; feed real post-performance metrics (3s retention, click-through) back in as the ground truth the judge currently stands in for.

## AI tools used, and where they got it wrong
I built this with **Claude Code (Claude Opus 5.5)** for planning, code and analysis. **Gemini 3.5 Flash-Lite** runs inside the tool. All the decisions in this doc are mine. Things the AI got wrong (full list in `docs/build_log.md`):

- **The "blind" judge wasn't blind.** The plan let the judge see speaker labels, and the judge then asked *"Why is Juliana referred to as a maid in the dialogue, but as Lady Juliana in the stage directions?"* The label gave away a disguise the clip keeps hidden. Fixed by masking unspoken names.
- **The grader confused "not mentioned" with "wrong".** It flagged 16% of promos as misunderstood, including a viewer summary that matched the logline, because the viewer named a character the one-line summary leaves out. The prompt was fixed and everything re-graded.
- **Unverified numbers.** The plan said 106 scenes, because a grep also counted Ep 6's upper-case field lines. The real count is 100, caught by a parser test. The AI also used up the scarce daily quota on probes before measuring it.
