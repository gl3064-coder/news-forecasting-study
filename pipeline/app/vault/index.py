"""Parse the vault's atomic notes into two in-memory artifacts:

    vault_titles — compact list of {title, one_liner} for Haiku prompts
    vault_full   — full {title, body} for Sonnet prompts

Rebuilt only after a successful vault.sync.pull(); cached otherwise."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from . import sync as vault_sync


@dataclass(frozen=True)
class VaultTitle:
    title: str
    one_liner: str


@dataclass(frozen=True)
class VaultNote:
    title: str
    body: str


_lock = threading.Lock()
_titles: list[VaultTitle] = []
_full: list[VaultNote] = []
_built: bool = False


def notes_dir() -> Path:
    subpath = os.getenv("PULSE_VAULT_SUBPATH", "02 Notes")
    return vault_sync.vault_root() / subpath


def titles() -> list[VaultTitle]:
    return list(_titles)


def full() -> list[VaultNote]:
    return list(_full)


def is_built() -> bool:
    return _built


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_PREAMBLE_RE = re.compile(
    r"^##\s+For future Claude\s*\n+(.+?)(?:\n##|\Z)",
    re.DOTALL | re.MULTILINE,
)


def _parse_frontmatter(raw: str) -> tuple[bool, str]:
    """Returns (frontmatter_valid, body). If no frontmatter, returns (True, raw)
    so notes without YAML are still parsed. Bad YAML braces / unterminated
    lists short-circuit to (False, '')."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return True, raw
    fm_text = match.group(1)
    body = raw[match.end():]
    # Cheap validity check: every non-blank line must contain ':' or start
    # with whitespace+dash (YAML list item). Anything else means broken YAML.
    for line in fm_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("- ") or ":" in s:
            continue
        return False, ""
    # Also reject obvious truncation: unmatched '[' in any value
    if fm_text.count("[") != fm_text.count("]"):
        return False, ""
    return True, body


def _extract_one_liner(body: str) -> str:
    """Pulls the first paragraph of the 'For future Claude' preamble if
    present, otherwise the first non-empty non-heading line of the body.
    Returns '' if neither is available."""
    match = _PREAMBLE_RE.search(body)
    if match:
        para = match.group(1).strip()
        # Take just the first paragraph (split on blank line)
        first_para = para.split("\n\n")[0]
        return " ".join(first_para.split())
    # Fallback: first non-empty non-heading line
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped
    return ""


def rebuild() -> None:
    """Parse all .md files in notes_dir() into the in-memory caches. Skips
    malformed files with a log line; never raises."""
    global _built
    new_titles: list[VaultTitle] = []
    new_full: list[VaultNote] = []
    directory = notes_dir()
    if not directory.is_dir():
        with _lock:
            _titles.clear()
            _full.clear()
            _built = True
        return
    for path in sorted(directory.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[vault] skipped {path.name}: read failed ({exc})", flush=True)
            continue
        title = path.stem
        valid, body = _parse_frontmatter(raw)
        if not valid:
            print(f"[vault] skipped {path.name}: bad YAML frontmatter", flush=True)
            continue
        one_liner = _extract_one_liner(body)
        if not one_liner:
            print(f"[vault] skipped {path.name}: empty body", flush=True)
            continue
        new_titles.append(VaultTitle(title=title, one_liner=one_liner))
        new_full.append(VaultNote(title=title, body=body.strip()))
    with _lock:
        _titles.clear()
        _full.clear()
        _titles.extend(new_titles)
        _full.extend(new_full)
        _built = True
    print(f"[vault] index rebuilt: {len(new_titles)} notes loaded", flush=True)
