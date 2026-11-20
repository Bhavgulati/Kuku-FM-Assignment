"""Prompt templates live in promofinder/prompts/*.md (editable without touching code).
Each file is '<system prompt>\n=== USER ===\n<user template>' with {{PLACEHOLDERS}}."""
from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    system, _, user = text.partition("=== USER ===")
    return system.strip(), user.strip()


def fill(template: str, **values) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def prompt_hashes() -> dict[str, str]:
    return {
        p.stem: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(PROMPT_DIR.glob("*.md"))
    }
