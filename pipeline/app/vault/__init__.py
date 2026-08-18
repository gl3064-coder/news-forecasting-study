"""Pulse vault integration — reads the user's Obsidian 02 Notes/ for LLM
context injection.

Module entry points:
    sync.pull()           — git clone or pull the vault repo
    index.rebuild()       — read 02 Notes/, parse, cache
    inject.for_haiku()    — compact title list for Haiku-tier prompts
    inject.for_sonnet()   — full atomic note bodies for Sonnet-tier prompts

Every function in this package degrades gracefully — when the vault is
unavailable, queries return empty strings and the LLM chain runs unchanged.
"""

from . import sync, index, inject  # re-export so callers can `from app import vault`

__all__ = ["sync", "index", "inject"]
