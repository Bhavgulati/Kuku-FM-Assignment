"""Parse microdrama screenplay .txt files into structured episodes.

Format observed in the input pack (one file per episode):

    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. CATHEDRAL – DAY
    Scene start: 00:00.00
    Scene end: 02:18.00
    Actions and dialogue:
    **CAMERA:**
    Start: (00:00.00) | End: (00:04.80)
    <one line of text>
    ...
    --- SCRIPT END ---

Field names are matched case-insensitively (episode 6 of the pack writes them in
upper case). Anything unexpected becomes a warning, not a crash, so a new show
with small format drift still parses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Labels that are not a speaker. Everything else in **LABEL:** is dialogue.
NON_SPEAKER_KINDS = {
    "CAMERA": "CAMERA",
    "SHOT": "CAMERA",
    "ACTION": "ACTION",
    "SOUND FX": "SFX",
    "SFX": "SFX",
    "SOUND": "SFX",
    "MUSIC": "SFX",
    "TRANSITION": "CAMERA",
}
# Parenthetical speaker tags such as (O.S.), (V.O.), (CONT'D) - not emotions.
SPEAKER_TAG_RE = re.compile(r"^(O\.?S\.?|V\.?O\.?|O\.?C\.?|CONT'?D|CONTINUED|PRE-LAP|FILTERED|ON PHONE)$", re.I)

LABEL_RE = re.compile(r"^\*\*(.+?):?\*\*\s*$")
TIMES_RE = re.compile(
    r"^start:\s*\(?\s*([\d:.]+)\s*\)?\s*\|\s*end:\s*\(?\s*([\d:.]+)\s*\)?\s*$", re.I
)
SCENE_RE = re.compile(r"^scene\s+(\d+)\s*$", re.I)
SCENE_FIELD_RE = re.compile(r"^scene\s+(heading|start|end)\s*:\s*(.*)$", re.I)
HEADER_RE = re.compile(r"^(episode number|episode logline|continuity notes)\s*:\s*(.*)$", re.I)


def parse_time(s: str) -> float:
    """'01:05.60' -> 65.6 ; also accepts 'HH:MM:SS.ss' and plain seconds."""
    parts = s.strip().split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return round(total, 3)


def fmt_time(t: float) -> str:
    """65.6 -> '01:05.60' (same style as the source scripts)."""
    t = max(0.0, t)
    m = int(t // 60)
    s = t - m * 60
    return f"{m:02d}:{s:05.2f}"


@dataclass
class Block:
    id: str  # global, e.g. "E01-B045"
    local_id: str  # within episode, e.g. "B045"
    idx: int  # 0-based position in the file
    kind: str  # CAMERA | ACTION | SFX | DIALOGUE
    label: str  # raw label text
    speaker: str | None
    tags: list[str]
    emotion: str
    text: str
    start: float
    end: float
    scene: int
    line: int  # 1-based line number in source file

    @property
    def is_dialogue(self) -> bool:
        return self.kind == "DIALOGUE"

    def display_label(self) -> str:
        if self.kind == "DIALOGUE":
            extra = ", ".join([*self.tags, self.emotion] if self.emotion else self.tags)
            return f"{self.speaker} ({extra})" if extra else str(self.speaker)
        return {"CAMERA": "SHOT", "ACTION": "ACTION", "SFX": "SOUND"}.get(self.kind, self.kind)

    def display_text(self) -> str:
        return f'"{self.text}"' if self.kind == "DIALOGUE" else self.text


@dataclass
class Scene:
    number: int
    heading: str
    start: float | None
    end: float | None


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    file: str
    scenes: list[Scene] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def eid(self) -> str:
        return f"E{self.number:02d}"

    @property
    def duration(self) -> float:
        ends = [b.end for b in self.blocks] + [s.end for s in self.scenes if s.end is not None]
        return max(ends) if ends else 0.0

    def block(self, local_or_global_id: str) -> Block | None:
        key = local_or_global_id.strip().upper()
        for b in self.blocks:
            if b.local_id == key or b.id == key:
                return b
        return None


def _split_label(label: str) -> tuple[str | None, list[str], str]:
    """'LADY ELEANOR (V.O.) (Steely, Unstoppable)' -> ('LADY ELEANOR', ['V.O.'], 'Steely, Unstoppable')."""
    name = label.split("(", 1)[0].strip()
    tags, emotions = [], []
    for group in re.findall(r"\(([^)]*)\)", label):
        g = group.strip()
        if SPEAKER_TAG_RE.match(g):
            tags.append(g.upper())
        elif g:
            emotions.append(g)
    return name or None, tags, ", ".join(emotions)


def parse_episode(path: str | Path) -> Episode:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    warnings: list[str] = []
    header: dict[str, str] = {}
    scenes: list[Scene] = []
    blocks: list[Block] = []
    cur_scene = 0

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.strip()
        lineno = i + 1
        if not line:
            i += 1
            continue
        m = HEADER_RE.match(line)
        if m and not blocks:
            header[m.group(1).lower()] = m.group(2).strip()
            i += 1
            continue
        if line.startswith("---") or re.match(r"^actions and dialogue\s*:?$", line, re.I):
            i += 1
            continue
        m = SCENE_RE.match(line)
        if m:
            cur_scene = int(m.group(1))
            scenes.append(Scene(cur_scene, "", None, None))
            i += 1
            continue
        m = SCENE_FIELD_RE.match(line)
        if m:
            if not scenes:
                scenes.append(Scene(0, "", None, None))
            key, val = m.group(1).lower(), m.group(2).strip()
            try:
                if key == "heading":
                    scenes[-1].heading = val
                elif key == "start":
                    scenes[-1].start = parse_time(val)
                else:
                    scenes[-1].end = parse_time(val)
            except ValueError:
                warnings.append(f"{path.name}:{lineno}: bad scene {key} value {val!r}")
            i += 1
            continue
        m = LABEL_RE.match(line)
        if m:
            label = m.group(1).strip()
            # timestamps on the next non-empty line
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            tm = TIMES_RE.match(lines[j].strip()) if j < n else None
            if not tm:
                warnings.append(f"{path.name}:{lineno}: block {label!r} has no 'Start | End' line; skipped")
                i += 1
                continue
            try:
                start, end = parse_time(tm.group(1)), parse_time(tm.group(2))
            except ValueError:
                warnings.append(f"{path.name}:{j + 1}: unreadable timestamps; block skipped")
                i = j + 1
                continue
            # body: following lines until blank / next label / scene / script marker
            k = j + 1
            body: list[str] = []
            while k < n:
                s = lines[k].strip()
                if not s or LABEL_RE.match(s) or SCENE_RE.match(s) or s.startswith("---"):
                    break
                body.append(s)
                k += 1
            text = " ".join(body).strip()
            upper = label.upper()
            kind = NON_SPEAKER_KINDS.get(upper, "DIALOGUE")
            speaker, tags, emotion = (None, [], "")
            if kind == "DIALOGUE":
                speaker, tags, emotion = _split_label(label)
                if len(text) >= 2 and text[0] in "\"“" and text[-1] in "\"”":
                    text = text[1:-1].strip()
            if end < start:
                warnings.append(f"{path.name}:{j + 1}: end before start ({tm.group(1)} > {tm.group(2)}); swapped")
                start, end = end, start
            if not text:
                warnings.append(f"{path.name}:{lineno}: block {label!r} has empty text")
            idx = len(blocks)
            blocks.append(
                Block(
                    id="",  # filled once the episode number is known
                    local_id=f"B{idx + 1:03d}",
                    idx=idx,
                    kind=kind,
                    label=label,
                    speaker=speaker,
                    tags=tags,
                    emotion=emotion,
                    text=text,
                    start=start,
                    end=end,
                    scene=cur_scene,
                    line=lineno,
                )
            )
            i = k
            continue
        warnings.append(f"{path.name}:{lineno}: unrecognised line ignored: {line[:80]!r}")
        i += 1

    number = None
    m = re.search(r"(\d+)", header.get("episode number", ""))
    if m:
        number = int(m.group(1))
    else:
        m = re.search(r"episode[-_ ]?(\d+)", path.stem, re.I)
        if m:
            number = int(m.group(1))
            warnings.append(f"{path.name}: no EPISODE NUMBER header; took {number} from file name")
    if number is None:
        raise ValueError(f"{path}: cannot determine episode number")

    ep = Episode(
        number=number,
        title=header.get("episode number", f"Episode {number}"),
        logline=header.get("episode logline", ""),
        continuity=header.get("continuity notes", ""),
        file=path.name,
        scenes=scenes,
        blocks=blocks,
        warnings=warnings,
    )
    for b in blocks:
        b.id = f"{ep.eid}-{b.local_id}"
    if not blocks:
        warnings.append(f"{path.name}: no blocks found")
    return ep


def load_show(folder: str | Path, episodes: set[int] | None = None) -> list[Episode]:
    folder = Path(folder)
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"no .txt screenplay files in {folder}")
    eps = [parse_episode(f) for f in files]
    eps.sort(key=lambda e: e.number)
    seen: dict[int, str] = {}
    for e in eps:
        if e.number in seen:
            raise ValueError(f"episode {e.number} appears twice: {seen[e.number]} and {e.file}")
        seen[e.number] = e.file
    if episodes:
        eps = [e for e in eps if e.number in episodes]
    return eps


def parse_episode_range(spec: str | None) -> set[int] | None:
    """'1-3,7,10-12' -> {1,2,3,7,10,11,12}."""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out
