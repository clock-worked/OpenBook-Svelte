import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class KnowledgeStore:
    """Lightweight knowledge store backed by a per-book book.characters.json.

    This augments rule-based parsing with:
    - genders per character
    - alias mapping (alias -> canonical)
    - first_seen chapter index/name
    - dialogue and mention counts

    All fields are optional in the on-disk JSON and defaulted here.
    """

    def __init__(self, book_root_dir: str):
        self.book_root_dir = book_root_dir
        self.book_characters_path = os.path.join(book_root_dir, "book.characters.json")
        self.characters_path = os.path.join(book_root_dir, "characters.json")
        self._loaded = False
        self._read_only = False
        self._characters: Dict[str, Dict] = {}
        # Derived views
        self._genders: Dict[str, str] = {}
        self._aliases: Dict[str, str] = {}

    # ------------------------
    # Loading / Saving
    # ------------------------
    def load(self) -> None:
        source_path = None
        if os.path.exists(self.book_characters_path):
            source_path = self.book_characters_path
            self._read_only = False
        elif os.path.exists(self.characters_path):
            source_path = self.characters_path
            self._read_only = True

        if not source_path:
            # Nothing to load; keep empty knowledge
            self._loaded = True
            self._rebuild_views()
            return

        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        characters = data.get("characters", [])
        for entry in characters:
            name = entry.get("name")
            if not name:
                continue
            self._characters[name] = {
                "name": name,
                "gender": entry.get("gender", "u"),
                # Accept both "aliases" list or build-time empty
                "aliases": list(entry.get("aliases", [])),
                "first_seen_chapter": entry.get("first_seen_chapter"),
                # Support legacy field "count" if present
                "count_dialogue": entry.get("count_dialogue", entry.get("count", 0)),
                "count_mentions": entry.get("count_mentions", 0),
                # Preserve optional presentation fields
                "color": entry.get("color"),
                "voice": entry.get("voice"),
            }
        self._loaded = True
        self._rebuild_views()

    def save(self) -> None:
        if not self._loaded:
            return
        if self._read_only:
            return
        # Reconstruct characters array preserving unknown fields as best-effort
        out = {"characters": []}
        for name, entry in sorted(self._characters.items(), key=lambda kv: kv[0].lower()):
            out["characters"].append({
                "name": name,
                "gender": entry.get("gender", "u"),
                "aliases": entry.get("aliases", []),
                "first_seen_chapter": entry.get("first_seen_chapter"),
                "count_dialogue": entry.get("count_dialogue", 0),
                "count_mentions": entry.get("count_mentions", 0),
                # Retain color/voice if they existed originally by passing through
                **{k: v for k, v in entry.items() if k in ("color", "voice")},
            })
        with open(self.book_characters_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    def _rebuild_views(self) -> None:
        self._genders = {name: entry.get("gender", "u") for name, entry in self._characters.items()}
        # Build alias map to canonical name
        aliases: Dict[str, str] = {}
        canonical_by_lower: Dict[str, str] = {
            str(name).strip().lower(): name
            for name in self._characters.keys()
            if str(name).strip()
        }
        for canonical, entry in self._characters.items():
            for alias in entry.get("aliases", []) or []:
                if not alias:
                    continue
                alias_key = str(alias).strip().lower()
                if not alias_key:
                    continue
                colliding_canonical = canonical_by_lower.get(alias_key)
                if colliding_canonical and colliding_canonical != canonical:
                    continue
                if alias not in aliases:
                    aliases[alias] = canonical
        self._aliases = aliases

    # ------------------------
    # Accessors used by parser
    # ------------------------
    @property
    def genders(self) -> Dict[str, str]:
        return self._genders

    @property
    def aliases(self) -> Dict[str, str]:
        return self._aliases

    def ensure_character(self, name: str, *, gender: Optional[str] = None) -> None:
        if name not in self._characters:
            self._characters[name] = {
                "name": name,
                "gender": gender or "u",
                "aliases": [],
                "first_seen_chapter": None,
                "count_dialogue": 0,
                "count_mentions": 0,
            }
            self._rebuild_views()

    def add_alias(self, alias: str, canonical: str) -> None:
        if not alias or not canonical:
            return
        self.ensure_character(canonical)
        entry = self._characters[canonical]
        aliases = set(entry.get("aliases", []))
        if alias not in aliases and alias != canonical:
            aliases.add(alias)
            entry["aliases"] = sorted(aliases)
            self._rebuild_views()

    # ------------------------
    # Curation ingestion helpers
    # ------------------------
    def update_counts(self, name: str, *, dialogue_inc: int = 0, mention_inc: int = 0,
                      first_seen_chapter: Optional[str] = None) -> None:
        self.ensure_character(name)
        entry = self._characters[name]
        entry["count_dialogue"] = int(entry.get("count_dialogue", 0)) + dialogue_inc
        entry["count_mentions"] = int(entry.get("count_mentions", 0)) + mention_inc
        if first_seen_chapter and not entry.get("first_seen_chapter"):
            entry["first_seen_chapter"] = first_seen_chapter

    # ------------------------
    # Utilities
    # ------------------------
    @staticmethod
    def find_book_root_from_file(file_path: str) -> Optional[str]:
        """Find the nearest parent directory named like 'Book-X' for a given file path."""
        try:
            p = Path(file_path).resolve()
        except Exception:
            p = Path(file_path).absolute()
        # If input is a directory, include it in the search; else, search its parents
        for parent in ([p] if p.is_dir() else []) + list(p.parents):
            if parent.name.startswith("Book-"):
                return str(parent)
        return None


def load_knowledge_for_file(file_path: str) -> Optional[KnowledgeStore]:
    book_root = KnowledgeStore.find_book_root_from_file(file_path)
    if not book_root:
        return None
    ks = KnowledgeStore(book_root)
    ks.load()
    return ks


