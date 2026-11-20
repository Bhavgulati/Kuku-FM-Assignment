# Build log: where the AI got it wrong

This is a running record of real mistakes made by the AI assistant (Claude Code, Claude Opus) while building this tool, kept so that the "one place where AI got it wrong" section of the solution doc is honest. Each entry says what went wrong, how it was caught, and what was fixed.

## During data profiling and planning

1. **Assumed one header style for every file.** The first profiling script matched `Scene start:` exactly. Episode 6 writes every field in upper case (`SCENE START:`, `ACTIONS AND DIALOGUE:`), so the script found zero scenes there and crashed (`IndexError`).
   *Caught by:* the crash. *Fix:* the parser matches field names case-insensitively, and a synthetic upper-case fixture plus a real-pack test lock this in.

2. **Wrong scene count in the approved plan.** The plan said the pack has 106 scenes. That number came from `grep '^SCENE '`, which also matched Episode 6's upper-case `SCENE HEADING:` / `SCENE START:` / `SCENE END:` lines. The real count is **100**.
   *Caught by:* the parser's totals not matching the plan. *Fix:* the real-pack test asserts 100 scenes and the exact block counts (1754 camera, 372 action, 1487 dialogue, 114 sound).

## During implementation

3. **Wrong expectations in the AI's own unit tests (the code was right).** The AI expected 6 camera blocks in a fixture that has 7. It also expected an end point to snap 1.0s forward to a shot boundary, although the snap rule it had written itself only moves cuts by up to 0.5s.
   *Caught by:* pytest. *Fix:* the tests were corrected; the code was not changed.

4. **Assumed every prompt in a run is unique.** The cache test assumed run 2's cache hits would equal run 1's API calls. But two baselines (B1 "episode ending" and B3 "rules only") can choose the same window, which produces an identical prompt and a cache hit inside run 1.
   *Caught by:* pytest. *Fix:* the test now checks `hits_run2 == calls_run1 + hits_run1`.

## During live integration (Gemini API)

5. **Cache key ignored provider settings.** The disk cache was keyed on model + prompts + temperature, but not on provider settings such as Gemini's `thinkingLevel`. Changing the thinking level would have silently returned stale cached answers.
   *Caught by:* code review while adding the thinking setting, before any affected result was used. *Fix:* the provider's `extra` settings are now part of the cache key.

6. **Misread a per-minute 429 as "daily quota exhausted".** The first rate-limit handler treated any 429 whose body contained "PerDay" as fatal. Gemini's error body can list several quota violations, so the run stopped when a wait would have been enough.
   *Caught by:* an immediate probe of the same model succeeded. *Fix:* the error is fatal only if *every* listed `quotaId` is per-day, and the quota IDs are now printed. The next stop really was `GenerateRequestsPerDayPerProjectPerModel-FreeTier`: this key allows gemini-3.8-flash only about 10–15 requests a day.

7. **Assumed the model always returns the requested JSON shape.** One judge call returned a bare list `[...]` instead of `{"ratings": [...]}`, and the code crashed with `'list' object has no attribute 'get'`.
   *Fix:* every stage accepts both shapes (`items_of`, `one_dict`).

8. **Overstated evidence in a config comment.** The AI wrote "cuts thinking tokens ~20x; picks were unchanged on Ep 1". The ~20x came from a trivial 1-line probe (101 → 5 output tokens), and "unchanged" compared against a *different* model.
   *Caught by:* self-review. *Fix:* the comment now states exactly what was measured.

9. **The blind judge was not actually blind.** The plan accepted that the judge would see names from speaker labels and stage directions, and noted it only as a limitation. The first live test showed this is worse than "optimistic": the judge asked *"Why is Juliana referred to as a maid in the dialogue, but as Lady Juliana in the stage directions?"* The speaker label gave away a disguise that the clip itself keeps hidden.
   *Fix:* one LLM call per show extracts personal names from the speaker labels. Code then masks every name the clip never speaks aloud (as "Character A/B…"), together with titles such as "Lady".

10. **Duplicate openings were judged against each other.** When a montage started on the same footage as a single clip, both openings landed in the same hook batch. The judge marked the second one down: *"Duplicate of an unengaging slow crying scene"*, and E01-P1's hook fell from 4 to 2.
    *Fix:* identical openings are judged once and share the score.

11. **Prompt tuning on n = 6 is not evidence.** After the smoke run, the proposer prompt was changed to "start ON the shock, not the build-up", a rule taken from the brief. The mean hook score on Eps 1–3 moved from 2.86 to 3.00, which is well inside the judge's test-retest noise (mean absolute difference 0.40). The change was kept because it follows the brief, but it is **not** claimed as an improvement.

12. **Spent the scarce quota before measuring it.** The plan assumed one free key would cover a full run of about 330 calls. Google does not publish per-model free-tier limits (they appear only in AI Studio), and the AI went ahead with model probes, a 3-model comparison and two smoke runs on the same per-model daily quota the real run needed. For this key that quota turned out to be about 20 requests per day for each Flash model. gemini-3.5-flash ran out after proposing 12 of the 50 episodes.
    *Effect:* the proposer was switched to gemini-3.5-flash-lite for all 50 episodes (the user's decision). The 12 Flash-proposed episodes are kept as a real "swap the model" comparison. *Lesson:* check the quota first (AI Studio → Rate limits), and run probes on a different model from the one the production run will use.

## After the first full run

13. **The comprehension grader confused "not mentioned" with "wrong".** The first full run reported 16% of promos as misunderstood. Reading the grader's notes showed that many of these flags were wrong:
    - *"Viewer introduces a character named Seamus not mentioned in the truth"*: the name is spoken in the clip, and the one-line episode summary simply leaves it out.
    - E17-P2 was marked *contradicted*, although the viewer's summary ("she admits she was frightened of losing him; a tender moment by the fire") matches the logline.
    - *"the wife is Juliana, not Character A"*: the grader treated a masked name as a different person.

    *Fix:* the grader prompt now states that a detail absent from the summary is not an error, and that "Character A/B" are hidden names to be matched by role. All runs were re-graded with the fixed prompt, and only the fixed grades are reported.

14. **A code edit made through a script corrupted a string literal.** Generating Python source inside a Python string turned the `\n` escapes into real line breaks, which caused a `SyntaxError`. *Caught by:* the import failing at once. *Fix:* the lines were rewritten by hand.

## Before publishing the repo

15. **A "show-agnostic" prompt used this show's own character names as examples.** The cast-name prompt taught the format with labels taken from this pack ("LADY JULIANA" → "Juliana", "LORD HARRINGTON" → "Harrington"). That contradicts the claim that the prompts carry nothing show-specific, and it would have put show content into a public repo.
    *Caught by:* a scan of the files about to be committed, before the first commit. *Fix:* the examples now use invented names. Re-running that one call (1 request) returned exactly the same 15 names, so the masking, and every cached judge result that depends on it, is unchanged.
