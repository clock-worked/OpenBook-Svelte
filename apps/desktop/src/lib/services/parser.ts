import { get } from 'svelte/store';
import { parserHints } from '$lib/stores/settings';
import type { Line, DialogueJson, DialogueLine, Character, LineCandidate, ParserHints, CharactersJson } from '$lib/types';
import { readTextFile, writeCentralCharacters, writeDialogue } from '$lib/services/fs';
import { computeReturningFlags } from '$lib/services/dialogueReturning';
import { buildIdToNameMap, buildNameToIdMap } from '$lib/stores/characters';
import { API_ENDPOINTS, apiPostJson, toApiClientError } from '$lib/services/apiClient';
import { resolveBookCharacters } from '$lib/services/chapterCharacterRepository';
import { ALWAYS_BLOCKED_CHARACTER_NAMES, buildUniqueCharacterId, normalizeCharacterKey } from '$lib/services/characterIdentity';
import type { JevVerifySummary } from '$lib/services/apiContracts';
import { setJevVerifySummary } from '$lib/stores/jevDialogueAi';
import { buildClosedWorldParserOptions } from '$lib/services/parserCatalog';
import { history } from '$lib/services/history';

interface ParserOutput {
  script: Line[];
  characters: string[];
  meta: { version: string; jevVerify?: JevVerifySummary | null };
}

/**
 * Convert character name to ID (lowercase, normalized)
 */
function nameToId(name: string): string {
  return name.toLowerCase().trim();
}

function isBlockedCharacterToken(name: string): boolean {
  return ALWAYS_BLOCKED_CHARACTER_NAMES.has(normalizeCharacterKey(name));
}

function stripNumericSlugSuffix(name: string): string {
  return String(name || '').trim().toLowerCase().replace(/(?:[-_]\d+)+$/g, '');
}

function lineSegmentsToText(line: Line | undefined): string {
  if (!line || !Array.isArray(line.segments)) return '';
  return line.segments.map((seg) => {
    if (seg.type === 'text') return seg.value;
    if (seg.type === 'pause') return ' ';
    return '';
  }).join('');
}

function countWords(text: string): number {
  const cleaned = String(text || '').trim();
  if (!cleaned) return 0;
  return cleaned.split(/\s+/).length;
}

function hasSpeechVerb(text: string): boolean {
  const lower = ` ${String(text || '').toLowerCase()} `;
  const verbs = ['said', 'asked', 'replied', 'answered', 'muttered', 'shouted', 'whispered', 'snapped', 'yelled', 'growled', 'sighed', 'hissed', 'called', 'told', 'added', 'continued', 'remarked', 'noted', 'stated', 'declared', 'insisted'];
  return verbs.some((verb) => lower.includes(` ${verb} `));
}

function clampConfidence(value: number): number {
  if (!Number.isFinite(value)) return 0.5;
  if (value < 0.15) return 0.15;
  if (value > 1) return 1;
  return value;
}

type RankedParserCandidate = {
  name: string;
  characterId: string | null;
  confidence: number;
  reasons?: string[];
};

function mergeReasonLists(...lists: Array<string[] | undefined>): string[] | undefined {
  const merged = new Set<string>();
  for (const list of lists) {
    for (const reason of list || []) {
      const value = String(reason || '').trim();
      if (value) merged.add(value);
    }
  }
  return merged.size > 0 ? Array.from(merged) : undefined;
}

function collapseMappedCandidates(
  rawCandidates: Array<{ name: string; confidence: number; reasons?: string[] }>,
  nameToCharIdMap: Map<string, string>,
  idToNameMap: Map<string, string>
): RankedParserCandidate[] {
  const collapsed = new Map<string, RankedParserCandidate & { order: number }>();

  rawCandidates.forEach((candidate, index) => {
    const normalizedName = normalizeCharacterKey(candidate.name);
    if (!normalizedName || isBlockedCharacterToken(normalizedName)) return;

    const mappedId = nameToCharIdMap.get(normalizedName) || null;
    const canonicalName = mappedId
      ? (idToNameMap.get(mappedId.toLowerCase()) || candidate.name)
      : candidate.name;
    const key = mappedId ? `id:${mappedId.toLowerCase()}` : `name:${normalizedName}`;
    const existing = collapsed.get(key);
    if (!existing) {
      collapsed.set(key, {
        name: canonicalName,
        characterId: mappedId,
        confidence: clampConfidence(candidate.confidence),
        reasons: mergeReasonLists(candidate.reasons),
        order: index,
      });
      return;
    }

    if (candidate.confidence > existing.confidence) {
      existing.name = canonicalName;
      existing.confidence = clampConfidence(candidate.confidence);
    }
    existing.characterId = existing.characterId || mappedId;
    existing.reasons = mergeReasonLists(existing.reasons, candidate.reasons);
    existing.order = Math.min(existing.order, index);
  });

  return Array.from(collapsed.values())
    .sort((left, right) => right.confidence - left.confidence || left.order - right.order)
    .map(({ order: _order, ...candidate }) => candidate);
}

function estimateDialogueConfidence(line: Line, index: number, allLines: Line[], sourceSuggestions: string[]): number {
  if (line.line_type !== 'dialogue') return 1;

  let confidence = line.is_suggestion ? 0.62 : 0.98;
  if (sourceSuggestions.length > 1) confidence -= 0.06;
  if (sourceSuggestions.length === 0) confidence -= 0.04;

  const prev = allLines[index - 1];
  const prevPrev = allLines[index - 2];
  const next = allLines[index + 1];

  if (prev?.line_type === 'dialogue') confidence -= 0.05;
  if (next?.line_type === 'dialogue') confidence -= 0.03;

  if (prev?.line_type === 'narration') {
    const prevText = lineSegmentsToText(prev);
    const prevLower = prevText.toLowerCase();
    const speakerLower = String(line.speaker || '').toLowerCase().trim();
    const mentionsSpeaker = !!speakerLower && prevLower.includes(speakerLower);
    const verbNearby = hasSpeechVerb(prevText);
    const prevWords = countWords(prevText);

    if (mentionsSpeaker && verbNearby) {
      const isInterruption = prevPrev?.line_type === 'dialogue';
      if (isInterruption) {
        confidence = Math.min(confidence, 0.95);
      } else {
        confidence = Math.max(confidence, 0.99);
      }
    }

    if (prevText.includes('\n\n')) confidence -= 0.08;
    if (prevWords >= 24) confidence -= 0.08;
  }

  return clampConfidence(confidence);
}

/**
 * Convert parser output into the persisted dialogue.json line shape.
 */
function convertLineToDialogueLine(
  line: Line,
  id: number,
  nameToCharIdMap: Map<string, string>,
  idToNameMap: Map<string, string>,
  index: number,
  allLines: Line[]
): DialogueLine {
  const isNarration = line.line_type === 'narration';

  // Convert segments to text string
  const text = line.segments.map(seg => {
    if (seg.type === 'text') {
      return seg.value;
    } else if (seg.type === 'pause') {
      const duration = seg.duration || 'medium';
      if (duration === 'short') return '[pause short]';
      if (duration === 'long') return '[pause long]';
      return '[pause]';
    }
    return '';
  }).join('');

  // Convert speaker name to character ID
  const speakerKey = line.speaker ? line.speaker.toLowerCase().trim() : '';
  const speakerIsBlocked = isBlockedCharacterToken(speakerKey);
  const parserAttribution = line.attribution;

  const sourceSuggestions = !isNarration
    ? (() => {
      const fromAttribution = Array.isArray(parserAttribution?.candidates)
        ? parserAttribution.candidates.map((candidate) => String(candidate?.name || '').trim()).filter(Boolean)
        : [];
      if (fromAttribution.length > 0) return fromAttribution;
      const fromParser = Array.isArray(line.suggestions)
        ? line.suggestions.map((name) => String(name || '').trim()).filter(Boolean)
        : [];
      if (fromParser.length > 0) return fromParser;
      const fallbackSpeaker = String(line.speaker || '').trim();
      return fallbackSpeaker ? [fallbackSpeaker] : [];
    })()
      .filter((name) => !isBlockedCharacterToken(name))
    : [];

  const estimatedConfidence = estimateDialogueConfidence(line, index, allLines, sourceSuggestions);
  const rawCandidates = !isNarration && Array.isArray(parserAttribution?.candidates) && parserAttribution.candidates.length > 0
    ? parserAttribution.candidates
      .map((candidate) => ({
        name: String(candidate?.name || '').trim(),
        confidence: clampConfidence(candidate?.confidence),
        reasons: Array.isArray(candidate?.reasons)
          ? candidate.reasons.filter((reason) => typeof reason === 'string' && reason.trim().length > 0)
          : undefined,
      }))
      .filter((candidate) => candidate.name.length > 0)
    : sourceSuggestions.map((name, idx) => ({
      name,
      confidence: Math.max(0.15, estimatedConfidence - (idx * 0.1)),
    }));
  const collapsedCandidates = collapseMappedCandidates(rawCandidates, nameToCharIdMap, idToNameMap);
  const baseConfidence = collapsedCandidates[0]?.confidence ?? estimatedConfidence;

  const hasNamedCandidates = collapsedCandidates.some((candidate) => !!candidate.characterId);
  const resolvedSpeakerId = speakerKey ? (nameToCharIdMap.get(speakerKey) || null) : null;
  const selectedReasons = parserAttribution?.decisionTrace?.selectedReasons;
  const hasDeterministicFirstPersonAttribution = !!resolvedSpeakerId
    && Array.isArray(selectedReasons)
    && selectedReasons.some((reason) =>
      reason === '0p_protagonist_first_person' || reason === 'first_person_override'
    );

  const shouldAbstain = !isNarration && !hasDeterministicFirstPersonAttribution && (
    line.is_suggestion === true
    || baseConfidence < 0.74
    || (collapsedCandidates.length > 1 && baseConfidence < 0.86)
    || !hasNamedCandidates
    || speakerIsBlocked
  );

  const characterId = isNarration
    ? 'narrator'
    : (shouldAbstain
      ? null
      : resolvedSpeakerId);

  if (!isNarration && !shouldAbstain && line.speaker && !resolvedSpeakerId) {
    console.warn(`[parser] Speaker "${line.speaker}" not found in characters.json, leaving unknown`);
  }

  const candidates: LineCandidate[] = collapsedCandidates
    .filter((candidate) => !!candidate.characterId)
    .map((candidate) => ({
      characterId: candidate.characterId || '',
      confidence: candidate.confidence,
    }));
  const topCandidateConfidence = collapsedCandidates[0]?.confidence ?? baseConfidence;
  const secondCandidateConfidence = collapsedCandidates[1]?.confidence ?? 0;
  const marginToSecond = Math.max(0, topCandidateConfidence - secondCandidateConfidence);
  const misattributionRisk = clampConfidence((1 - topCandidateConfidence) * 0.7 + (1 - marginToSecond) * 0.3);
  const hasSpan = typeof (line as any).span_start === 'number' && typeof (line as any).span_end === 'number';
  return {
    id,
    characterId,
    text,
    span: hasSpan ? { start: (line as any).span_start as number, end: (line as any).span_end as number } : null,
    metadata: {
      emotion: null,
      intensity: 0.5,
      pacing: null,
      prefix: null,
      customTags: {
        sourceAlias: line.speaker || null,
        sourceCandidates: sourceSuggestions,
      }
    },
    candidates,
    isConflict: isNarration ? false : (line.is_suggestion || false || shouldAbstain),
    isReturning: false,
    attribution: isNarration
      ? {
        confidence: 1,
        topCandidateConfidence: 1,
        marginToSecond: 1,
        misattributionRisk: 0,
        resolutionStatus: 'auto',
        thresholdUsed: 0.62,
        sourceAlias: 'Narrator',
        sourceCandidates: ['Narrator'],
        candidates: [
          {
            characterId: 'narrator',
            name: 'Narrator',
            confidence: 1,
            reasons: ['non_quoted_narration']
          }
        ]
      }
      : {
        confidence: topCandidateConfidence,
        topCandidateConfidence,
        marginToSecond,
        misattributionRisk,
        resolutionStatus: shouldAbstain ? 'unknown' : 'auto',
        thresholdUsed: 0.62,
        sourceAlias: line.speaker || null,
        sourceCandidates: sourceSuggestions,
        contextGender: parserAttribution?.contextGender ?? null,
        contextGenderCue: parserAttribution?.contextGenderCue ?? null,
        genderConflict: parserAttribution?.genderConflict ?? false,
        parserBackend: parserAttribution?.parserBackend ?? null,
        decisionTrace: parserAttribution?.decisionTrace ?? null,
        candidates: collapsedCandidates.map((candidate) => ({
          characterId: candidate.characterId,
          name: candidate.name,
          confidence: candidate.confidence,
          reasons: candidate.reasons,
        })),
      }
  };
}

/**
 * Read the chapter text file: File System API first, then the backend
 * readText endpoint; throws when neither yields content.
 */
async function readChapterText(input: string): Promise<string> {
  let fileContent = await readTextFile(input);
  if (fileContent === null) {
    const response = await apiPostJson<{ content: string }>(API_ENDPOINTS.readText, {
      file_path: input,
    });
    fileContent = response.content;
  }
  if (fileContent === null) {
    throw new Error(`Could not read file content from ${input}`);
  }
  return fileContent;
}

/**
 * Resolve the book's character catalog for this parse: the v3
 * `characters/` folder first, falling back to the legacy root
 * `characters.json`. An empty book resolves to an empty v2.0 catalog.
 */
async function resolveBookCatalog(): Promise<CharactersJson> {
  const catalog = await resolveBookCharacters('');
  return catalog ?? { formatVersion: '2.0', characters: [] };
}

/**
 * Extract the chapter name from the input path:
 * "{chapterId}/chapter.txt" -> "{chapterId}"; the legacy "chapter.txt"
 * shape falls back to the filename minus extension.
 */
function extractChapterName(input: string): string {
  const pathParts = input.split('/');
  if (pathParts.length > 1 && pathParts[pathParts.length - 1] === 'chapter.txt') {
    return pathParts[pathParts.length - 2];
  }
  return pathParts[pathParts.length - 1].replace(/\.txt$/, '');
}

/**
 * Bootstrap the book's character set from the parsed chapter: keeps the
 * catalog's characters (minus blocked tokens) and, for books without a
 * closed-world catalog yet, adds one record per unknown parsed name.
 * With a catalog present, unknown parser surfaces stay provisional and are
 * resolved explicitly during review.
 */
function bootstrapCharacters(
  catalog: CharactersJson,
  parsedCharacterNames: string[],
  chapterName: string,
): { characters: CharactersJson; nameToCharIdMap: Map<string, string> } {
  const allBookChars = catalog.characters.filter((character) =>
    !isBlockedCharacterToken(character.id) && !isBlockedCharacterToken(character.name),
  );
  const existingBookCharNames = new Set(allBookChars.map((c) => c.name.toLowerCase().trim()));
  const existingBookCharIds = new Set(allBookChars.map((c) => c.id));

  for (const char of allBookChars) {
    const aliases = Array.isArray(char.aliases) ? char.aliases : [];
    for (const alias of aliases) {
      const key = String(alias || '').toLowerCase().trim();
      if (key) existingBookCharNames.add(key);
    }
  }

  const nameToCharIdMap = buildNameToIdMap(allBookChars);
  const closedWorldEnabled = catalog.characters.length > 0;

  for (const name of parsedCharacterNames) {
    const normalizedName = normalizeCharacterKey(name);
    if (!normalizedName) continue;
    if (isBlockedCharacterToken(normalizedName)) continue;
    if (nameToCharIdMap.has(normalizedName) || existingBookCharNames.has(normalizedName)) {
      continue;
    }
    if (closedWorldEnabled) continue;

    const baseNormalizedName = stripNumericSlugSuffix(normalizedName);
    if (baseNormalizedName && baseNormalizedName !== normalizedName && existingBookCharNames.has(baseNormalizedName)) {
      const canonical = allBookChars.find((character) => {
        const canonicalKey = String(character?.name || '').toLowerCase().trim();
        if (canonicalKey === baseNormalizedName) return true;
        const aliases = Array.isArray(character?.aliases) ? character.aliases : [];
        return aliases.some((alias: string) => String(alias || '').toLowerCase().trim() === baseNormalizedName);
      });

      if (canonical) {
        const aliases = Array.isArray(canonical.aliases) ? canonical.aliases : [];
        if (!aliases.some((alias: string) => String(alias || '').toLowerCase().trim() === normalizedName)) {
          canonical.aliases = [...aliases, name];
        }
        if (canonical.id) {
          nameToCharIdMap.set(normalizedName, canonical.id);
        }
        existingBookCharNames.add(normalizedName);
        continue;
      }
    }

    // ID is the lowercased name, made unique with the canonical
    // `-2`/`-3`… scheme (characterIdentity.buildUniqueCharacterId).
    const charId = buildUniqueCharacterId(nameToId(name), existingBookCharIds);
    existingBookCharIds.add(charId);
    nameToCharIdMap.set(normalizedName, charId);
    existingBookCharNames.add(normalizedName);

    allBookChars.push({
      guid: null,
      id: charId,
      name,
      gender: 'Unknown',
      aliases: [],
      color: null,
      notes: '',
      firstAppearance: chapterName,
      stats: {
        totalLines: 0,
        chapterCount: 0
      }
    });
  }

  return {
    characters: { formatVersion: catalog.formatVersion || '2.0', characters: allBookChars },
    nameToCharIdMap,
  };
}

/**
 * Convert the parser's script lines into the persisted DialogueLine shape
 * and stamp the returning flags.
 */
function buildDialogueLines(
  script: Line[],
  rawText: string,
  nameToCharIdMap: Map<string, string>,
  idToNameMap: Map<string, string>,
): DialogueLine[] {
  const baseDialogueLines = script.map((line, idx) =>
    convertLineToDialogueLine(line, idx + 1, nameToCharIdMap, idToNameMap, idx, script)
  );
  const returningFlags = computeReturningFlags(
    baseDialogueLines,
    rawText,
    (_line, index) => script[index]?.line_type === 'dialogue'
  );
  return baseDialogueLines.map((line, index) => ({
    ...line,
    isReturning: returningFlags[index],
  }));
}

/**
 * Assemble the persisted dialogue.json document for a chapter.
 */
function assembleDialogueJson(chapterName: string, dialogueLines: DialogueLine[]): DialogueJson {
  const characterBreakdown: Record<string, number> = {};
  for (const line of dialogueLines) {
    if (line.characterId) {
      characterBreakdown[line.characterId] = (characterBreakdown[line.characterId] || 0) + 1;
    }
  }
  return {
    formatVersion: '3.2',
    chapterId: chapterName,
    lines: dialogueLines,
    stats: {
      totalLines: dialogueLines.length,
      conflicts: dialogueLines.filter((line) => line.isConflict).length,
      characterBreakdown
    }
  };
}

export async function runParserForChapter(args: {
  input: string; // Path to chapter text file, e.g., "{chapterId}/chapter.txt" or "chapter.txt" (legacy)
  narrator?: string;
}): Promise<{ ok: boolean; chapter?: string; numLines?: number; numConflicts?: number; scriptPath?: string; error?: string }> {
  history.clear();

  try {
    const fileContent = await readChapterText(args.input);

    const hints = get(parserHints);
    const manualBlockList = Array.from(
      new Set([...(hints.manualBlockList || []), ...ALWAYS_BLOCKED_CHARACTER_NAMES])
    );
    // RESOLVED catalog (v3 characters/ folder, else the legacy
    // characters.json) — the parser consumes a resolved catalog, never a
    // raw path, when building its closed-world options.
    const bookCatalog = await resolveBookCatalog();
    const parserOptions = buildClosedWorldParserOptions(buildParserOptions(hints), bookCatalog);

    const parsed = await apiPostJson<ParserOutput>(API_ENDPOINTS.parse, {
      text: fileContent,
      filename: args.input,
      manual_blocklist: manualBlockList,
      parser_options: parserOptions,
    });
    const parsedCharacters = Array.isArray(parsed.characters) ? parsed.characters : [];
    if (!Array.isArray(parsed.characters)) {
      console.warn('[parser] Missing or invalid characters list in parser response');
    }

    // Surface the JEV verification summary (meta.jevVerify) to the chapter UI.
    // Defensive: older parses may not include it.
    setJevVerifySummary(parsed.meta?.jevVerify ?? null);

    const chapterName = extractChapterName(args.input);

    // Update the central characters.json FIRST (single source of truth for
    // v2 books). The resolved catalog carries formatVersion '3.0' only in
    // v3 folder mode, where the characters/ folder remains the sole source
    // of truth (IN-2) — the central file is left untouched.
    const { characters: bootstrapped, nameToCharIdMap } = bootstrapCharacters(bookCatalog, parsedCharacters, chapterName);
    if (bootstrapped.formatVersion !== '3.0') {
      await writeCentralCharacters('', bootstrapped);
    }
    const idToNameMap = buildIdToNameMap(bootstrapped.characters);

    const dialogueLines = buildDialogueLines(parsed.script, fileContent, nameToCharIdMap, idToNameMap);
    const dialogueJson = assembleDialogueJson(chapterName, dialogueLines);
    const numConflicts = dialogueJson.stats.conflicts;

    const dialoguePath = `${chapterName}/dialogue.json`;

    // Write the chapter dialogue.json file.
    const saved = await writeDialogue(dialoguePath, dialogueJson);
    if (!saved) {
      throw new Error(`Could not save parser output to ${dialoguePath}`);
    }

    return {
      ok: true,
      chapter: chapterName,
      numLines: dialogueLines.length,
      numConflicts,
      scriptPath: dialoguePath,
    };

  } catch (e) {
    const apiError = toApiClientError(e);
    console.error('[parser] Failed', apiError);
    return { ok: false, error: `[${apiError.type}] ${apiError.message}` };
  }
}

function buildParserOptions(hints: ParserHints) {
  const protagonistNames = (hints.protagonistNames && hints.protagonistNames.length > 0)
    ? hints.protagonistNames
    : (hints.protagonistName ? [hints.protagonistName] : []);

  const heuristics = {
    protagonist_first_person_tag: hints.heuristics?.protagonistFirstPersonTag ?? true,
    narrator_identity: hints.heuristics?.narratorIdentity ?? true,
    coreference: hints.heuristics?.coreference ?? true,
    explicit_tags: hints.heuristics?.explicitTags ?? true,
    tag_continuation: hints.heuristics?.tagContinuation ?? true,
    contiguous_dialogue: hints.heuristics?.contiguousDialogue ?? true,
    carry_across_short_narration: hints.heuristics?.carryAcrossShortNarration ?? true,
    suggest_alternatives: hints.heuristics?.suggestAlternatives ?? true,
    first_person_override: hints.heuristics?.firstPersonOverride ?? true,
    narrator_fallback: hints.heuristics?.narratorFallback ?? true,
    vocative_guard: hints.heuristics?.vocativeGuard ?? true,
  };

  return {
    parser_backend: hints.parserBackend || 'legacy',
    pov_mode: hints.povMode || 'first_person',
    protagonists: protagonistNames,
    learn_verbs: hints.learnVerbs ?? false,
    heuristics,
  };
}

