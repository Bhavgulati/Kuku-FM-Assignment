# Promo Finder: finding promos in microdrama screenplays
**Problem 1 (Find the Promos)** · Bhavishya Gulati · 20234049 · Electronics and Communication Engineering · Prototype: https://github.com/Bhavgulati/Kuku-FM-Assignment · Raw outputs: https://drive.google.com/drive/folders/1ygWtC8yZLoSYEPBGFS7OmEDp0X111woM

**In one line:** a re-runnable command-line tool that reads a show's screenplay files and returns *ranked* 20–60s promos, both single clips and cross-episode montages. Each promo comes with exact segment timestamps, the hook, the cliffhanger and why it works. The tool then **grades its own picks with a blind AI "cold viewer"** that also grades naive baselines, so the claims below are measured rather than asserted.

## Why Problem 1
I chose Problem 1 because its output can be checked, not just liked. A promo either has a hook in its first 3 seconds, ends unresolved and makes sense to a cold viewer, or it doesn't. Every pick is a set of timestamps an editor can jump to in seconds. The screenplays already give a start and end time for every line, so a whole 50-episode show (about 103 minutes) can be searched from text without watching it. A tool can do the first pass while the editor keeps the final call. It is also the problem where it is easiest to fool yourself, because almost any microdrama clip looks dramatic. So I wanted one where I could measure the AI against naive shortcuts (the episode's last 45 seconds, a random window, simple rules) and report where it loses

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
| **v2: AI + rule hints** (final, 79 clips) | **3.14** | 3.81 | 3.10 | **3.35** | 10% |
| v1: AI only (98 clips) | 2.86 | 3.80 | 3.16 | 3.27 | 14% |
| B1 episode ending | 2.40 | 3.70 | 3.25 | 3.12 | 5% |
| B2 random window | 2.25 | 3.40 | 3.50 | 3.05 | 30% |
| B3 rules only | 3.55 | 3.65 | 3.15 | 3.45 | 10% |

Baselines are from the final run. In the v1 run the same baselines scored within 0.05 on every column, which shows how much the mix of openings in a batch moves the scores. Misread = the grader found the viewer's understanding contradicts or only partly matches the logline.

- **v1 lost to simple rules on the hook.** The AI-only proposer beat the naive baselines on hooks, but **lost to the rules-only picker (3.55)**. Reading the openings side by side shows why: the AI opens a beat *before* the shock. In Ep 24 it opens on *"You thought? A new maid…"* (judge: 2) and the riding-crop strike comes 10s later, while B3 opens on the strike (5).
- **So v2 makes the rules suggest openings and lets the AI build the story.** Clips that open on a rule-flagged moment rose from 37% to 65%, and the mean hook rose from 2.86 to 3.14, still below rules-only. On its own that gain is not proof: the bootstrap 95% CI of the difference is −0.08 to +0.65. The per-episode test is clearer. On the 20 sampled episodes, v2's first pick beats the episode-ending baseline in **14** (v1: 9) and the rules-only pick in **10** (v1: 5), with 1 and 5 ties. That is suggestive but not proven at n = 20 (sign test p ≈ 0.06 against B1). Example: in E23 v1 opened 3s before *"Someone is poisoning her."* (hook 1), while v2 opens on that line (4).
- **A code check on the AI's own claims.** The proposer quotes its hook line, and code checks that the line really plays in the first 3s. v1: 51 of 64 correct. 12 of the misses play later than claimed or outside the clip; in E10-P1 the claimed line is at 00:25.5, but the clip starts at 00:28.0. One quote (E13-P2) stitches three real shouts together in the wrong order. v2: 38 of 42. Flash proposer: 19 of 19. Every mismatch is flagged on the editor's card.
- **The judge is noisy on hooks.** Re-judging the same 20 promos in different batches, in two runs: hooks 30–47% exact and 95–100% within ±1; cliffhanger 85–90% exact; stands-alone 55–70% exact. Rank differences below ~0.5 mean little. Cliffhanger scores barely separate the groups (3.4–3.8), because nearly every scripted moment is tense.
- **Where it picks badly.**
    - Quiet bonding episodes give weak hooks (E08 *"Boring dialogue about plants"*, judge 1; E17 still 1–2 in v2).
    - The AI sometimes opens on scenery (E37 v1: wedding invitations, judge 1), or picks the weakest suggestion. In E24 the strike was suggested, but v2 opened on an earlier suggestion, a question (hook 1).
    - Hand-checking the shortlist: E10-P1 (#9) promises *"what hidden horrors lie beneath his coat?"* but shows the scars inside the clip, so it really ends on a reaction.
    - Montages need context (v2 hook 2.4, n = 5).
    - 42% of v1 proposals (39% in v2) failed validation on the first try. Most were fixed by the repair round, and 9 per run were dropped, almost all for length. The model is weak at timestamp arithmetic, which is exactly why code owns the timestamps.
- **Model strength matters.** On the same 12 episodes with the same Flash-Lite judge (no hints), a **Flash** proposer scored 3.41 combined vs Flash-Lite's 3.14 (hook 3.23 vs 2.71, cliffhanger 3.74 vs 3.48). Its viewers misread 1 of 23 clips vs 4 of 21, and **none of its 23 proposals broke a rule**, vs 9 of 23 for Flash-Lite. With n ≈ 22 and hook batches that differ between the runs, the clearest signal is the rule-following. With a paid key, use Flash as the proposer.

## Scale and cost
- **One show** (50 episodes, 103 min): the final run makes 271 API calls, using 750k input and 47k output tokens (thinking included). It takes about 26 min from scratch on the free tier, with the tool throttled to 12 requests/min, and a cached re-run is free. Two-thirds of the calls are production: proposing, plus judging our own promos for the ranking. The rest is evaluation (baselines, re-test). Episodes are independent, so the work parallelises.
- **Paid cost (estimate):** Gemini 3.5 Flash-Lite lists $0.30 per 1M input tokens and $2.50 per 1M output tokens (Google AI Studio pricing page, 20 Nov 2026). The measured tokens give **≈ $0.34 per show including evaluation, or $0.27 without it**. gemini-3.8-flash ($0.75 / $3.75, doubling after 31 Dec 2026) would cost about $0.75 at the same token counts. Ten shows a week costs about $3.50 on Flash-Lite. The editor's time drops from watching about 100 minutes to reviewing about 15 ranked cards.
- **Real limit = quota, not money.** This free key allowed about 20 requests per day per Flash model and about 500 per day for Flash-Lite, so the stronger Flash proposer covered only 12 episodes. A paid key removes the limit. The cache makes any interruption resumable: the final run stopped at the daily cap and finished the next day, needing only 65 new calls.
- **Next steps (not built):** log the editor's accept/reject decisions to tune the weights and add few-shot examples; feed real post-performance metrics (3s retention, click-through) back in as the ground truth the judge currently stands in for.

## AI tools used, and where they got it wrong
I built this with **Claude Code (Claude Opus 5.5)** for planning, code and analysis. **Gemini 3.5 Flash-Lite** runs inside the tool. All the decisions in this doc are mine. Things the AI got wrong (full list in `docs/build_log.md`):

- **The "blind" judge wasn't blind.** The plan let the judge see speaker labels, and the judge then asked *"Why is Juliana referred to as a maid in the dialogue, but as Lady Juliana in the stage directions?"* The label gave away a disguise the clip keeps hidden. Fixed by masking unspoken names.
- **The grader confused "not mentioned" with "wrong".** It flagged 16% of promos as misunderstood, including a viewer summary that matched the logline, because the viewer named a character the one-line summary leaves out. The prompt was fixed and everything re-graded (v1: 16% → 14%).
- **Unverified numbers and checks.** The plan said 106 scenes, because a grep also counted Ep 6's upper-case field lines. The real count is 100, caught by a parser test. The AI's own hook-quote check called 2 real v2 quotes "invented", because it could not match a quote that merges a one-word line like *"Dignity?"*. Hand-checking caught it. The AI also used up the scarce daily quota on probes before measuring it.
