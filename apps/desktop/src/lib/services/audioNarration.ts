import type { DialogueLine } from '$lib/types';

/**
 * Speech-verb narration tagging for advanced audio generation.
 *
 * Normal generation strips narration context about *how* a character says a
 * line (e.g. "Caleb muttered, shaking his head"). When the advanced-overlay
 * "include speech-verb narration" option is on, these short tags are found
 * next to each dialogue line and re-included in the text sent to TTS.
 *
 * Pure helpers only — line text, chapter tags and raw chapter text are all
 * passed in, so the module is trivially unit-testable.
 */

/** Common speech-attribution verbs used to recognise narration tags. */
const SPEECH_VERBS = ['said', 'asked', 'replied', 'answered', 'muttered', 'shouted', 'whispered', 'snapped', 'yelled', 'growled', 'sighed', 'hissed', 'called', 'told', 'added', 'continued', 'remarked', 'noted', 'stated', 'declared', 'insisted'];

/** True when the text contains a whole speech-attribution verb ("he said,", "she whispered", ...). */
export function hasSpeechVerb(text: string): boolean {
  const lower = String(text || '').toLowerCase();
  if (!lower.trim()) return false;
  return SPEECH_VERBS.some((verb) => new RegExp(`\\b${verb}\\b`).test(lower));
}

/** A short narration line that tags how a neighbouring line was spoken. */
export interface NarrationTagCandidate {
  text: string;
  spanStart: number;
  spanEnd: number;
}

/** Tags longer than this are narration sentences, not speech-verb tags. */
export const MAX_SPEECH_VERB_TAG_WORDS = 12;

/** Max characters of quotes/whitespace/punctuation allowed between a tag and its line. */
const MAX_TAG_GAP_CHARS = 30;

/** Characters that may appear in the gap between a tag span and a line span. */
const TAG_GAP_CHARS_RE = /^[\s"'“”‘’`.,;:!?…—–-]*$/;

const EDGE_QUOTES_RE_START = /^[\s"'“”‘’`]+/;
const EDGE_QUOTES_RE_END = /[\s"'“”‘’`]+$/;

function countWords(text: string): number {
  const cleaned = String(text || '').trim();
  if (!cleaned) return 0;
  return cleaned.split(/\s+/).length;
}

/**
 * True when a narration line is a short speech-verb tag ("Caleb muttered,
 * shaking his head") rather than a full narration sentence.
 */
export function isShortSpeechVerbTag(text: string): boolean {
  const trimmed = String(text || '').trim();
  if (!trimmed) return false;
  if (countWords(trimmed) > MAX_SPEECH_VERB_TAG_WORDS) return false;
  return hasSpeechVerb(trimmed);
}

/**
 * Pull the short speech-verb narration tags out of a chapter's dialogue
 * lines (the lines as persisted in dialogue.json), ordered by span position.
 */
export function extractNarrationTags(dialogueLines: DialogueLine[]): NarrationTagCandidate[] {
  const tags: NarrationTagCandidate[] = [];
  for (const line of dialogueLines) {
    if (!line || line.characterId !== 'narrator') continue;
    const span = line.span;
    if (!span || !Number.isFinite(span.start) || !Number.isFinite(span.end) || span.end < span.start) continue;
    if (!isShortSpeechVerbTag(line.text)) continue;
    tags.push({ text: line.text, spanStart: span.start, spanEnd: span.end });
  }
  return tags.sort((a, b) => a.spanStart - b.spanStart);
}

/** Trim a tag's text and drop edge quotes plus trailing sentence punctuation. */
function normalizeTagText(text: string): string {
  return String(text || '')
    .trim()
    .replace(EDGE_QUOTES_RE_START, '')
    .replace(EDGE_QUOTES_RE_END, '')
    .replace(/[.,;:!?…]+$/, '')
    .trim();
}

/** Remove surrounding whitespace and quote characters from a raw text region. */
function stripEdgeQuotes(text: string): string {
  return String(text || '')
    .replace(EDGE_QUOTES_RE_START, '')
    .replace(EDGE_QUOTES_RE_END, '')
    .trim();
}

/** End a tag with a period when more spoken text follows it. */
function punctuateIfFollowed(text: string, followed: boolean): string {
  if (followed && !/[.!?]$/.test(text)) return `${text}.`;
  return text;
}

/**
 * True when the raw-text gap between two adjacent spans contains only
 * quotes/whitespace/punctuation (i.e. the spans are directly attached).
 * Without raw text the gap cannot be verified, so it is allowed.
 */
function isCleanGap(rawText: string | null, from: number, to: number): boolean {
  if (rawText === null) return true;
  if (to < from) return false;
  const gap = rawText.slice(from, to);
  return gap.length <= MAX_TAG_GAP_CHARS && TAG_GAP_CHARS_RE.test(gap);
}

type TagPlacement = { tag: NarrationTagCandidate; kind: 'before' | 'after' | 'inside' };

/**
 * Rebuild a dialogue line's TTS text with adjacent speech-verb narration
 * tags re-included. Returns the original text when no tag applies, so
 * callers degrade gracefully (no span, no tag, unreadable chapter).
 *
 * Handles three layouts:
 * - tag immediately before the line span → prepended
 * - tag immediately after the line span → appended
 * - tag embedded inside the line span (parser merged the quotes around it
 *   into one line) → the line text is reconstructed from the raw chapter
 *   region with the tag inserted at its original position.
 */
export function buildLineTextWithSpeechVerbTags(
  line: DialogueLine,
  narrationTags: NarrationTagCandidate[],
  rawChapterText: string | null,
): string {
  const original = String(line?.text ?? '');
  const span = line?.span;
  if (!span || !Number.isFinite(span.start) || !Number.isFinite(span.end) || span.end < span.start) {
    return original;
  }
  if (!Array.isArray(narrationTags) || narrationTags.length === 0) return original;

  const placements: TagPlacement[] = [];
  for (const tag of narrationTags) {
    if (tag.spanStart >= span.start && tag.spanEnd <= span.end && (tag.spanStart > span.start || tag.spanEnd < span.end)) {
      placements.push({ tag, kind: 'inside' });
      continue;
    }
    if (tag.spanEnd <= span.start) {
      if (isCleanGap(rawChapterText, tag.spanEnd, span.start)) {
        placements.push({ tag, kind: 'before' });
      }
      continue;
    }
    if (tag.spanStart >= span.end) {
      if (isCleanGap(rawChapterText, span.end, tag.spanStart)) {
        placements.push({ tag, kind: 'after' });
      }
    }
  }
  if (placements.length === 0) return original;

  const inside = placements
    .filter((p) => p.kind === 'inside')
    .map((p) => p.tag)
    .sort((a, b) => a.spanStart - b.spanStart);

  // Embedded tags: reconstruct the spoken text from the raw chapter region,
  // inserting each tag where the source had it.
  if (inside.length > 0 && rawChapterText !== null) {
    let cursor = span.start;
    const pieces: { text: string; isTag: boolean }[] = [];
    for (const tag of inside) {
      const region = stripEdgeQuotes(rawChapterText.slice(cursor, tag.spanStart));
      if (region) pieces.push({ text: region, isTag: false });
      const tagText = normalizeTagText(tag.text);
      if (tagText) pieces.push({ text: tagText, isTag: true });
      cursor = tag.spanEnd;
    }
    const tail = stripEdgeQuotes(rawChapterText.slice(cursor, span.end));
    if (tail) pieces.push({ text: tail, isTag: false });
    for (let i = 0; i < pieces.length - 1; i++) {
      if (pieces[i].isTag) pieces[i].text = punctuateIfFollowed(pieces[i].text, true);
    }
    const rebuilt = pieces.map((p) => p.text).join(' ').trim();
    if (rebuilt) return rebuilt;
    // Raw region came back empty — fall through and append the tags.
  }

  const before = placements
    .filter((p) => p.kind === 'before')
    .map((p) => p.tag)
    .sort((a, b) => b.spanStart - a.spanStart)[0];
  const after = placements
    .filter((p) => p.kind === 'after')
    .map((p) => p.tag)
    .sort((a, b) => a.spanStart - b.spanStart)[0];

  const pieces: string[] = [];
  if (before) pieces.push(punctuateIfFollowed(normalizeTagText(before.text), true));
  if (original.trim()) pieces.push(original);
  if (after) pieces.push(normalizeTagText(after.text));
  // No raw text to position embedded tags — include them at the end.
  for (const tag of inside) pieces.push(normalizeTagText(tag.text));

  const result = pieces.filter((p) => p && p.trim()).join(' ').trim();
  return result || original;
}
