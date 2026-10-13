# OpenBook

OpenBook is a desktop app for preparing audiobooks of novels: it parses chapters, attributes dialogue to characters, assigns TTS voices, and generates per-line audio.

## Language

### The book

**Book**:
A novel under preparation, anchored to a folder containing its chapters and character data.

**Chapter**:
A unit of a book's source text, each with its own dialogue data, roster, and audio.

**Book root**:
The folder anchoring a book's data.
_Avoid_: project root

**Audio root**:
The folder anchoring generated audio output.

### Characters

**Character**:
A story speaker. Its stored identity groups a title and its alias surface forms into one cluster.
_Avoid_: Speaker (that is a TTS voice), entry, record

**Alias cluster**:
A character's identity as one logical unit: its title plus every alias surface form.
_Avoid_: character record

**Title**:
A character's display name. Renaming a title promotes the old title to an alias — no name is ever lost.
_Avoid_: name, primary name, surface name, main alias

**Alias**:
An alternative surface form a character appears by in the text.
_Avoid_: primary alias, main alias (retired concepts)

**Descriptor**:
A soft piece of attribution evidence about a character, learned during chapter review. Not an alias, and not durable — later reviews can re-append it.

**Short GUID**:
A character's stable 8-character Crockford base32 identity, decoupled from all of its names.
_Avoid_: slug, id (the v2 name-derived form)

**Narrator**:
A reserved sentinel speaker for unattributed narration. Not a character and has no character file.

**Roster**:
The set of characters who speak in a chapter.

**Reference family**:
The four data families that reference a character by GUID: dialogue files, chapter rosters, voices, and audio manifests.

**Character stats**:
A character's book-wide speaking volume: how many lines they speak and in how many chapters.
_Avoid_: character counts, speaking totals

**Stats accumulation**:
The per-character, per-chapter line counts from which character stats are derived; rebuilt when a book is opened and adjusted as assignments change.
_Avoid_: counts cache, tallies

### Voices

**Voice**:
A TTS voice option (provider + voice) available to read lines.

**Voice assignment**:
The mapping from a character to the voice that reads its lines.

**Speaker**:
A TTS voice reading a character's lines (v2 terminology fix). Never used for a story character.

**Unknown voice**:
The fallback voice used for lines with no attributed character.

### Dialogue & attribution

**Line**:
One unit of a chapter's dialogue: a span of source text with a speaker reference and attribution metadata.

**Attribution**:
The parser's per-line output: candidate speakers with confidence, margin, and risk.

**Candidate**:
A speaker proposed for a line, with a confidence score.

**Conflict**:
A line the parser is not confident about, flagged for manual review.

**Resolution status**:
The state of a line's assignment: `auto`, `unknown`, or `user_confirmed`.

**Unknown line**:
A line persisted with no attributed character, rather than being defaulted to Narrator.

**Gold label**:
The curated, user-accepted assignment used as ground truth in evaluations.

**Character catalog**:
The closed-world set of canonical characters (identity, names, aliases, gender) passed to the parser on each parse.

**Closed-world catalog**:
The policy that the parser must not silently add surfaces outside the catalog; out-of-catalog surfaces surface for human canonicalization instead.

**Explicit anchor**:
A hard, literal attribution in the text (e.g. "Jacob said") that pins a quote's speaker.

**Episode**:
A short two-participant stretch of dialogue, decoded jointly rather than line by line.

**Joint decoder**:
The Viterbi decoding of an episode using speaker-alternation transition weights.

**Sieve**:
An ordered, high-precision rule that resolves hard evidence before softer conversational evidence.

**Reaction verb**:
Non-speech evidence (e.g. "nodded") that must not count as attribution.

**Decision trace**:
Per-line provenance of how the parser reached its assignment.

**Context gender cue**:
Nearby pronoun evidence on a line (e.g. "he said").

**Gender conflict**:
A flag that a line's chosen speaker contradicts its context gender cue.

**Reviewed chapter**:
A chapter whose line assignments have all been accepted as gold; any later edit clears the state.

### Attribution providers

**Provider**:
A parallel speaker-attribution backend: Gemini, local llama, JEV, or Open-Jev.

**JEV**:
TypeSafe's "System One" typed decision API. Returns structured answers with probability distributions, not generated text.

**Open-Jev**:
The open-weights local twin of JEV (9B), served on a local server.

**OpenJevPro**:
The designated fallback for the local track (Qwen3-8B on LM Studio).

**Noul**:
A yes/no question to a decision model; the answer is a single probability.

**Choice question**:
A pick-over-options question; the answer carries the chosen option, the full probability vector, and a confidence.

**Scene roster**:
The candidate-speaker set for a line, built from nearby dialogue turns.

**Verify layer**:
The JEV cross-verification pass over parser output, governed by a verify policy.

**Assert**:
A committed speaker attribution — the high-stakes, destructive action the false-positive-minimization policy protects.

**Suggestion**:
An uncommitted attribution below the assert threshold.

**Auto-apply**:
Writing an attribution automatically when its top probability clears the threshold.

### Evaluation

**Frozen books**:
The fixed evaluation corpus: Books 14–16 for development, Book 17 for validation, Book 18 for a one-shot confirmation.

**Baseline**:
The frozen parser state (books + thresholds) against which changes are compared.

**Steering triple**:
The headline metrics: assert rate (with a floor), assert precision, and guessHit@3 — replacing bare accuracy.

**Pre-registered promotion gate**:
Numeric go/no-go gates declared before held-out data is opened.

### Audio

**Clip**:
A generated per-line audio file.

**Manifest**:
The per-character record tracking which clips exist for a chapter.

**Merged split**:
The experimental generation of one clip spanning character + narration + character, with ASR suggesting the split points.

**Regenerate manifest**:
The audit output listing missing or wrong-line clips to be regenerated.

### Process

**Council**:
The multi-advisor decision protocol (Crystal Society) that adjudicates design disputes.

**Ruling**:
A numbered council decision (R1, R2, …) that is part of the decision record.

**Lane**:
A parallel refactor workstream with strict file ownership and a single owner.
_Avoid_: workstream

**Dual I/O**:
The persistence model where the frontend writes through the File System Access API with a backend save as fallback.
