#!/usr/bin/env python3
"""
Chapter scanning and management service.
Provides functionality to list and analyze chapters from the filesystem.
"""

import os
import json
from typing import List, Dict, Optional


class ChapterInfo:
    """Information about a single chapter."""
    def __init__(
        self,
        name: str,
        path: str,
        parsed: bool = False,
        script_path: Optional[str] = None,
        audio: bool = False,
        reviewed: bool = False,
    ):
        self.name = name
        self.path = path
        self.parsed = parsed
        self.script_path = script_path
        self.audio = audio
        self.reviewed = reviewed
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "path": self.path,
            "parsed": self.parsed,
            "scriptPath": self.script_path,
            "audio": self.audio,
            "reviewed": self.reviewed,
        }


def chapter_has_audio(chapter_dir_path: str) -> bool:
    """Return True when a chapter's audio_lines directory contains generated audio."""
    audio_lines_path = os.path.join(chapter_dir_path, "audio_lines")
    if not os.path.isdir(audio_lines_path):
        return False

    audio_extensions = (".wav", ".mp3", ".m4a", ".flac", ".ogg")

    try:
        for character_entry in os.scandir(audio_lines_path):
            if not character_entry.is_dir() or character_entry.name.startswith('.'):
                continue

            character_dir_path = character_entry.path
            manifest_path = os.path.join(character_dir_path, "manifest.json")

            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as handle:
                        manifest = json.load(handle)

                    clips = manifest.get("clips") if isinstance(manifest, dict) else None
                    if isinstance(clips, list) and len(clips) > 0:
                        return True

                    metadata = manifest.get("metadata") if isinstance(manifest, dict) else None
                    total_clips = metadata.get("totalClips") if isinstance(metadata, dict) else None
                    if isinstance(total_clips, int) and total_clips > 0:
                        return True
                except (json.JSONDecodeError, OSError, TypeError, ValueError):
                    return True

            for clip_entry in os.scandir(character_dir_path):
                if clip_entry.is_file() and clip_entry.name.lower().endswith(audio_extensions):
                    return True
    except OSError:
        return False

    return False


def list_chapters(book_root_path: str) -> List[ChapterInfo]:
    """
    Scans the book root directory and returns a list of chapters.
    
    Args:
        book_root_path: Absolute path to the book's root directory
        
    Returns:
        List of ChapterInfo objects
        
    Raises:
        ValueError: If the path is invalid or not a directory
    """
    if not book_root_path:
        raise ValueError("Book root path not set")
    
    if not os.path.isdir(book_root_path):
        raise ValueError(f"Path is not a valid directory: {book_root_path}")
    
    chapters = []
    
    for entry in os.listdir(book_root_path):
        full_path = os.path.join(book_root_path, entry)
        
        # Skip non-directories and hidden files
        if not os.path.isdir(full_path) or entry.startswith('.'):
            continue
        
        # Check if directory contains chapter.txt file
        chapter_txt_path = os.path.join(full_path, 'chapter.txt')
        if not os.path.isfile(chapter_txt_path):
            continue
        
        # Use directory name as chapter title
        title = entry
        
        parsed = False
        reviewed = False
        script_path = None
        
        # Check if chapter has been parsed (has dialogue.json or script.json)
        # Check for v2.0 dialogue.json first
        dialogue_path = os.path.join(full_path, 'dialogue.json')
        if os.path.isfile(dialogue_path):
            parsed = True
            script_path = f"{title}/dialogue.json"
            try:
                with open(dialogue_path, "r", encoding="utf-8") as handle:
                    reviewed = bool(json.load(handle).get("reviewed"))
            except (json.JSONDecodeError, OSError, TypeError):
                reviewed = False
        
        chapters.append(ChapterInfo(
            name=title,
            path=f"{title}/chapter.txt",
            parsed=parsed,
            script_path=script_path,
            audio=chapter_has_audio(full_path),
            reviewed=reviewed,
        ))
    
    # Sort chapters by name
    chapters.sort(key=lambda x: x.name)
    
    return chapters


def get_chapter_stats(chapters: List[ChapterInfo]) -> Dict:
    """
    Calculate statistics about the chapters.
    
    Args:
        chapters: List of ChapterInfo objects
        
    Returns:
        Dictionary with statistics
    """
    total = len(chapters)
    parsed = sum(1 for ch in chapters if ch.parsed)
    
    return {
        "total": total,
        "parsed": parsed,
        "unparsed": total - parsed
    }

