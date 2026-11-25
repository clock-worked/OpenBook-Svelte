"""The parsed-line record every parser produces.

A DialogueLine is one line of dialogue or narration with its speaker, its
attribution and its span in the chapter text. It lives in the models
package so the parsers (legacy, BookNLP, ModernBookNLP) and the services that
read their output depend on the record, not on the legacy parser module.
"""

import re


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
        attribution=None,
    ):
        self.speaker = speaker
        self.line_type = line_type
        self.is_suggestion = is_suggestion
        self.suggestions = suggestions or []
        self.attribution = attribution
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
            'attribution': self.attribution,
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
                attribution=data.get('attribution'),
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
                attribution=data.get('attribution'),
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
