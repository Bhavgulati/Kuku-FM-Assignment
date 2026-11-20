"""Rules-only promo picker (no AI). Used as baseline B3: how far do simple text cues get?

Hook cues: violent / shocking action and sound words, intense delivery tags, '!' lines.
End cues: a question, a threat-like trailing line, an explicit 'cliffhanger'/'sting' cue,
or the episode's own ending (episodes are written to end on cliffhangers)."""
from __future__ import annotations

import re

from .parse import Block, Episode
from .timeline import EPS, Segment, clean_cuts, segment_from_times

HOOK_WORDS = re.compile(
    r"\b(slap\w*|strik\w*|struck|punch\w*|crack\w*|scream\w*|shatter\w*|explo\w*|blood\w*|slam\w*|grab\w*|seiz\w*|"
    r"knife|sword|blade|fire|flames?|ablaze|collaps\w*|kneel\w*|kiss\w*|rip\w*|throw\w*|shov\w*|chok\w*|poison\w*|"
    r"dead|die|kill\w*|gun|whip\w*|burn\w*|chain\w*|drag\w*|hang\w*|arrest\w*|reveal\w*|gasp\w*)\b",
    re.I,
)
INTENSE = re.compile(
    r"\b(furious|scream\w*|shock\w*|horrif\w*|venom\w*|cruel|threat\w*|desperat\w*|hyster\w*|outrag\w*|gloat\w*|"
    r"mock\w*|sneer\w*|menac\w*|enrag\w*|terrif\w*|devastat\w*|vicious|savage|livid|panic\w*|accus\w*|smug|"
    r"defiant|commanding|deadly|icy|cold)\b",
    re.I,
)
END_CUES = re.compile(r"\b(cliffhanger|sting|ominous|suspense|cut to black|freeze)\b", re.I)


def intensity(b: Block) -> float:
    s = 0.0
    if b.is_dialogue:
        s += 1.0 if "!" in b.text else 0.0
        s += 0.5 if "?" in b.text else 0.0
        s += min(2.0, 0.75 * len(INTENSE.findall(b.emotion)))
    else:
        w = 2.0 if b.kind in ("ACTION", "SFX") else 1.0
        s += min(2.0 * w, w * len(HOOK_WORDS.findall(b.text)))
    return s


def end_score(ep: Episode, t: float) -> float:
    """How cliffhanger-like is cutting at time t? Looks at what ends just before t."""
    s = 0.0
    recent = [b for b in ep.blocks if t - 4.0 <= b.end <= t + EPS]
    lines = [b for b in recent if b.is_dialogue]
    if lines:
        last = max(lines, key=lambda b: b.end)
        if last.text.rstrip().endswith("?"):
            s += 1.5
        if last.text.rstrip().endswith(("...", "—", "-", "!")):
            s += 1.0
        s += min(1.5, 0.75 * len(INTENSE.findall(last.emotion)))
    if any(END_CUES.search(b.text) for b in recent if not b.is_dialogue):
        s += 2.0
    if abs(t - ep.duration) <= EPS:
        s += 1.5
    return s


def best_window(ep: Episode, hook_s: float = 3.0, min_len: float = 25.0, max_len: float = 50.0) -> Segment | None:
    cuts = clean_cuts(ep)
    ends = {e: end_score(ep, e) for e in cuts}
    best, best_score = None, -1.0
    for s in cuts:
        hook = sum(intensity(b) for b in ep.blocks if s - EPS <= b.start < s + hook_s)
        if hook <= 0:
            continue
        for e in cuts:
            if not (s + min_len <= e <= s + max_len):
                continue
            score = hook + ends[e]
            if score > best_score:
                best, best_score = (s, e), score
    if best is None:
        return None
    return segment_from_times(ep, *best)
