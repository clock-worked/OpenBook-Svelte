import type { DialogueJson, ScriptJson } from '$lib/types';
import type { UnifiedLine } from '$lib/components/chapter/tools/types';

type ChapterLike = {
  title: string;
  path?: string;
};

type NormalizedScriptPayload = {
  lines: UnifiedLine[];
  stats: any;
  hadAliasChanges?: boolean;
};

type NormalizedAttributionFn = (
  existing: any,
  candidates: Array<{ name: string; characterId?: string | null; confidence: number }>,
  characterName: string | null,
  threshold: number,
  fallbackSourceAlias?: string | null,
  fallbackSourceCandidates?: string[],
  unknownSpeakerLabel?: string
) => any;

export async function loadChapterContent(params: {
  chapter: ChapterLike;
  root: string;
  unknownThreshold: number;
  unknownSpeakerLabel: string;
  readDialogueForChapter: (title: string) => Promise<DialogueJson | ScriptJson | null>;
  normalizeScriptData: (data: DialogueJson | ScriptJson | null, options: {
    root?: string;
    unknownThreshold: number;
    unknownSpeakerLabel?: string;
    readCentralCharacters: (root: string) => Promise<{ characters?: any[] } | null>;
    buildIdToNameMap: (characters: any[]) => Map<string, string>;
  }) => Promise<NormalizedScriptPayload | null>;
  readCentralCharacters: (root: string) => Promise<{ characters?: any[] } | null>;
  buildIdToNameMap: (characters: any[]) => Map<string, string>;
  readTextFile: (path: string) => Promise<string | null>;
}): Promise<{
  isV2Format: boolean;
  normalized: NormalizedScriptPayload | null;
  currentScript: ScriptJson | null;
  rawText: string | null;
}> {
  const { chapter, root } = params;

  const dialogue = await params.readDialogueForChapter(chapter.title);
  let isV2Format = false;
  let normalized: NormalizedScriptPayload | null = null;
  let currentScript: ScriptJson | null = null;

  if (dialogue) {
    isV2Format =
      'formatVersion' in dialogue &&
      (dialogue.formatVersion === '2.0' || dialogue.formatVersion === '3.0');

    normalized = await params.normalizeScriptData(dialogue, {
      root,
      unknownThreshold: params.unknownThreshold,
      unknownSpeakerLabel: params.unknownSpeakerLabel,
      readCentralCharacters: params.readCentralCharacters,
      buildIdToNameMap: params.buildIdToNameMap,
    });

    if (normalized) {
      currentScript = {
        chapter: chapter.title,
        sourceFile: chapter.path || '',
        lines: normalized.lines.map((line) => ({
          id: line.id,
          text: line.text,
          span: line.span,
          chosenSpeaker: line.characterName,
          candidates: line.candidates,
          isConflict: line.isConflict,
          attribution: line.attribution,
        })),
        stats: normalized.stats,
      };
    }
  }

  let rawText: string | null = null;
  if (chapter.path) {
    rawText = await params.readTextFile(chapter.path);
  }

  return {
    isV2Format,
    normalized,
    currentScript,
    rawText,
  };
}

export async function saveDialogueFromNormalized(params: {
  normalized: { lines: UnifiedLine[]; stats: any };
  chapter: ChapterLike;
  root: string;
  unknownThreshold: number;
  unknownSpeakerLabel: string;
  ensureCentralCharactersForNames: (names: string[]) => Promise<void>;
  ensureCharacterNameToIdMap: () => Promise<Map<string, string>>;
  normalizeAttribution: NormalizedAttributionFn;
  writeDialogue: (path: string, data: DialogueJson) => Promise<boolean>;
  getRootDirInfo: () => { name: string; hasHandle: boolean };
  getBackendRootAbsolutePath: () => string | null;
  apiSave: (relativePath: string, content: DialogueJson) => Promise<Response>;
}): Promise<void> {
  const scr = params.normalized;
  const ch = params.chapter;

  const rootInfo = params.getRootDirInfo();
  console.log('[ChapterView] Saving changes...', {
    format: 'v3.0',
    numLines: scr.lines.length,
    chapter: ch.title,
    root: params.root,
    rootDirHandle: rootInfo,
  });

  const allNames = new Set<string>();
  for (const line of scr.lines) {
    if (line.characterName) allNames.add(line.characterName);
    for (const candidate of line.candidates || []) {
      if (candidate?.name) allNames.add(candidate.name);
    }
  }

  await params.ensureCentralCharactersForNames(Array.from(allNames));
  const nameToIdMap = await params.ensureCharacterNameToIdMap();

  const characterBreakdown: Record<string, number> = {};
  for (const line of scr.lines) {
    if (!line.characterName) continue;
    const charId = nameToIdMap.get(line.characterName.toLowerCase());
    if (charId) {
      characterBreakdown[charId] = (characterBreakdown[charId] || 0) + 1;
    } else {
      console.warn(
        `[ChapterView] Character "${line.characterName}" not found in characters.json, skipping from breakdown`
      );
    }
  }

  const v2Dialogue: DialogueJson = {
    formatVersion: '3.0',
    chapterId: ch.title,
    lines: scr.lines.map((line) => {
      const attribution = params.normalizeAttribution(
        line.attribution || null,
        (line.candidates || []).map((candidate) => ({
          name: candidate.name,
          characterId: candidate.name ? (nameToIdMap.get(candidate.name.toLowerCase()) || null) : null,
          confidence: candidate.confidence,
        })),
        line.characterName,
        params.unknownThreshold,
        null,
        [],
        params.unknownSpeakerLabel
      );

      const nameLower = String(line.characterName || '').trim().toLowerCase();
      let characterId: string | null = null;
      if (attribution.resolutionStatus === 'unknown' || nameLower === params.unknownSpeakerLabel.toLowerCase()) {
        characterId = null;
      } else if (line.characterName) {
        characterId = nameToIdMap.get(line.characterName.toLowerCase()) || null;
        if (!characterId) {
          console.warn(
            `[ChapterView] Character "${line.characterName}" not found in characters.json for line ${line.id}, defaulting to narrator`
          );
          characterId = 'narrator';
        }
      }

      return {
        id: line.id,
        characterId,
        text: line.text,
        span: line.span || { start: 0, end: 0 },
        metadata: {
          emotion: null,
          intensity: 1.0,
          pacing: null,
          prefix: null,
          customTags: {
            attribution,
            sourceAlias: attribution.sourceAlias,
            sourceCandidates: attribution.sourceCandidates,
          },
        },
        candidates: line.candidates
          .map((candidate) => {
            const candidateId = candidate.name ? (nameToIdMap.get(candidate.name.toLowerCase()) || null) : null;
            if (candidate.name && !candidateId) {
              console.warn(`[ChapterView] Candidate "${candidate.name}" not found in characters.json`);
              return null;
            }
            if (!candidateId) return null;
            return {
              characterId: candidateId,
              confidence: candidate.confidence,
            };
          })
          .filter((candidate): candidate is { characterId: string; confidence: number } => !!candidate),
        isConflict: line.isConflict || attribution.resolutionStatus === 'unknown',
        attribution,
      };
    }),
    stats: {
      totalLines: scr.lines.length,
      conflicts: scr.stats?.numConflicts || 0,
      characterBreakdown,
    },
  };

  const relativePath = `${ch.title}/dialogue.json`;
  console.log('[ChapterView] Writing dialogue to relative path:', relativePath);

  const savedLocally = await params.writeDialogue(relativePath, v2Dialogue);
  if (savedLocally) {
    console.log('[ChapterView] ✓ Successfully saved dialogue.json via File System API');
    return;
  }

  const backendRoot = params.getBackendRootAbsolutePath();
  if (!backendRoot) {
    console.warn('[ChapterView] ✗ Save failed: no backend root path available');
    return;
  }

  try {
    const response = await params.apiSave(relativePath, v2Dialogue);
    if (response.ok) {
      console.log('[ChapterView] ✓ Successfully saved dialogue.json via API');
    } else {
      const errorData = await response.json();
      console.error('[ChapterView] ✗ Failed to save dialogue.json via API:', errorData.detail || response.statusText);
    }
  } catch (error) {
    console.error('[ChapterView] ✗ Network error saving dialogue.json via API:', error);
  }
}
