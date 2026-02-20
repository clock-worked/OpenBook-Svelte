# pylint: disable=relative-beyond-top-level
"""Hybrid rule-based + coreference-aware dialogue parser converting raw novel text
into structured speaker-attributed lines.
"""

import re
import os
import sys
import json
from copy import deepcopy

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

from fastcoref import FCoref
from .knowledge_store import load_knowledge_for_file
try:
    from .models.character import Character
except ImportError:
    from models.character import Character

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DEFAULT_SPEECH_VERBS_PATH = os.path.join(DATA_DIR, "speech_verbs.txt")
DEFAULT_REACTION_VERBS_PATH = os.path.join(DATA_DIR, "reaction_verbs.txt")

DEFAULT_PARSER_OPTIONS = {
    "pov_mode": "first_person",
    "protagonists": [],
    "learn_verbs": False,
    "heuristics": {
        "protagonist_first_person_tag": True,
        "narrator_identity": True,
        "coreference": True,
        "explicit_tags": True,
        "tag_continuation": True,
        "contiguous_dialogue": True,
        "carry_across_short_narration": True,
        "suggest_alternatives": True,
        "first_person_override": True,
        "narrator_fallback": True,
        "vocative_guard": True,
    },
}
NARRATOR_PERSONA = "Narrator"
DEFAULT_BLOCKED_SPEAKERS = {"he", "she", "as"}


def _normalize_speaker_token(value):
    return str(value or "").strip().lower()


class CharacterManager:
    """ Manages character aliases and names. """
    def __init__(self, known_genders=None, known_aliases=None):
        self.genders = (known_genders or {}).copy()
        self.aliases = (known_aliases or {}).copy()
        self.sorted_aliases = sorted(self.aliases.keys(), key=len, reverse=True)
        self.non_narrator_names = [n for n in self.genders.keys() if n != NARRATOR_PERSONA]
        self.non_narrator_regex = "|".join(
            r"\b" + re.escape(n) + r"\b" for n in self.non_narrator_names
        )

    def reload_views(self):
        """Rebuild cached alias/name views and regex strings."""
        self.sorted_aliases = sorted(self.aliases.keys(), key=len, reverse=True)
        self.non_narrator_names = [n for n in self.genders.keys() if n != NARRATOR_PERSONA]
        self.non_narrator_regex = "|".join(
            r"\b" + re.escape(n) + r"\b" for n in self.non_narrator_names
        )

    def resolve_alias(self, subject):
        """Resolve a subject string to its canonical name when possible."""
        cleaned_subject = subject.strip(".,'\"-?! ")
        for alias in self.sorted_aliases:
            if re.fullmatch(alias, cleaned_subject, re.IGNORECASE):
                return self.aliases[alias]
        for name in self.genders.keys():
            if name.lower() == cleaned_subject.lower():
                return name
        return cleaned_subject

    def get_all_subjects_regex(self):
        """Return a regex alternation for all known subjects and aliases."""
        all_subjects = list(set(list(self.genders.keys()) + list(self.aliases.keys())))
        all_subjects = [s for s in all_subjects if s]
        if not all_subjects:
            return "(?!x)x"
        sorted_subjects = sorted(all_subjects, key=len, reverse=True)
        return "|".join(re.escape(s) for s in sorted_subjects)

    def ensure_name(self, name, gender="u"):
        """Ensure a subject exists, returning True if it was newly added."""
        if not name:
            return False
        if name not in self.genders:
            self.genders[name] = gender
            self.reload_views()
            return True
        return False

    def is_known_subject(self, name):
        """Return True if the name is known as a subject or alias."""
        if not name:
            return False
        if name in self.genders:
            return True
        if name in self.aliases:
            return True
        if name in self.aliases.values():
            return True
        return False


class DialogueLine:
    """ Represents a single line of dialogue or narration in the script. """
    def __init__(
        self,
        text,
        speaker,
        line_type="narration",
        is_suggestion=False,
        suggestions=None,
        span_start=None,
        span_end=None,
    ):
        self.speaker = speaker
        self.line_type = line_type
        self.is_suggestion = is_suggestion
        self.suggestions = suggestions or []
        # Absolute character offsets into the original raw text (start inclusive, end exclusive)
        self.span_start = span_start
        self.span_end = span_end
        # Convert text string to a list of segments for finer control
        self.segments = self._text_to_segments(text)

    @property
    def text(self):
        """ Reconstructs the text string from segments. """
        return self._segments_to_text(self.segments)

    def _text_to_segments(self, text_string):
        """Converts a string with [pause] tags into a list of segments."""
        if not text_string:
            return []
        segments = []
        # Regex to find all pause tags and keep the delimiters
        pause_regex = re.compile(r'(\[pause(?: (?:short|long))?\])')
        parts = pause_regex.split(text_string)
        for part in parts:
            if not part:
                continue
            match = pause_regex.fullmatch(part)
            if match:
                duration = "medium" # Default for [pause]
                if "short" in part:
                    duration = "short"
                elif "long" in part:
                    duration = "long"
                segments.append({"type": "pause", "duration": duration})
            else:
                segments.append({"type": "text", "value": part})
        return segments

    def _segments_to_text(self, segments):
        """Converts a list of segments back into a string with [pause] tags."""
        text = ""
        for segment in segments:
            if segment['type'] == 'text':
                text += segment['value']
            elif segment['type'] == 'pause':
                if segment['duration'] == 'short':
                    text += '[pause short]'
                elif segment['duration'] == 'long':
                    text += '[pause long]'
                else: # medium
                    text += '[pause]'
        return text

    def to_dict(self):
        """Serialize the dialogue line into a JSON-friendly dictionary."""
        return {
            'segments': self.segments,
            'speaker': self.speaker,
            'line_type': self.line_type,
            'is_suggestion': self.is_suggestion,
            'suggestions': self.suggestions,
            'span_start': self.span_start,
            'span_end': self.span_end,
        }

    @staticmethod
    def from_dict(data):
        """Deserialize a dialogue line from a dictionary payload."""
        # For backward compatibility with old script files that used 'text'
        if 'text' in data:
            line = DialogueLine(
                text=data.get('text', ''),
                speaker=data.get('speaker', ''),
                line_type=data.get('line_type', 'narration'),
                is_suggestion=data.get('is_suggestion', False),
                suggestions=data.get('suggestions', []),
                span_start=data.get('span_start'),
                span_end=data.get('span_end'),
            )
        else:
            line = DialogueLine(
                text="", # Provide empty text, segments will be set directly
                speaker=data.get('speaker', ''),
                line_type=data.get('line_type', 'narration'),
                is_suggestion=data.get('is_suggestion', False),
                suggestions=data.get('suggestions', []),
                span_start=data.get('span_start'),
                span_end=data.get('span_end'),
            )
            line.segments = data.get('segments', [])
        return line


# Utility functions for script serialization

def script_to_dict_list(script_lines):
    """Serialize a list of DialogueLine objects into dictionaries."""
    return [line.to_dict() for line in script_lines]

def script_from_dict_list(dict_list):
    """Deserialize a list of dictionaries into DialogueLine objects."""
    return [DialogueLine.from_dict(d) for d in dict_list]


class HybridDialogueParser:
    """ The core parser engine. """
    def __init__(self, char_manager, narrator_persona=NARRATOR_PERSONA, protagonists=None,
                 options=None, direct_speech_verbs=None, reaction_verbs=None,
                 speech_verbs_path=None):
        self.char_manager = char_manager
        self.narrator_persona = narrator_persona
        self.protagonists = [p for p in (protagonists or []) if p]
        self.primary_protagonist = self.protagonists[0] if self.protagonists else None
        self.context_stack = []
        self.coref_map = {}
        self.direct_speech_verbs = [v.lower() for v in (direct_speech_verbs or []) if v]
        self.reaction_verbs = [v.lower() for v in (reaction_verbs or []) if v]
        self.direct_speech_verbs_regex = self._verbs_to_regex(self.direct_speech_verbs)
        self.all_verbs_regex = self._verbs_to_regex(self.direct_speech_verbs + self.reaction_verbs)
        self.subjects_regex_str = self.char_manager.get_all_subjects_regex()
        self.blocked_speakers = set(DEFAULT_BLOCKED_SPEAKERS)
        self.options = normalize_parser_options(options or {})
        self.heuristics = self.options.get("heuristics", {})
        self.pov_mode = self.options.get("pov_mode", "first_person")
        self.learn_verbs = bool(self.options.get("learn_verbs", False))
        self.speech_verbs_path = speech_verbs_path
        # Rule instrumentation
        self.rule_hits = {}

    def _verbs_to_regex(self, verbs):
        if not verbs:
            return "(?!x)x"
        return "|".join(re.escape(v) for v in verbs)

    def _ensure_subject(self, name):
        if _normalize_speaker_token(name) in self.blocked_speakers:
            return name
        added = self.char_manager.ensure_name(name)
        if added:
            self.subjects_regex_str = self.char_manager.get_all_subjects_regex()
        return name

    def _heuristic_enabled(self, name):
        return bool(self.heuristics.get(name, True))

    def _is_first_person_text(self, text):
        return re.search(r"\b(I|I'm|I’ve|I'd|me|my|mine)\b", text, re.I)

    def _update_context(self, speaker):
        if speaker and speaker not in ["Unassigned", "Unknown"] and not speaker.startswith('['):
            if not self.context_stack or speaker != self.context_stack[0]:
                self.context_stack.insert(0, speaker)
                if len(self.context_stack) > 5:
                    self.context_stack.pop()

    def parse(self, text, coref_map):
        """Parse raw text into DialogueLine objects with speaker attribution."""
        self.coref_map = coref_map
        self.context_stack = []
        # Keep the original text intact for span calculations
        raw_text = text

        chunks = []
        current_pos = 0
        # Split on any quoted span using straight or curly quotes
        raw_chunks = re.split(r'(["“”].*?["“”])', raw_text)
        for chunk_text in raw_chunks:
            if not chunk_text.strip():
                current_pos += len(chunk_text)
                continue
            chunk_type = "dialogue" if chunk_text.startswith(('"', '“', '”')) else "narration"
            start_index = raw_text.find(chunk_text, current_pos)
            # Keep both the original text (including quotes/spaces) and the trimmed
            # text used downstream
            chunks.append({
                "type": chunk_type,
                "orig_text": chunk_text,
                "text": chunk_text.strip('"“” '),
                "speaker": "Unassigned",
                "start_index": start_index
            })
            current_pos = start_index + len(chunk_text)

        for i, chunk in enumerate(chunks):
            if chunk["type"] == "narration":
                # Scene/paragraph break handling: reset carryover across blank lines
                # or scene markers.
                orig_text = chunk.get("orig_text", chunk["text"])
                has_blank_line = re.search(r"\n\s*\n", orig_text)
                has_scene_marker = re.search(
                    r"^(?:[-*\u2014\u2013]\s*){3,}$",
                    chunk["text"].strip(),
                    re.M,
                )
                if has_blank_line or has_scene_marker:
                    self.context_stack = []
                self._discover_subjects_from_tag(chunk.get('text', ''))
                for match in re.finditer(fr'\b({self.subjects_regex_str})\b', chunk['text'], re.I):
                    self._update_context(self.char_manager.resolve_alias(match.group(1)))
                continue

            if chunk["type"] == "dialogue":
                # Run full inference passes
                speaker = None
                if self.pov_mode == "first_person" and self._heuristic_enabled(
                    "protagonist_first_person_tag"
                ):
                    speaker = speaker or self._rule(
                        "0p_protagonist_first_person",
                        self._pass_0p_protagonist_first_person(i, chunks),
                    )
                if self._heuristic_enabled("narrator_identity"):
                    speaker = speaker or self._rule(
                        "0a_narrator_identity",
                        self._pass_0a_narrator_identity(i, chunks),
                    )
                speaker = speaker or self._rule(
                    "0b_narrator_vocative",
                    self._pass_0b_narrator_vocative(i, chunks),
                )
                if self._heuristic_enabled("coreference"):
                    speaker = speaker or self._rule(
                        "1_coreference",
                        self._pass_1_coreference(i, chunks),
                    )
                    speaker = speaker or self._rule(
                        "1b_backward_coreference",
                        self._pass_1b_backward_coreference(i, chunks),
                    )
                if self._heuristic_enabled("explicit_tags"):
                    speaker = speaker or self._rule(
                        "2_forward_explicit_tags",
                        self._pass_2_forward_explicit_tags(i, chunks),
                    )
                    speaker = speaker or self._rule(
                        "2b_backward_explicit_tags",
                        self._pass_2b_backward_explicit_tags(i, chunks),
                    )
                if self._heuristic_enabled("tag_continuation"):
                    speaker = speaker or self._rule(
                        "4b_tag_continuation",
                        self._pass_4b_tag_continuation(i, chunks),
                    )
                if self._heuristic_enabled("contiguous_dialogue"):
                    speaker = speaker or self._rule(
                        "4_contiguous_dialogue",
                        self._pass_4_contiguous_dialogue(i, chunks),
                    )
                if self._heuristic_enabled("carry_across_short_narration"):
                    speaker = speaker or self._rule(
                        "4c_carry_across_short_narration",
                        self._pass_4c_carry_across_short_narration(i, chunks),
                    )
                if self._heuristic_enabled("suggest_alternatives"):
                    speaker = speaker or self._rule(
                        "5_suggest_alternatives",
                        self._pass_5_suggest_alternatives(i, chunks),
                    )

                # Disambiguation rule: dialogue should never be attributed to Narrator or Unknown
                final_speaker = speaker or "Unknown"
                if (
                    isinstance(final_speaker, str)
                    and _normalize_speaker_token(final_speaker) in self.blocked_speakers
                ):
                    final_speaker = "Unknown"
                # Heuristic for first-person narrative: if the dialogue text contains
                # first-person pronouns, attribute to protagonist regardless of tags.
                # This preserves the full system but enforces first-person POV.
                if self.pov_mode == "first_person" and self._heuristic_enabled(
                    "first_person_override"
                ):
                    if self._is_first_person_text(chunk['text']) and self.primary_protagonist:
                        if final_speaker in [self.narrator_persona, "Unknown", None]:
                            final_speaker = self.primary_protagonist
                if self.pov_mode == "first_person" and self._heuristic_enabled("narrator_fallback"):
                    if isinstance(final_speaker, str) and not final_speaker.startswith('['):
                        if (
                            final_speaker in [self.narrator_persona, "Unknown"]
                            and self.primary_protagonist
                        ):
                            final_speaker = self.primary_protagonist

                # Vocative handling: if line addresses a name, avoid attributing to
                # that addressee.
                if self._heuristic_enabled("vocative_guard"):
                    voc_match = re.match(fr'^\s*({self.subjects_regex_str})\s*,', chunk['text'])
                    if voc_match:
                        addressed = self.char_manager.resolve_alias(voc_match.group(1))
                        if isinstance(final_speaker, str) and final_speaker == addressed:
                            prev = self._pass_4_contiguous_dialogue(i, chunks)
                            final_speaker = prev or final_speaker

                chunk["speaker"] = final_speaker
                if final_speaker and not str(final_speaker).startswith('['):
                    self._update_context(final_speaker)

        return self._format_output_as_objects(chunks)

    # Helper methods
    def _get_forward_tag(self, index, chunks):
        if (index + 1) < len(chunks) and chunks[index + 1]["type"] == "narration":
            return chunks[index + 1]
        return None
    
    def _get_backward_tag(self, index, chunks):
        if index > 0 and chunks[index - 1]["type"] == "narration":
            return chunks[index - 1]
        return None

    # High-confidence passes
    def _rule(self, name, value):
        if value:
            self.rule_hits[name] = self.rule_hits.get(name, 0) + 1
        return value

    def _pass_0p_protagonist_first_person(self, index, chunks):
        if not self.primary_protagonist:
            return None
        tag = self._get_forward_tag(index, chunks) or self._get_backward_tag(index, chunks)
        if not tag:
            return None
        # Only accept if immediately adjacent (no extra dialogue between) and not
        # contradicted by explicit tag.
        if re.match(r'^\s*I\s+(?:' + self.direct_speech_verbs_regex + r')\b', tag['text'], re.I):
            # Contradiction: explicit named subject different than protagonist in same tag
            named = re.search(
                fr'\b({self.subjects_regex_str})\b\s+'
                fr'({self.direct_speech_verbs_regex})\b',
                tag['text'],
                re.I,
            )
            if named:
                subject = self.char_manager.resolve_alias(named.group(1))
                if subject and subject.lower() != self.primary_protagonist.lower():
                    return None
            return self.primary_protagonist
        return None
    def _pass_0a_narrator_identity(self, index, chunks):
        forward_tag = self._get_forward_tag(index, chunks)
        if forward_tag:
            tag_text = forward_tag['text'].lstrip(", ")
            if re.match(fr'^(I|we)\s+({self.all_verbs_regex})\b', tag_text, re.I):
                return self.narrator_persona
        return None

    def _pass_0b_narrator_vocative(self, _index, _chunks):
        return None

    def _pass_1_coreference(self, index, chunks):
        forward_tag = self._get_forward_tag(index, chunks)
        if forward_tag:
            tag_text = forward_tag['text'].lstrip(', ')
            match = re.match(
                fr'\b(he|she|they)\b\s+'
                fr'({self.direct_speech_verbs_regex})\b',
                tag_text,
                re.I,
            )
            if match:
                start_pos = match.start(1)
                abs_pos = (
                    forward_tag['start_index']
                    + forward_tag['text'].find(tag_text)
                    + start_pos
                )
                if abs_pos in self.coref_map:
                    return self.coref_map[abs_pos]
        return None
    
    def _pass_1b_backward_coreference(self, index, chunks):
        backward_tag = self._get_backward_tag(index, chunks)
        if not backward_tag:
            return None
        tag_text = backward_tag['text']
        pronoun_verb_regex = fr'\b(he|she|they)\b\s*({self.direct_speech_verbs_regex})\b'
        matches = list(re.finditer(pronoun_verb_regex, tag_text, re.I))
        if matches:
            last_match = matches[-1]
            pronoun_start_in_tag = last_match.start(1)
            abs_pos = backward_tag['start_index'] + pronoun_start_in_tag
            if abs_pos in self.coref_map:
                return self.coref_map[abs_pos]
        return None
    
    def _pass_2_forward_explicit_tags(self, index, chunks):
        forward_tag = self._get_forward_tag(index, chunks)
        if forward_tag:
            tag_text = forward_tag['text'].lstrip(', ')
            match = re.search(
                fr'^\s*({self.subjects_regex_str})\s*'
                fr'({self.direct_speech_verbs_regex})',
                tag_text,
                re.I,
            )
            if match:
                return self.char_manager.resolve_alias(match.group(1))
            fallback = re.search(
                fr'^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+'
                fr'({self.direct_speech_verbs_regex})\b',
                tag_text,
            )
            if fallback:
                return self._ensure_subject(fallback.group(1))
        return None
        
    def _pass_2b_backward_explicit_tags(self, index, chunks):
        backward_tag = self._get_backward_tag(index, chunks)
        if backward_tag:
            tag_text = backward_tag['text'].strip()
            match = re.search(
                fr'({self.subjects_regex_str})\s+'
                fr'({self.direct_speech_verbs_regex})\s*\.?\s*$',
                tag_text,
                re.I,
            )
            if match:
                return self.char_manager.resolve_alias(match.group(1))
            fallback = re.search(
                fr'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+'
                fr'({self.direct_speech_verbs_regex})\s*\.?\s*$',
                tag_text,
            )
            if fallback:
                return self._ensure_subject(fallback.group(1))
        return None

    def _pass_4_contiguous_dialogue(self, index, chunks):
        if index > 0 and chunks[index - 1]["type"] == "dialogue":
            previous_speaker = chunks[index - 1]["speaker"]
            if (
                previous_speaker
                and previous_speaker not in ["Unassigned", "Unknown"]
                and not str(previous_speaker).startswith('[')
            ):
                return previous_speaker
        return None

    def _pass_4b_tag_continuation(self, index, chunks):
        # If pattern is: dialogue -> narration (with speech verb by same subject)
        # -> dialogue, keep same speaker.
        if (
            index > 1
            and chunks[index - 1]["type"] == "narration"
            and chunks[index - 2]["type"] == "dialogue"
        ):
            prev_speaker = chunks[index - 2]["speaker"]
            if not prev_speaker or prev_speaker in ["Unassigned", "Unknown"]:
                return None
            tag_text = chunks[index - 1]['text']
            # explicit subject mention
            if re.search(
                fr'\b{re.escape(prev_speaker)}\b\s+'
                fr'({self.direct_speech_verbs_regex})\b',
                tag_text,
                re.I,
            ):
                return prev_speaker
            # Or alias of prev_speaker
            for alias, canonical in self.char_manager.aliases.items():
                if canonical == prev_speaker:
                    if re.search(
                        fr'\b{re.escape(alias)}\b\s+'
                        fr'({self.direct_speech_verbs_regex})\b',
                        tag_text,
                        re.I,
                    ):
                        return prev_speaker
        return None

    def _pass_4c_carry_across_short_narration(self, index, chunks):
        # Carry speaker across a short narration if no conflicting explicit tag is present
        if (
            index > 1
            and chunks[index - 1]["type"] == "narration"
            and chunks[index - 2]["type"] == "dialogue"
        ):
            prev_speaker = chunks[index - 2]["speaker"]
            if not prev_speaker or prev_speaker in ["Unassigned", "Unknown"]:
                return None
            tag_text = chunks[index - 1]['text']
            # Define "short" narration
            if len(tag_text) <= 60:
                # If narration explicitly attributes speech to someone else, do not carry over
                conflict = re.search(
                    fr'\b({self.subjects_regex_str})\b\s+'
                    fr'({self.direct_speech_verbs_regex})\b',
                    tag_text,
                    re.I,
                )
                if conflict:
                    subject = self.char_manager.resolve_alias(conflict.group(1))
                    if subject and subject.lower() != str(prev_speaker).lower():
                        return None
                return prev_speaker
        return None

    def _pass_5_suggest_alternatives(self, _index, _chunks):
        if not self.context_stack:
            return None
        if len(self.context_stack) >= 2:
            last_speaker, prev_speaker = self.context_stack[0], self.context_stack[1]
            if last_speaker != prev_speaker:
                return f"[{prev_speaker}|{last_speaker}]"
        last_speaker = self.context_stack[0]
        if last_speaker != self.narrator_persona:
            return f"[{last_speaker}|{self.narrator_persona}]"
        return f"[{self.narrator_persona}|Unknown]"

    def _discover_subjects_from_tag(self, tag_text):
        if not tag_text:
            return
        verb_regex = self.all_verbs_regex
        if not verb_regex or verb_regex == "(?!x)x":
            return
        # Match capitalized subject followed by a verb in the speech/reaction list
        pattern = re.compile(fr'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b\s+({verb_regex})\b')
        for match in pattern.finditer(tag_text):
            subject = match.group(1)
            if subject:
                self._ensure_subject(subject)
        
    def _format_output_as_objects(self, chunks):
        """Format chunks into DialogueLine objects with absolute spans into the original text."""
        def iter_line_bounds(text_value: str):
            # Yield (start, end) indices for each line within s, excluding line break chars
            i = 0
            start = 0
            text_len = len(text_value)
            while i < text_len:
                if text_value[i] == '\n' or text_value[i] == '\r':
                    yield (start, i)
                    # Handle CRLF
                    if (
                        text_value[i] == '\r'
                        and i + 1 < text_len
                        and text_value[i + 1] == '\n'
                    ):
                        i += 2
                    else:
                        i += 1
                    start = i
                else:
                    i += 1
            # Last line
            if start <= text_len:
                yield (start, text_len)

        output_lines = []
        for chunk in chunks:
            orig = chunk.get('orig_text', chunk['text'])
            start_index = chunk['start_index']
            # Compute the mapping from chunk['text'] to raw text by trimming appropriate characters
            # The strip() call used upstream is chunk_text.strip('"“” ')
            # We must account for all these characters to calculate offsets correctly.
            leading_chars = {'"', '“', '”', ' '}
            trailing_chars = {'"', '“', '”', ' '}

            lead = 0
            while lead < len(orig) and orig[lead] in leading_chars:
                lead += 1
            trail = 0
            while trail < (len(orig) - lead) and orig[len(orig) - 1 - trail] in trailing_chars:
                trail += 1

            base_start = start_index + lead
            text = chunk['text']

            for (line_start, line_end) in iter_line_bounds(text):
                # Trim spaces at both ends but keep indices relative to `text`
                trim_start = line_start
                while (
                    trim_start < line_end
                    and text[trim_start].isspace()
                    and text[trim_start] not in ('\n', '\r')
                ):
                    trim_start += 1
                trim_end = line_end
                while (
                    trim_end > trim_start
                    and text[trim_end - 1].isspace()
                    and text[trim_end - 1] not in ('\n', '\r')
                ):
                    trim_end -= 1
                if trim_start >= trim_end:
                    continue

                line_text = text[trim_start:trim_end]
                span_start = base_start + trim_start
                span_end = base_start + trim_end

                if chunk["type"] == "narration":
                    output_lines.append(DialogueLine(
                        text=line_text,
                        speaker=self.narrator_persona,
                        line_type="narration",
                        is_suggestion=False,
                        suggestions=[],
                        span_start=span_start,
                        span_end=span_end,
                    ))
                else:
                    speaker = chunk["speaker"]
                    is_suggestion = str(speaker).startswith('[') and str(speaker).endswith(']')
                    suggestions = []
                    final_speaker = speaker
                    if is_suggestion:
                        suggestions = speaker.strip('[]').split('|')
                        final_speaker = suggestions[0]
                    output_lines.append(DialogueLine(
                        text=line_text,
                        speaker=final_speaker,
                        line_type="dialogue",
                        is_suggestion=is_suggestion,
                        suggestions=suggestions,
                        span_start=span_start,
                        span_end=span_end,
                    ))
        return output_lines


class DialogueParserService:
    """
    Main service to orchestrate the dialogue parsing process.
    """
    def __init__(self, protagonist_name: str = "Protagonist", default_options=None):
        print("Initializing models...", file=sys.stderr)
        self.char_manager = CharacterManager()
        # Lazy load models to avoid long startup time
        self.coref_model = None
        self.coref_error = None
        self.protagonist_name = protagonist_name
        self.default_options = normalize_parser_options(
            default_options or {},
            protagonist_name=protagonist_name,
        )
        self.knowledge_store = None
        self.blocked_speakers = set(DEFAULT_BLOCKED_SPEAKERS)
        self.speech_verbs = _load_verb_list(DEFAULT_SPEECH_VERBS_PATH)
        self.reaction_verbs = _load_verb_list(DEFAULT_REACTION_VERBS_PATH)
        print("Models initialized.", file=sys.stderr)

    def _ensure_coref_model(self):
        if self.coref_model or self.coref_error:
            return
        try:
            self.coref_model = FCoref(
                model_name_or_path="biu-nlp/f-coref",
                device=None,
            )
        except Exception as exc:
            self.coref_error = exc
            print(f"Coreference model load failed: {exc}", file=sys.stderr)

    def _create_coref_lookup_map(self, text, clusters):
        lookup_map = {}
        for cluster in clusters:
            canonical_name = None
            mentions_in_cluster = [text[start:end] for start, end in cluster]
            for mention in mentions_in_cluster:
                resolved_mention = self.char_manager.resolve_alias(mention)
                if self.char_manager.is_known_subject(resolved_mention):
                    canonical_name = resolved_mention
                    break
            if not canonical_name:
                sorted_mentions = sorted(
                    [
                        m
                        for m in mentions_in_cluster
                        if m.lower() not in ["i", "he", "she", "we", "they"]
                    ],
                    key=len,
                    reverse=True,
                )
                if sorted_mentions:
                    canonical_name = self.char_manager.resolve_alias(sorted_mentions[0])
            if canonical_name:
                for mention_start, _ in cluster:
                    lookup_map[mention_start] = canonical_name
        return lookup_map

    def parse_file(self, file_path: str, options: dict = None):
        """
        Reads a file, runs coreference resolution, and parses the dialogue.
        
        Returns:
            A tuple containing:
            - A list of DialogueLine objects.
            - A list of unique character names found in the script.
        """
        try:
            # Use newline='' to prevent Python from translating CRLF -> \n on Windows.
            # We need exact character offsets that match what the UI reads from disk.
            with open(file_path, "r", encoding="utf-8", newline='') as file_handle:
                text = file_handle.read()

            merged_options = deepcopy(self.default_options)
            if options:
                merged_options.update(options)
                if isinstance(options.get("heuristics"), dict):
                    merged_options["heuristics"].update(options.get("heuristics", {}))
            options = normalize_parser_options(
                merged_options,
                protagonist_name=self.protagonist_name,
            )
            # Load knowledge store for this book and merge into character manager
            self.knowledge_store = load_knowledge_for_file(file_path)
            if self.knowledge_store:
                # Merge genders
                self.char_manager.genders.update(self.knowledge_store.genders)
                # Merge aliases (alias -> canonical)
                for alias, canonical in self.knowledge_store.aliases.items():
                    self.char_manager.aliases[alias] = canonical
                self.char_manager.reload_views()
                # Load dynamic speaker blocklist if present
                blocklist_path = os.path.join(
                    os.path.dirname(file_path),
                    "speaker_blocklist.json",
                )
                if not os.path.exists(blocklist_path):
                    # Also check at book root
                    blocklist_path = os.path.join(
                        os.path.dirname(os.path.dirname(file_path)),
                        "speaker_blocklist.json",
                    )
                if os.path.exists(blocklist_path):
                    with open(blocklist_path, "r", encoding="utf-8") as blocklist_handle:
                        blocklist_data = json.load(blocklist_handle)
                        for name in blocklist_data.get("blocked_speakers", []):
                            normalized = _normalize_speaker_token(name)
                            if normalized:
                                self.blocked_speakers.add(normalized)

            for name in options.get("protagonists", []) or []:
                self.char_manager.ensure_name(name)
            self.char_manager.ensure_name(NARRATOR_PERSONA)

            coref_lookup = {}
            if options.get("heuristics", {}).get("coreference", True):
                print("Running coreference resolution...", file=sys.stderr)
                self._ensure_coref_model()
                if self.coref_model:
                    preds = self.coref_model.predict(texts=[text])
                    clusters = preds[0].get_clusters(as_strings=False)
                    print("Coreference resolution complete.", file=sys.stderr)

                    print("Creating coreference lookup map...", file=sys.stderr)
                    coref_lookup = self._create_coref_lookup_map(text, clusters)
                else:
                    print("Coreference disabled: model unavailable.", file=sys.stderr)


            print("Parsing text...", file=sys.stderr)
            dialogue_parser = HybridDialogueParser(
                self.char_manager,
                narrator_persona=NARRATOR_PERSONA,
                protagonists=options.get("protagonists", []),
                options=options,
                direct_speech_verbs=self.speech_verbs,
                reaction_verbs=self.reaction_verbs,
                speech_verbs_path=DEFAULT_SPEECH_VERBS_PATH,
            )
            # Pass dynamic blocklist into parser
            dialogue_parser.blocked_speakers = set(self.blocked_speakers)

            parsed_lines = dialogue_parser.parse(text, coref_map=coref_lookup)
            print("Parsing complete!", file=sys.stderr)

            # Print rule performance last
            if dialogue_parser.rule_hits:
                print("Rule performance:", file=sys.stderr)
                for rule_name, count in sorted(
                    dialogue_parser.rule_hits.items(),
                    key=lambda kv: kv[0],
                ):
                    print(f"  {rule_name}: {count}", file=sys.stderr)

            # Extract unique character names from the final script
            parsed_character_names = sorted(
                list(
                    set(
                        line.speaker
                        for line in parsed_lines
                        if _normalize_speaker_token(line.speaker) not in self.blocked_speakers
                    )
                )
            )
            
            return parsed_lines, parsed_character_names

        except FileNotFoundError:
            print(f"ERROR: The file '{file_path}' was not found.", file=sys.stderr)
            raise
        except Exception as error:  # pylint: disable=broad-except
            print(
                f"An unexpected error occurred during parsing: {error}",
                file=sys.stderr,
            )
            raise


def _load_verb_list(file_path: str):
    if not file_path or not os.path.exists(file_path):
        return []
    verbs = []
    with open(file_path, "r", encoding="utf-8") as file_handle:
        for line in file_handle:
            cleaned = line.strip().lower()
            if not cleaned or cleaned.startswith("#"):
                continue
            verbs.append(cleaned)
    return sorted(set(verbs))



def normalize_parser_options(options: dict, protagonist_name: str = None):
    merged = deepcopy(DEFAULT_PARSER_OPTIONS)
    if not options:
        options = {}
    # Backward compatibility for legacy keys
    if "protagonistName" in options and "protagonists" not in options:
        options["protagonists"] = [options.get("protagonistName")]
    if "protagonistNames" in options and "protagonists" not in options:
        options["protagonists"] = options.get("protagonistNames")

    for key, value in options.items():
        if key == "heuristics" and isinstance(value, dict):
            for h_key, h_value in value.items():
                merged["heuristics"][h_key] = bool(h_value)
        else:
            merged[key] = value

    protagonists = merged.get("protagonists") or []
    if isinstance(protagonists, str):
        protagonists = [protagonists]
    protagonists = [p for p in protagonists if p]
    if not protagonists and protagonist_name:
        protagonists = [protagonist_name]
    merged["protagonists"] = protagonists

    pov_mode = merged.get("pov_mode") or "first_person"
    if pov_mode not in ("first_person", "third_person"):
        pov_mode = "first_person"
    merged["pov_mode"] = pov_mode
    return merged

if __name__ == "__main__":
    service = DialogueParserService()
    parsed_lines, parsed_character_names = service.parse_file("./samples/prologue.txt")
    print(parsed_lines)
    print(parsed_character_names)


