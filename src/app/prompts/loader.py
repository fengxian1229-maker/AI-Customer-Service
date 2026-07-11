from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Formatter


PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Prompt file not found: {filename}") from exc
    if not prompt:
        raise ValueError(f"Prompt file is empty: {filename}")
    return prompt


def render_prompt(filename: str, **values: str) -> str:
    template = load_prompt(filename)
    required = {
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name is not None
    }
    supplied = set(values)
    missing = required - supplied
    unexpected = supplied - required
    if missing:
        raise ValueError(f"Prompt {filename} missing values: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"Prompt {filename} received unexpected values: {', '.join(sorted(unexpected))}")
    return template.format(**values)
