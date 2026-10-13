import type { ChapterStatus } from '$lib/stores/bookState';
import type { DialogueLine } from '$lib/types';
import type { GenerateAudioCharacterOptions } from '$lib/services/audio';

/**
 * Audio-generation planning for the AudioPanel.
 *
 * The panel used to own three structurally identical planners (whole-book
 * "generate missing", advanced preview, advanced queue) plus the queued-job
 * payload rebuild. They all share the same steps: read each chapter's
 * dialogue, group lines by character, check which lines already have audio,
 * and assemble jobs from what is missing. The panel now supplies the
 * environment (script reads, audio-existence checks, voice lookup) through
 * {@link AudioPlannerContext} / {@link RebuildPayloadDeps} and calls into
 * these planners.
 */

// === Shared types ===

/**
 * A dialogue line extended with the queue context the planners attach
 * (chapter/source/character). The extra fields are optional so plain
 * `DialogueLine[]` (e.g. from the single-chapter enqueue path) stays
 * assignable.
 */
export type PlannedDialogueLine = DialogueLine & {
  chapterTitle?: string;
  sourceFile?: string;
  characterName?: string;
  chosenSpeaker?: string;
};

export interface PlannerVoiceInfo {
  voiceId: string;
  provider: string;
  displayName: string;
  characterId: string;
}

export interface PlannedChapterBatch {
  chapterTitle: string;
  sourceFile: string;
  characterId: string;
  characterName: string;
  voiceInfo: PlannerVoiceInfo;
  lines: PlannedDialogueLine[];
  totalCharacters: number;
}

export interface PlannedCharacterBatch {
  characterId: string;
  characterName: string;
  voiceInfo: PlannerVoiceInfo;
  lines: PlannedDialogueLine[];
  totalCharacters: number;
  chapterCount: number;
  chapterBatchCount: number;
  underTargetChapterBatchCount: number;
}

export interface PlannedSpeakerSourceBatch {
  speakerSourceId: string;
  speakerLabel: string;
  voiceInfo: PlannerVoiceInfo;
  lines: PlannedDialogueLine[];
  totalCharacters: number;
  characterCount: number;
  chapterCount: number;
  primaryCharacterId: string;
  primaryCharacterName: string;
}

export interface PlannedChapterOverview {
  chapterTitle: string;
  missingVoiceTotal: number;
}

export interface AdvancedBookPreview {
  totalChapterCount: number;
  parsedChapterCount: number;
  skippedUnparsedCount: number;
  totalMissingLines: number;
  missingVoiceLineCount: number;
  missingVoiceCharacterCount: number;
  characters: PlannedCharacterBatch[];
  chapterBatches: PlannedChapterBatch[];
  chapterOverview: PlannedChapterOverview[];
}

/**
 * A fully planned queue job: the payload the panel hands to
 * `generateAudioForCharacter`, plus the identity the panel uses to build
 * the queue job id (`jobIdBase`) and the display name for the queue row
 * (`displayName`).
 */
export interface PlannedQueueJob {
  jobIdBase: string;
  displayName: string;
  characterId: string;
  characterName: string;
  lines: PlannedDialogueLine[];
  cumulativeCharacterCounts: number[];
  totalCharacters: number;
  voiceInfo: PlannerVoiceInfo;
  chapterTitle: string;
  sourceFile: string;
  generationOptions?: GenerateAudioCharacterOptions;
}

// === Planner context ===

export interface AudioPlannerContext {
  /** All chapters of the book (advanced planning filters to `parsed`). */
  chapters: ChapterStatus[];
  /** Character-count target used to flag under-target chapter batches. */
  batchTarget: number;
  /** Reads a chapter's dialogue document (or null when absent). */
  readScriptForChapter: (chapterTitle: string) => Promise<{ lines: any[] } | null>;
  /** Checks whether audio already exists for a line. */
  isLineAudioPresent: (args: { chapterTitle: string; characterName: string; lineId: number }) => Promise<boolean>;
  /** Maps a line to a character ID (v2.0 characterId / v1.0 chosenSpeaker). */
  resolveCharacterIdFromLine: (line: any) => string | null;
  /** Resolves a display name for a character ID (falls back to the ID). */
  resolveCharacterName: (characterId: string) => string;
  /** Voice assignment for a character, or null when unassigned. */
  getVoice: (characterId: string) => PlannerVoiceInfo | null;
  /** Source-file path for a chapter. */
  getChapterSourceFile: (chapter: { title: string; path?: string }) => string;
}

// === Line helpers ===

export function countLineCharacters(line: DialogueLine): number {
  return Math.max(1, String((line as any)?.text ?? '').length);
}

export function sumLineCharacters(lines: DialogueLine[]): number {
  return lines.reduce((sum, line) => sum + countLineCharacters(line), 0);
}

export function buildCumulativeCharacterCounts(lines: DialogueLine[]): { cumulative: number[]; total: number } {
  const cumulative: number[] = [];
  let runningTotal = 0;

  for (const line of lines) {
    runningTotal += countLineCharacters(line);
    cumulative.push(runningTotal);
  }

  return { cumulative, total: runningTotal };
}

/**
 * Copies lines and stamps them with queue context (chapter, source file,
 * character name), filling `chosenSpeaker` when the line has none.
 */
export function buildPlannedLinesWithContext(
  lines: DialogueLine[],
  chapterTitle: string,
  sourceFile: string,
  characterName: string,
): PlannedDialogueLine[] {
  return lines.map((line) => ({
    ...(line as any),
    chapterTitle,
    sourceFile,
    characterName,
    ...(typeof (line as any)?.chosenSpeaker === 'string' && String((line as any).chosenSpeaker).trim()
      ? {}
      : { chosenSpeaker: characterName }),
  }));
}

export function getSpeakerSourceId(voiceInfo: PlannerVoiceInfo): string {
  return `${voiceInfo.provider}::${voiceInfo.voiceId}`;
}

/**
 * Groups character batches by the underlying voice (provider + voice id)
 * so a single speaker source can serve many characters in one batch.
 */
export function buildSpeakerSourceBatches(items: PlannedCharacterBatch[]): PlannedSpeakerSourceBatch[] {
  const batchesBySource = new Map<
    string,
    {
      speakerSourceId: string;
      speakerLabel: string;
      voiceInfo: PlannerVoiceInfo;
      lines: PlannedDialogueLine[];
      totalCharacters: number;
      characterIds: Set<string>;
      chapterTitles: Set<string>;
      primaryCharacterId: string;
      primaryCharacterName: string;
    }
  >();

  for (const item of items) {
    const speakerSourceId = getSpeakerSourceId(item.voiceInfo);
    let batch = batchesBySource.get(speakerSourceId);
    if (!batch) {
      batch = {
        speakerSourceId,
        speakerLabel: item.voiceInfo.displayName,
        voiceInfo: item.voiceInfo,
        lines: [],
        totalCharacters: 0,
        characterIds: new Set<string>(),
        chapterTitles: new Set<string>(),
        primaryCharacterId: item.characterId,
        primaryCharacterName: item.characterName,
      };
      batchesBySource.set(speakerSourceId, batch);
    }

    batch.lines.push(...item.lines);
    batch.totalCharacters += item.totalCharacters;
    batch.characterIds.add(item.characterId);
    for (const line of item.lines) {
      const lineChapterTitle = resolvePlannedLineChapterTitle(line, item.characterName);
      if (lineChapterTitle) {
        batch.chapterTitles.add(lineChapterTitle);
      }
    }
  }

  const batches = Array.from(batchesBySource.values()).map((item) => ({
    speakerSourceId: item.speakerSourceId,
    speakerLabel: item.speakerLabel,
    voiceInfo: item.voiceInfo,
    lines: [...item.lines].sort((left, right) => {
      const leftChapter = resolvePlannedLineChapterTitle(left, item.primaryCharacterName);
      const rightChapter = resolvePlannedLineChapterTitle(right, item.primaryCharacterName);
      const chapterCompare = leftChapter.localeCompare(rightChapter, undefined, { sensitivity: 'base' });
      if (chapterCompare !== 0) return chapterCompare;
      return Number((left as any)?.id || 0) - Number((right as any)?.id || 0);
    }),
    totalCharacters: item.totalCharacters,
    characterCount: item.characterIds.size,
    chapterCount: item.chapterTitles.size,
    primaryCharacterId: item.primaryCharacterId,
    primaryCharacterName: item.primaryCharacterName,
  }));

  batches.sort((left, right) => {
    if (right.totalCharacters !== left.totalCharacters) {
      return right.totalCharacters - left.totalCharacters;
    }
    return left.speakerLabel.localeCompare(right.speakerLabel, undefined, { sensitivity: 'base' });
  });

  return batches;
}

// === Line resolvers ===

export function resolvePlannedLineChapterTitle(
  line: PlannedDialogueLine | undefined,
  fallbackChapterTitle: string,
): string {
  if (typeof line?.chapterTitle === 'string' && line.chapterTitle.trim()) {
    return line.chapterTitle.trim();
  }
  return fallbackChapterTitle;
}

export function resolvePlannedLineSourceFile(
  line: PlannedDialogueLine | undefined,
  fallbackSourceFile: string,
): string {
  if (typeof line?.sourceFile === 'string' && line.sourceFile.trim()) {
    return line.sourceFile.trim();
  }
  return fallbackSourceFile;
}

export function resolvePlannedLineCharacterName(
  line: PlannedDialogueLine | undefined,
  fallbackCharacterName = '',
): string {
  if (typeof line?.characterName === 'string' && line.characterName.trim()) {
    return line.characterName.trim();
  }
  if (typeof line?.chosenSpeaker === 'string' && line.chosenSpeaker.trim()) {
    return line.chosenSpeaker.trim();
  }
  return fallbackCharacterName;
}

// === Planners ===

interface ChapterCharacterMissing {
  characterId: string;
  characterName: string;
  missingLines: DialogueLine[];
}

/**
 * Reads one chapter's dialogue, groups lines by character, and returns the
 * per-character missing lines (audio not yet present). Shared by the
 * whole-book and advanced planners.
 */
async function collectChapterMissingLines(
  ctx: AudioPlannerContext,
  chapter: ChapterStatus,
): Promise<ChapterCharacterMissing[]> {
  const script = await ctx.readScriptForChapter(chapter.title);
  if (!script?.lines?.length) return [];

  const linesByCharacterId = new Map<string, DialogueLine[]>();
  for (const line of script.lines as any[]) {
    const characterId = ctx.resolveCharacterIdFromLine(line);
    if (!characterId) continue;

    const existingLines = linesByCharacterId.get(characterId) || [];
    existingLines.push(line as DialogueLine);
    linesByCharacterId.set(characterId, existingLines);
  }

  const result: ChapterCharacterMissing[] = [];
  for (const [characterId, characterLines] of linesByCharacterId.entries()) {
    if (characterLines.length === 0) continue;

    const characterName = ctx.resolveCharacterName(characterId);
    const checks = await Promise.all(
      characterLines.map(async (line) => ({
        line,
        exists: await ctx.isLineAudioPresent({ chapterTitle: chapter.title, characterName, lineId: line.id }),
      }))
    );

    const missingLines = checks.filter((check) => !check.exists).map((check) => check.line);
    if (missingLines.length === 0) continue;

    result.push({ characterId, characterName, missingLines });
  }
  return result;
}

export interface ChapterQueueTotals {
  queuedCharacterBatches: number;
  queuedLines: number;
  missingVoiceLines: number;
}

export interface WholeBookMissingPlan {
  /** One job per voiced character with missing lines (batched across chapters). */
  jobs: PlannedQueueJob[];
  /** Per-chapter totals (only chapters with queued or missing-voice lines). */
  chapterTotals: Map<string, ChapterQueueTotals>;
  missingVoiceCharacterIds: Set<string>;
  totalLinesQueued: number;
}

/**
 * Plans "generate missing audio across the whole book": one job per voiced
 * character, batching that character's missing lines across chapters.
 */
export async function planWholeBookMissing(ctx: AudioPlannerContext): Promise<WholeBookMissingPlan> {
  const missingVoiceCharacterIds = new Set<string>();
  const jobsByCharacterId = new Map<string, PlannedQueueJob>();
  const chapterTotals = new Map<string, ChapterQueueTotals>();
  let totalLinesQueued = 0;

  for (const chapter of ctx.chapters) {
    let chapterTotalsEntry: ChapterQueueTotals | undefined;
    const getTotals = (): ChapterQueueTotals => {
      if (!chapterTotalsEntry) {
        chapterTotalsEntry = { queuedCharacterBatches: 0, queuedLines: 0, missingVoiceLines: 0 };
        chapterTotals.set(chapter.title, chapterTotalsEntry);
      }
      return chapterTotalsEntry;
    };

    for (const { characterId, characterName, missingLines } of await collectChapterMissingLines(ctx, chapter)) {
      const voiceInfo = ctx.getVoice(characterId);
      if (!voiceInfo) {
        missingVoiceCharacterIds.add(characterId);
        getTotals().missingVoiceLines += missingLines.length;
        continue;
      }

      const sourceFile = ctx.getChapterSourceFile(chapter);
      const linesWithContext = buildPlannedLinesWithContext(missingLines, chapter.title, sourceFile, characterName);

      let job = jobsByCharacterId.get(characterId);
      if (!job) {
        job = {
          jobIdBase: `book-${characterId}-missing`,
          displayName: characterName,
          characterId,
          characterName,
          lines: [],
          cumulativeCharacterCounts: [],
          totalCharacters: 0,
          voiceInfo,
          chapterTitle: chapter.title,
          sourceFile,
        };
        jobsByCharacterId.set(characterId, job);
      }

      job.lines.push(...linesWithContext);

      totalLinesQueued += linesWithContext.length;
      getTotals().queuedCharacterBatches += 1;
      getTotals().queuedLines += linesWithContext.length;
    }
  }

  const jobs = Array.from(jobsByCharacterId.values()).map((job) => {
    const { cumulative, total } = buildCumulativeCharacterCounts(job.lines);
    job.cumulativeCharacterCounts = cumulative;
    job.totalCharacters = total;
    return job;
  });

  return { jobs, chapterTotals, missingVoiceCharacterIds, totalLinesQueued };
}

/**
 * Plans the advanced-generation preview: per-character and per-chapter
 * batches of missing lines across all parsed chapters, plus the
 * per-chapter missing-voice overview.
 */
export async function planAdvancedPreview(ctx: AudioPlannerContext): Promise<AdvancedBookPreview> {
  const allChapters = ctx.chapters;
  const parsedChapters = allChapters.filter((chapter) => chapter.parsed);

  const charactersById = new Map<string, {
    characterId: string;
    characterName: string;
    voiceInfo: PlannerVoiceInfo;
    lines: PlannedDialogueLine[];
    totalCharacters: number;
    chapterBatchCount: number;
    underTargetChapterBatchCount: number;
    chapters: Set<string>;
  }>();
  const chapterBatches: PlannedChapterBatch[] = [];
  const chapterOverview = new Map<string, PlannedChapterOverview>();
  const missingVoiceCharacterIds = new Set<string>();
  let totalMissingLines = 0;
  let missingVoiceLineCount = 0;

  for (const chapter of parsedChapters) {
    let chapterMissingVoiceTotal = 0;

    for (const { characterId, characterName, missingLines } of await collectChapterMissingLines(ctx, chapter)) {
      const voiceInfo = ctx.getVoice(characterId);
      if (!voiceInfo) {
        missingVoiceCharacterIds.add(characterId);
        chapterMissingVoiceTotal += missingLines.length;
        missingVoiceLineCount += missingLines.length;
        continue;
      }

      const sourceFile = ctx.getChapterSourceFile(chapter);
      const linesWithContext = buildPlannedLinesWithContext(missingLines, chapter.title, sourceFile, characterName);
      const totalCharacters = sumLineCharacters(linesWithContext);

      chapterBatches.push({
        chapterTitle: chapter.title,
        sourceFile,
        characterId,
        characterName,
        voiceInfo,
        lines: linesWithContext,
        totalCharacters,
      });

      totalMissingLines += linesWithContext.length;

      let candidate = charactersById.get(characterId);
      if (!candidate) {
        candidate = {
          characterId,
          characterName,
          voiceInfo,
          lines: [],
          totalCharacters: 0,
          chapterBatchCount: 0,
          underTargetChapterBatchCount: 0,
          chapters: new Set<string>(),
        };
        charactersById.set(characterId, candidate);
      }

      candidate.lines.push(...linesWithContext);
      candidate.totalCharacters += totalCharacters;
      candidate.chapterBatchCount += 1;
      if (totalCharacters < ctx.batchTarget) {
        candidate.underTargetChapterBatchCount += 1;
      }
      candidate.chapters.add(chapter.title);
    }

    if (chapterMissingVoiceTotal > 0) {
      chapterOverview.set(chapter.title, {
        chapterTitle: chapter.title,
        missingVoiceTotal: chapterMissingVoiceTotal,
      });
    }
  }

  const characters = Array.from(charactersById.values())
    .map((candidate) => ({
      characterId: candidate.characterId,
      characterName: candidate.characterName,
      voiceInfo: candidate.voiceInfo,
      lines: candidate.lines,
      totalCharacters: candidate.totalCharacters,
      chapterCount: candidate.chapters.size,
      chapterBatchCount: candidate.chapterBatchCount,
      underTargetChapterBatchCount: candidate.underTargetChapterBatchCount,
    }))
    .sort((left, right) => {
      if (right.totalCharacters !== left.totalCharacters) {
        return right.totalCharacters - left.totalCharacters;
      }
      if (right.lines.length !== left.lines.length) {
        return right.lines.length - left.lines.length;
      }
      return left.characterName.localeCompare(right.characterName, undefined, { sensitivity: 'base' });
    });

  chapterBatches.sort((left, right) => {
    const chapterCompare = left.chapterTitle.localeCompare(right.chapterTitle, undefined, { sensitivity: 'base' });
    if (chapterCompare !== 0) return chapterCompare;
    return left.characterName.localeCompare(right.characterName, undefined, { sensitivity: 'base' });
  });

  return {
    totalChapterCount: allChapters.length,
    parsedChapterCount: parsedChapters.length,
    skippedUnparsedCount: Math.max(0, allChapters.length - parsedChapters.length),
    totalMissingLines,
    missingVoiceLineCount,
    missingVoiceCharacterCount: missingVoiceCharacterIds.size,
    characters,
    chapterBatches,
    chapterOverview: Array.from(chapterOverview.values()).sort((left, right) =>
      left.chapterTitle.localeCompare(right.chapterTitle, undefined, { sensitivity: 'base' })
    ),
  };
}

export type AdvancedGenerationMode = 'character' | 'chapter' | 'speaker-source';

export interface AdvancedGenerationPlan {
  jobs: PlannedQueueJob[];
  chapterProcessableTotals: Map<string, number>;
  jobsQueued: number;
  linesQueued: number;
}

/**
 * Turns a preview plus the selected characters into concrete queue jobs for
 * the chosen generation mode (per-character, per-chapter, or grouped by
 * speaker source).
 */
export function planAdvancedGeneration(
  preview: AdvancedBookPreview,
  options: {
    mode: AdvancedGenerationMode;
    selectedCharacterIds: Set<string>;
    generationOptions?: GenerateAudioCharacterOptions;
  },
): AdvancedGenerationPlan {
  const selectedIds = new Set(options.selectedCharacterIds);
  const jobs: PlannedQueueJob[] = [];
  const chapterProcessableTotals = new Map<string, number>();

  if (options.mode === 'character') {
    for (const batch of preview.characters) {
      if (!selectedIds.has(batch.characterId) || batch.lines.length === 0) continue;

      const firstLine = batch.lines[0];
      const chapterTitle = resolvePlannedLineChapterTitle(firstLine, batch.characterName);
      const sourceFile = resolvePlannedLineSourceFile(firstLine, `${chapterTitle}/chapter.txt`);
      const { cumulative, total } = buildCumulativeCharacterCounts(batch.lines);

      jobs.push({
        jobIdBase: `advanced-character-${batch.characterId}`,
        displayName: batch.characterName,
        characterId: batch.characterId,
        characterName: batch.characterName,
        lines: batch.lines,
        cumulativeCharacterCounts: cumulative,
        totalCharacters: total,
        voiceInfo: batch.voiceInfo,
        chapterTitle,
        sourceFile,
        generationOptions: options.generationOptions,
      });

      for (const line of batch.lines) {
        const lineChapterTitle = resolvePlannedLineChapterTitle(line, chapterTitle);
        chapterProcessableTotals.set(lineChapterTitle, (chapterProcessableTotals.get(lineChapterTitle) || 0) + 1);
      }
    }
  } else if (options.mode === 'chapter') {
    for (const batch of preview.chapterBatches) {
      if (!selectedIds.has(batch.characterId) || batch.lines.length === 0) continue;

      const { cumulative, total } = buildCumulativeCharacterCounts(batch.lines);

      jobs.push({
        jobIdBase: `advanced-chapter-${batch.chapterTitle}-${batch.characterId}`,
        displayName: batch.characterName,
        characterId: batch.characterId,
        characterName: batch.characterName,
        lines: batch.lines,
        cumulativeCharacterCounts: cumulative,
        totalCharacters: total,
        voiceInfo: batch.voiceInfo,
        chapterTitle: batch.chapterTitle,
        sourceFile: batch.sourceFile,
        generationOptions: options.generationOptions,
      });

      chapterProcessableTotals.set(batch.chapterTitle, (chapterProcessableTotals.get(batch.chapterTitle) || 0) + batch.lines.length);
    }
  } else {
    const selectedCharacters = preview.characters.filter((batch) => selectedIds.has(batch.characterId));
    for (const batch of buildSpeakerSourceBatches(selectedCharacters)) {
      if (batch.lines.length === 0) continue;

      const firstLine = batch.lines[0];
      const chapterTitle = resolvePlannedLineChapterTitle(firstLine, batch.primaryCharacterName);
      const sourceFile = resolvePlannedLineSourceFile(firstLine, `${chapterTitle}/chapter.txt`);
      const { cumulative, total } = buildCumulativeCharacterCounts(batch.lines);
      const speakerSourceId = batch.speakerSourceId.replace(/[^a-zA-Z0-9_-]/g, '_');

      jobs.push({
        jobIdBase: `advanced-speaker-source-${speakerSourceId}`,
        displayName: `${batch.speakerLabel} (speaker source)`,
        characterId: batch.primaryCharacterId,
        characterName: batch.primaryCharacterName,
        lines: batch.lines,
        cumulativeCharacterCounts: cumulative,
        totalCharacters: total,
        voiceInfo: batch.voiceInfo,
        chapterTitle,
        sourceFile,
        generationOptions: options.generationOptions,
      });

      for (const line of batch.lines) {
        const lineChapterTitle = resolvePlannedLineChapterTitle(line, chapterTitle);
        chapterProcessableTotals.set(lineChapterTitle, (chapterProcessableTotals.get(lineChapterTitle) || 0) + 1);
      }
    }
  }

  return {
    jobs,
    chapterProcessableTotals,
    jobsQueued: jobs.length,
    linesQueued: jobs.reduce((sum, job) => sum + job.lines.length, 0),
  };
}

// === Queue payload rebuild ===

export interface RebuildPayloadDeps {
  /** Chapter path for a title (undefined when the chapter is unknown). */
  resolveChapterPath: (chapterTitle: string) => string | undefined;
  /** Reads the chapter script, preferring an in-memory copy when the caller can. */
  resolveScript: (chapterTitle: string) => Promise<{ lines: any[] } | null>;
  /** Display name for a character ID. */
  resolveCharacterName: (characterId: string, fallbackName?: string) => string;
  /** Voice assignment for a character, or null when unassigned. */
  getVoice: (characterId: string) => PlannerVoiceInfo | null;
  /** Lines of one character within a script (v2.0 characterId / v1.0 chosenSpeaker). */
  filterCharacterLines: (characterId: string, characterName: string, script: any) => DialogueLine[];
  /** Checks whether audio already exists for a line. */
  isLineAudioPresent: (args: { chapterTitle: string; characterName: string; lineId: number }) => Promise<boolean>;
}

export interface RebuildPayloadArgs {
  id: string;
  characterId: string;
  characterName: string;
  chapterTitle: string;
  generationMode?: 'missing' | 'full';
}

export interface RebuiltQueuePayload {
  payload: PlannedQueueJob | null;
  markComplete?: boolean;
  error?: string;
}

/**
 * Rebuilds a queued job's payload from disk for a queue item that has no
 * in-memory payload (e.g. after a restart). `markComplete: true` means the
 * job is a no-op (no script / no lines / nothing missing); `error` means the
 * job should fail with that message.
 */
export async function rebuildQueuedJobPayload(
  deps: RebuildPayloadDeps,
  item: RebuildPayloadArgs,
): Promise<RebuiltQueuePayload> {
  const script = await deps.resolveScript(item.chapterTitle);
  if (!script?.lines?.length) {
    return { payload: null, markComplete: true };
  }

  const characterName = deps.resolveCharacterName(item.characterId, item.characterName || item.characterId);
  const voiceInfo = deps.getVoice(item.characterId);
  if (!voiceInfo) {
    return { payload: null, error: `No voice assigned to ${characterName}.` };
  }

  const linesForCharacter = deps.filterCharacterLines(item.characterId, characterName, script);
  if (linesForCharacter.length === 0) {
    return { payload: null, markComplete: true };
  }

  let linesToGenerate = linesForCharacter;
  if ((item.generationMode ?? 'missing') === 'missing') {
    const checks = await Promise.all(
      linesForCharacter.map(async (line) => ({
        line,
        exists: await deps.isLineAudioPresent({ chapterTitle: item.chapterTitle, characterName, lineId: line.id }),
      }))
    );
    linesToGenerate = checks.filter((check) => !check.exists).map((check) => check.line);
    if (linesToGenerate.length === 0) {
      return { payload: null, markComplete: true };
    }
  }

  const { cumulative, total } = buildCumulativeCharacterCounts(linesToGenerate);

  return {
    payload: {
      jobIdBase: `${item.characterId}-rebuild`,
      displayName: characterName,
      characterId: item.characterId,
      characterName,
      lines: linesToGenerate,
      cumulativeCharacterCounts: cumulative,
      totalCharacters: total,
      voiceInfo,
      chapterTitle: item.chapterTitle,
      sourceFile: deps.resolveChapterPath(item.chapterTitle) || `${item.chapterTitle}/chapter.txt`,
    },
  };
}
