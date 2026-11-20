"""Mask character names the viewer has not heard, before the blind judge sees a promo.

A real viewer knows a character's name only if it is spoken aloud in the clip. Speaker
labels and stage directions are full of names (and titles that give secrets away, e.g. a
'LADY' disguised as a maid). Without masking, the judge would overrate how well a promo
stands on its own.

Names come from one cheap LLM call per show over the list of speaker labels (general for
any show). The masking itself is plain code."""
from __future__ import annotations

import re
import string

from .llm.base import LLMClient, LLMError, QuotaExhausted
from .parse import Episode
from .prompting import fill, load_prompt
from .timeline import ViewItem

TITLES = r"(?:LADY|LORD|SIR|DAME|DUKE|DUCHESS|PRINCE|PRINCESS|KING|QUEEN|COUNT|COUNTESS|BARON|BARONESS|EARL|" \
         r"MR|MRS|MS|MISS|DR|MASTER|MISTRESS)\.?"


def extract_names(client: LLMClient, eps: list[Episode]) -> list[str]:
    labels = sorted({b.speaker for e in eps for b in e.blocks if b.is_dialogue and b.speaker})
    if not labels:
        return []
    system, user_t = load_prompt("cast_names")
    try:
        res = client.complete_json(system, fill(user_t, LABELS="\n".join(labels)), tag="names:show", temperature=0.0)
    except QuotaExhausted:
        raise
    except LLMError as e:
        print(f"  ! name extraction failed ({e}); judge will see unmasked names")
        return []
    raw = res if isinstance(res, list) else (res.get("names") if isinstance(res, dict) else None) or []
    names = {str(n).strip() for n in raw if isinstance(n, str)}
    # keep only names that really occur in the labels (guards against invented names)
    label_text = " ".join(labels).upper()
    return sorted(n for n in names if len(n) >= 3 and re.search(rf"\b{re.escape(n.upper())}\b", label_text))


class Masker:
    """Per-promo masking: names spoken aloud in `heard_items` stay; all others become 'Character A/B/..'."""

    def __init__(self, names: list[str], heard_items: list[ViewItem]):
        spoken = " ".join(it.block.text for it in heard_items if it.block.is_dialogue)
        self.keep = {n.lower() for n in names if re.search(rf"\b{re.escape(n)}\b", spoken, re.I)}
        self.hidden = [n for n in sorted(names, key=len, reverse=True) if n.lower() not in self.keep]
        self.alias: dict[str, str] = {}
        if self.hidden:
            alt = "|".join(re.escape(n) for n in self.hidden)
            self.rx = re.compile(rf"(?:\b{TITLES}\s+)?\b({alt})\b", re.I)
        else:
            self.rx = None

    def _sub(self, m: re.Match) -> str:
        key = m.group(1).lower()
        if key not in self.alias:
            self.alias[key] = "Character " + string.ascii_uppercase[len(self.alias) % 26]
        return self.alias[key]

    def __call__(self, text: str) -> str:
        return self.rx.sub(self._sub, text) if self.rx else text
