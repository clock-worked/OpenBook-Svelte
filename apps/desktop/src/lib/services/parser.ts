import { get } from 'svelte/store';
import { parserHints } from '$lib/stores/settings';
import type { Line, DialogueJson, DialogueLine, Character, LineCandidate, ParserHints } from '$lib/types';
import { readTextFile, getRootDirHandle, readCentralCharacters, writeCentralCharacters } from '$lib/services/fs';
import { buildNameToIdMap } from '$lib/stores/characters';
import { API_ENDPOINTS, apiPostJson, apiRequestJson, toApiClientError } from '$lib/services/apiClient';

const ALWAYS_BLOCKED_CHARACTER_NAMES = new Set(['he', 'she', 'as']);

interface ParserOutput {
  script: Line[];
  characters: string[];
  meta: { version: string };
}

/**
 * Convert character name to ID (lowercase, normalized)
 */
function nameToId(name: string): string {
  return name.toLowerCase().trim();
}

function normalizeCharacterToken(name: string): string {
  return String(name || '').toLowerCase().trim();
}

function isBlockedCharacterToken(name: string): boolean {
  return ALWAYS_BLOCKED_CHARACTER_NAMES.has(normalizeCharacterToken(name));
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
 * Convert Line format to DialogueLine format (v2.0)
 */
function convertLineToDialogueLine(line: Line, id: number, nameToCharIdMap: Map<string, string>, index: number, allLines: Line[]): DialogueLine {
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

  // Create candidates from suggestions (convert names to character IDs) - default to empty string if not found
  const sourceSuggestions = !isNarration
    ? (() => {
      const fromParser = Array.isArray(line.suggestions)
        ? line.suggestions.map((name) => String(name || '').trim()).filter(Boolean)
        : [];
      if (fromParser.length > 0) return fromParser;
      const fallbackSpeaker = String(line.speaker || '').trim();
      return fallbackSpeaker ? [fallbackSpeaker] : [];
    })()
      .filter((name) => !isBlockedCharacterToken(name))
    : [];

  const baseConfidence = estimateDialogueConfidence(line, index, allLines, sourceSuggestions);

  const hasNamedCandidates = sourceSuggestions.some((name) => {
    const candidateKey = String(name || '').toLowerCase().trim();
    return !!candidateKey && !!nameToCharIdMap.get(candidateKey);
  });

  const shouldAbstain = !isNarration && (
    line.is_suggestion === true
    || baseConfidence < 0.74
    || (sourceSuggestions.length > 1 && baseConfidence < 0.86)
    || !hasNamedCandidates
    || speakerIsBlocked
  );

  const characterId = isNarration
    ? 'narrator'
    : (shouldAbstain
      ? null
      : (speakerKey ? (nameToCharIdMap.get(speakerKey) || null) : null));

  if (!isNarration && !shouldAbstain && line.speaker && !nameToCharIdMap.get(speakerKey)) {
    console.warn(`[parser] Speaker "${line.speaker}" not found in characters.json, leaving unknown`);
  }

  const candidates: LineCandidate[] = sourceSuggestions.length > 0
    ? sourceSuggestions.map((name, idx) => {
      const candidateKey = String(name).toLowerCase().trim();
      const candidateId = nameToCharIdMap.get(candidateKey) || '';
      if (name && !nameToCharIdMap.get(candidateKey)) {
        console.warn(`[parser] Candidate "${name}" not found in characters.json`);
      }
      return {
        characterId: candidateId,
        confidence: Math.max(0.15, baseConfidence - (idx * 0.1))
      };
    })
    : [];

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
      : undefined
  };
}

/**
 * Write DialogueJson to file (v2.0 format)
 */
async function writeDialogue(path: string, data: DialogueJson): Promise<boolean> {
  const rootHandle = getRootDirHandle();
  if (!rootHandle) return false;
  try {
    const pathParts = path.split('/').filter(p => p);
    let currentHandle: FileSystemDirectoryHandle | FileSystemFileHandle = rootHandle;
    for (let i = 0; i < pathParts.length; i++) {
      const part = pathParts[i];
      if (i < pathParts.length - 1) {
        currentHandle = await (currentHandle as FileSystemDirectoryHandle).getDirectoryHandle(part, { create: true });
      } else {
        const fileHandle = await (currentHandle as FileSystemDirectoryHandle).getFileHandle(part, { create: true });
        const writable = await fileHandle.createWritable();
        await writable.write(JSON.stringify(data, null, 2));
        await writable.close();
      }
    }
    return true;
  } catch (e) {
    console.error(`Failed to write dialogue to ${path}`, e);
    return false;
  }
}

export async function runParserForChapter(args: {
  input: string; // Path to chapter text file, e.g., "{chapterId}/chapter.txt" or "chapter.txt" (legacy)
  narrator?: string;
}): Promise<{ ok: boolean; chapter?: string; numLines?: number; numConflicts?: number; scriptPath?: string; error?: string }> {

  try {
    const rootHandle = getRootDirHandle();
    if (!rootHandle) {
      throw new Error("No root directory selected");
    }

    const fileContent = await readTextFile(args.input);
    if (fileContent === null) {
      throw new Error(`Could not read file content from ${args.input}`);
    }

    const hints = get(parserHints);
    const manualBlockList = Array.from(
      new Set([...(hints.manualBlockList || []), ...ALWAYS_BLOCKED_CHARACTER_NAMES])
    );
    const parserOptions = buildParserOptions(hints);

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

    // Extract chapter name from path: "{chapterId}/chapter.txt" -> "{chapterId}"
    // Handle both old format ("chapter.txt") and new format ("{chapterId}/chapter.txt")
    const pathParts = args.input.split('/');
    let chapterName: string;
    if (pathParts.length > 1 && pathParts[pathParts.length - 1] === 'chapter.txt') {
      // New format: extract directory name
      chapterName = pathParts[pathParts.length - 2];
    } else {
      // Old format: extract from filename
      const inputFileName = pathParts[pathParts.length - 1];
      chapterName = inputFileName.replace(/\.txt$/, '');
    }

    // Update the central characters.json file FIRST (single source of truth)
    const root = ''; // Not used in v2.0 but required by function signature
    const existingBookChars = await readCentralCharacters(root) || { formatVersion: '2.0', characters: [] };
    const allBookChars = existingBookChars.characters.filter((character) => {
      return !isBlockedCharacterToken(character.id) && !isBlockedCharacterToken(character.name);
    });
    const existingBookCharNames = new Set(allBookChars.map(c => c.name.toLowerCase().trim()));
    const existingBookCharIds = new Set(allBookChars.map(c => c.id));

    for (const char of allBookChars) {
      const aliases = Array.isArray(char.aliases) ? char.aliases : [];
      for (const alias of aliases) {
        const key = String(alias || '').toLowerCase().trim();
        if (key) existingBookCharNames.add(key);
      }
    }

    // Create name to character ID map for dialogue conversion
    // Use centralized helper to build the map from existing characters
    const nameToCharIdMap = buildNameToIdMap(allBookChars);

    // Add new characters with proper IDs
    for (const name of parsedCharacters) {
      const normalizedName = String(name || '').toLowerCase().trim();
      if (!normalizedName) continue;
      if (isBlockedCharacterToken(normalizedName)) continue;
      if (nameToCharIdMap.has(normalizedName) || existingBookCharNames.has(normalizedName)) {
        continue;
      }

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

      // Generate ID from name (lowercase)
      let charId = nameToId(name);
      // Ensure ID is unique
      let counter = 1;
      while (existingBookCharIds.has(charId)) {
        charId = `${nameToId(name)}_${counter}`;
        counter++;
      }
      existingBookCharIds.add(charId);
      nameToCharIdMap.set(normalizedName, charId);
      existingBookCharNames.add(normalizedName);

      allBookChars.push({
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

    // Write updated central characters.json
    await writeCentralCharacters(root, { formatVersion: existingBookChars.formatVersion || '2.0', characters: allBookChars });

    // Convert lines to DialogueLine format (v2.0)
    const dialogueLines = parsed.script.map((line, idx) =>
      convertLineToDialogueLine(line, idx + 1, nameToCharIdMap, idx, parsed.script)
    );

    // Calculate character breakdown for stats
    const characterBreakdown: Record<string, number> = {};
    for (const line of dialogueLines) {
      if (line.characterId) {
        characterBreakdown[line.characterId] = (characterBreakdown[line.characterId] || 0) + 1;
      }
    }

    const numConflicts = dialogueLines.filter(line => line.isConflict).length;

    const dialogueJson: DialogueJson = {
      formatVersion: '3.0',
      chapterId: chapterName,
      lines: dialogueLines,
      stats: {
        totalLines: dialogueLines.length,
        conflicts: numConflicts,
        characterBreakdown
      }
    };

    const dialoguePath = `${chapterName}/dialogue.json`;

    // Write the dialogue.json file (v2.0 format)
    await writeDialogue(dialoguePath, dialogueJson);

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
    pov_mode: hints.povMode || 'first_person',
    protagonists: protagonistNames,
    learn_verbs: hints.learnVerbs ?? false,
    heuristics,
  };
}


