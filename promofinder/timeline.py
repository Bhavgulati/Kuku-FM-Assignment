"""Timeline maths: clean cut points, block-ID windows -> timestamps, viewer render.

The LLM only ever names block IDs. Everything time-related is computed here, so
every timestamp in the output is traceable to a line of the source script and a
cut can never fall in the middle of a line of dialogue.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .parse import Block, Episode, fmt_time

EPS = 0.05  # tolerance in seconds
SHOT_SNAP = 0.5  # move a cut onto a nearby shot boundary if within this many seconds


@dataclass
class Segment:
    episode: int
    file: str
    start_block: str  # global block id
    end_block: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def to_dict(self) -> dict:
        return {
            "episode": self.episode,
            "file": self.file,
            "start": fmt_time(self.start),
            "end": fmt_time(self.end),
            "start_s": round(self.start, 2),
            "end_s": round(self.end, 2),
            "duration_s": self.duration,
            "start_block": self.start_block,
            "end_block": self.end_block,
        }

    def label(self) -> str:
        return f"E{self.episode:02d} {fmt_time(self.start)}–{fmt_time(self.end)}"


@dataclass
class Promo:
    promo_id: str
    kind: str  # single | montage | baseline
    source: str  # llm | B1 | B2 | B3
    segments: list[Segment]
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    trope: str = "other"
    flags: list[str] = field(default_factory=list)
    repaired: bool = False
    hook_claim: str | None = None  # does the proposer's quoted hook line really open the clip? (validate.py)
    judge: dict = field(default_factory=dict)  # filled by judge.py
    scores: dict = field(default_factory=dict)  # filled by rank.py

    @property
    def duration(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode for s in self.segments})

    @property
    def depth(self) -> int:
        """Earliest episode a new viewer must reach to see this footage."""
        return max(self.episodes)

    def segments_label(self) -> str:
        return " | ".join(s.label() for s in self.segments)


def dialogue_in_progress(ep: Episode, t: float) -> Block | None:
    """Return a dialogue block that is being spoken at time t (strictly inside), if any."""
    for b in ep.blocks:
        if b.is_dialogue and b.start + EPS < t < b.end - EPS:
            return b
    return None


def shot_boundaries(ep: Episode) -> list[float]:
    pts = {0.0, ep.duration}
    for b in ep.blocks:
        if b.kind == "CAMERA":
            pts.add(b.start)
            pts.add(b.end)
    return sorted(pts)


def clean_cuts(ep: Episode) -> list[float]:
    """All block boundaries where no dialogue line is mid-utterance."""
    pts = {0.0, ep.duration}
    for b in ep.blocks:
        pts.add(b.start)
        pts.add(b.end)
    return sorted(p for p in pts if dialogue_in_progress(ep, p) is None)


def _snap(ep: Episode, t: float, direction: int) -> float:
    """Move t so it does not split a dialogue line (direction -1 = earlier, +1 = later),
    then onto a shot boundary if one is within SHOT_SNAP in the same direction."""
    for _ in range(50):
        b = dialogue_in_progress(ep, t)
        if b is None:
            break
        t = b.start if direction < 0 else b.end
    shots = shot_boundaries(ep)
    if direction < 0:
        near = [s for s in shots if t - SHOT_SNAP - EPS <= s <= t + EPS and dialogue_in_progress(ep, s) is None]
        if near:
            t = max(near)
    else:
        near = [s for s in shots if t - EPS <= s <= t + SHOT_SNAP + EPS and dialogue_in_progress(ep, s) is None]
        if near:
            t = min(near)
    return round(min(max(t, 0.0), ep.duration), 3)


def make_segment(ep: Episode, start_block_id: str, end_block_id: str) -> Segment:
    """Block IDs -> snapped segment. Raises ValueError with a message the LLM can act on."""
    sb, eb = ep.block(start_block_id), ep.block(end_block_id)
    missing = [x for x, b in ((start_block_id, sb), (end_block_id, eb)) if b is None]
    if missing:
        raise ValueError(f"block id(s) {', '.join(missing)} do not exist in episode {ep.number}")
    start = _snap(ep, sb.start, -1)
    end = _snap(ep, eb.end, +1)
    if end - start < EPS:
        raise ValueError(
            f"end block {eb.local_id} ({fmt_time(eb.end)}) is not after start block {sb.local_id} ({fmt_time(sb.start)})"
        )
    return Segment(ep.number, ep.file, sb.id, eb.id, start, end)


def segment_from_times(ep: Episode, start: float, end: float) -> Segment:
    """Used by the code-only baselines: arbitrary times -> snapped segment with block ids."""
    start = _snap(ep, start, -1)
    end = _snap(ep, end, +1)
    inside = [b for b in ep.blocks if b.start >= start - EPS and b.end <= end + EPS]
    if not inside:
        inside = [b for b in ep.blocks if b.end > start and b.start < end]
    sb = min(inside, key=lambda b: (b.start, b.idx))
    eb = max(inside, key=lambda b: (b.end, b.idx))
    return Segment(ep.number, ep.file, sb.id, eb.id, start, end)


@dataclass
class ViewItem:
    rel_start: float  # seconds from start of promo
    rel_end: float
    block: Block
    segment_index: int


def view_items(promo: Promo, eps_by_num: dict[int, Episode]) -> list[ViewItem]:
    """What the viewer experiences, in promo time. Blocks that only partly overlap a
    segment (a shot already running when the clip starts) are clipped to it."""
    items: list[ViewItem] = []
    offset = 0.0
    for si, seg in enumerate(promo.segments):
        ep = eps_by_num[seg.episode]
        for b in sorted(ep.blocks, key=lambda b: (b.start, b.idx)):
            ov_start, ov_end = max(b.start, seg.start), min(b.end, seg.end)
            fully_inside = b.start >= seg.start - EPS and b.end <= seg.end + EPS
            if ov_end - ov_start <= 0.1 and not fully_inside:
                continue
            items.append(
                ViewItem(
                    round(offset + ov_start - seg.start, 2),
                    round(offset + ov_end - seg.start, 2),
                    b,
                    si,
                )
            )
        offset += seg.duration
    return items


def _fmt_rel(t: float) -> str:
    return f"{t:04.1f}"


def render(items: list[ViewItem], with_ids: bool = False, mask=None) -> str:
    """Plain-text 'what is shown and said' render used by the blind judge and the report.
    `mask` (names.Masker) hides names a viewer has not heard: applied to speaker labels and
    stage directions, never to spoken lines."""
    out: list[str] = []
    last_seg = None
    for it in items:
        if last_seg is not None and it.segment_index != last_seg:
            out.append("--- CUT TO A DIFFERENT MOMENT ---")
        last_seg = it.segment_index
        prefix = f"[{_fmt_rel(it.rel_start)}–{_fmt_rel(it.rel_end)}s]"
        if with_ids:
            prefix += f" {it.block.id}"
        label, text = it.block.display_label(), it.block.display_text()
        if mask is not None:
            label = mask(label)
            if not it.block.is_dialogue:
                text = mask(text)
        out.append(f"{prefix} {label}: {text}")
    return "\n".join(out)


def window(items: list[ViewItem], t0: float, t1: float) -> list[ViewItem]:
    """Items overlapping [t0, t1) in promo time."""
    return [it for it in items if it.rel_end > t0 + EPS and it.rel_start < t1 - EPS]


def first_seconds(promo: Promo, eps_by_num: dict[int, Episode], seconds: float = 3.0) -> str:
    return render(window(view_items(promo, eps_by_num), 0.0, seconds))


def last_seconds(promo: Promo, eps_by_num: dict[int, Episode], seconds: float = 5.0) -> str:
    d = promo.duration
    return render(window(view_items(promo, eps_by_num), d - seconds, d + 1))


def episode_listing(ep: Episode, include_camera: bool = True) -> str:
    """Compact listing for the proposer: one block per line, local ids and episode times."""
    lines: list[str] = []
    scene = None
    heads = {s.number: s.heading for s in ep.scenes}
    for b in ep.blocks:
        if b.scene != scene:
            scene = b.scene
            lines.append(f"SCENE {scene:02d} — {heads.get(scene, '')}".rstrip(" —"))
        if b.kind == "CAMERA" and not include_camera:
            continue
        lines.append(f"{b.local_id} {fmt_time(b.start)}–{fmt_time(b.end)} {b.display_label()}: {b.display_text()}")
    return "\n".join(lines)
