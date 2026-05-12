<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { currentChapter, bookRoot, currentScript, chapters } from '$lib/stores/bookState';
  import { characters, colorForCharacter, rgbaToOpaqueHex, buildNameToIdMap, buildIdToNameMap } from '$lib/stores/characters';
  import { voices } from '$lib/stores/speakers';
  import {
    checkAudioExistsForCharacter,
    checkAudioExistsForLine,
    generateAudioForLine,
    generateAudioForCharacter,
    readManifest,
    deleteAudioLineForCharacter,
    reconcileAudioManifestForCharacter,
  } from '$lib/services/audio';
  import type { GenerateAudioCharacterOptions } from '$lib/services/audio';
  import { Headphones, Loader, Scissors, FileText, BookOpen, X } from 'lucide-svelte';
  import type { DialogueLine, Character } from '$lib/types';
  import { audioGenerateLineId } from '$lib/stores/selection';
  import { audioUpdateTrigger, triggerAudioUpdate } from '$lib/stores/audioUpdates';
  import { readCentralCharacters, readDialogueForChapter } from '$lib/services/fs';
  import {
    enqueueAudioJob,
    getNextQueuedJob,
    isJobCanceled,
    requeueRunningJobs,
    markJobCanceled,
    markJobCompleted,
    markJobFailed,
    markJobRunning,
    setQueueChapterOverview,
    updateJobProgress,
    type AudioQueueItem,
  } from '$lib/stores/audioQueue';

  const DISPLAY_ALPHA = 1.0;
  const CHARACTER_BATCH_TARGET = 800;
  const DEFAULT_ADVANCED_FILLER_TEXT = 'The following lines are all voiced by the same speaker source. Keep the same voice, pacing, and emotional continuity across the batch.';

  type VoiceInfo = {
    voiceId: string;
    provider: string;
    displayName: string;
    characterId: string;
  };

  let generatingCharacter: string | null = null;
  let generationProgress = { current: 0, total: 0 };
  let showOverwriteModal = false;
  let overwriteCharacterId: string | null = null;
  let overwriteClipCount = 0;
  let pruningStale = false;
  let audioClipCounts = new Map<string, number>();
  let queueProcessing = false;
  type QueuedDialogueLine = DialogueLine & {
    chapterTitle?: string;
    sourceFile?: string;
    __queueChapterTitle?: string;
    __queueSourceFile?: string;
  };
  type QueuedJobPayload = {
    characterId: string;
    characterName: string;
    lines: QueuedDialogueLine[];
    cumulativeCharacterCounts: number[];
    totalCharacters: number;
    voiceInfo: VoiceInfo;
    chapterTitle: string;
    sourceFile: string;
    generationOptions?: GenerateAudioCharacterOptions;
  };

  type AdvancedGenerationMode = 'character' | 'chapter' | 'speaker-source';

  type AdvancedBookChapterBatch = {
    chapterTitle: string;
    sourceFile: string;
    characterId: string;
    characterName: string;
    voiceInfo: VoiceInfo;
    lines: QueuedDialogueLine[];
    totalCharacters: number;
  };

  type AdvancedBookCharacterBatch = {
    characterId: string;
    characterName: string;
    voiceInfo: VoiceInfo;
    lines: QueuedDialogueLine[];
    totalCharacters: number;
    chapterCount: number;
    chapterBatchCount: number;
    underTargetChapterBatchCount: number;
  };

  type AdvancedSpeakerSourceBatch = {
    speakerSourceId: string;
    speakerLabel: string;
    voiceInfo: VoiceInfo;
    lines: QueuedDialogueLine[];
    totalCharacters: number;
    characterCount: number;
    chapterCount: number;
    primaryCharacterId: string;
    primaryCharacterName: string;
  };

  type AdvancedChapterOverview = {
    chapterTitle: string;
    missingVoiceTotal: number;
  };

  type AdvancedBookPreview = {
    totalChapterCount: number;
    parsedChapterCount: number;
    skippedUnparsedCount: number;
    totalMissingLines: number;
    missingVoiceLineCount: number;
    missingVoiceCharacterCount: number;
    characters: AdvancedBookCharacterBatch[];
    chapterBatches: AdvancedBookChapterBatch[];
    chapterOverview: AdvancedChapterOverview[];
  };

  const queuedJobs = new Map<string, QueuedJobPayload>();
  let showAdvancedOverlay = false;
  let loadingAdvancedPreview = false;
  let advancedSubmitting = false;
  let advancedPreviewError: string | null = null;
  let advancedPreview: AdvancedBookPreview | null = null;
  let advancedGenerationMode: AdvancedGenerationMode = 'character';
  let advancedUseFillerForShortBatch = false;
  let advancedFillerText = DEFAULT_ADVANCED_FILLER_TEXT;
  let selectedAdvancedCharacterIds = new Set<string>();
  let advancedPreviewRequestId = 0;
  let advancedSelectAllInput: HTMLInputElement | null = null;

  function countLineCharacters(line: DialogueLine): number {
    return Math.max(1, String((line as any)?.text ?? '').length);
  }

  function sumLineCharacters(lines: DialogueLine[]): number {
    return lines.reduce((sum, line) => sum + countLineCharacters(line), 0);
  }

  function buildCumulativeCharacterCounts(lines: DialogueLine[]): { cumulative: number[]; total: number } {
    const cumulative: number[] = [];
    let runningTotal = 0;

    for (const line of lines) {
      runningTotal += countLineCharacters(line);
      cumulative.push(runningTotal);
    }

    return { cumulative, total: runningTotal };
  }

  // Maps: ID → name, name → ID (for legacy data), ID → Character
  let characterIdToName = new Map<string, string>();
  let characterNameToId = new Map<string, string>();
  let characterIdToData = new Map<string, Character>();

  function resolveCharacterIdFromLine(line: any): string | null {
    if ('characterId' in line && line.characterId) {
      return String(line.characterId);
    }

    if ('chosenSpeaker' in line && line.chosenSpeaker) {
      return characterNameToId.get(String(line.chosenSpeaker).toLowerCase()) || null;
    }

    return null;
  }

  function getChapterSourceFile(chapter: { title: string; path?: string }): string {
    return chapter.path || `${chapter.title}/chapter.txt`;
  }

  function buildQueuedLinesWithContext(
    lines: DialogueLine[],
    chapterTitle: string,
    sourceFile: string,
    characterName: string,
  ): QueuedDialogueLine[] {
    return lines.map((line) => ({
      ...(line as any),
      __queueChapterTitle: chapterTitle,
      __queueSourceFile: sourceFile,
      __queueCharacterName: characterName,
      ...(typeof (line as any)?.chosenSpeaker === 'string' && String((line as any).chosenSpeaker).trim()
        ? {}
        : { chosenSpeaker: characterName }),
    }));
  }

  function getSpeakerSourceId(voiceInfo: VoiceInfo): string {
    return `${voiceInfo.provider}::${voiceInfo.voiceId}`;
  }

  function buildSpeakerSourceBatches(items: AdvancedBookCharacterBatch[]): AdvancedSpeakerSourceBatch[] {
    const batchesBySource = new Map<
      string,
      {
        speakerSourceId: string;
        speakerLabel: string;
        voiceInfo: VoiceInfo;
        lines: QueuedDialogueLine[];
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
        const lineChapterTitle = resolveQueuedLineChapterTitle(line, item.characterName);
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
        const leftChapter = resolveQueuedLineChapterTitle(left, item.primaryCharacterName);
        const rightChapter = resolveQueuedLineChapterTitle(right, item.primaryCharacterName);
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

  function resolveQueuedLineChapterTitle(line: QueuedDialogueLine | undefined, fallbackChapterTitle: string): string {
    if (typeof (line as any)?.chapterTitle === 'string' && String((line as any).chapterTitle).trim()) {
      return String((line as any).chapterTitle).trim();
    }

    if (typeof line?.__queueChapterTitle === 'string' && String(line.__queueChapterTitle).trim()) {
      return String(line.__queueChapterTitle).trim();
    }

    return fallbackChapterTitle;
  }

  function resolveQueuedLineSourceFile(line: QueuedDialogueLine | undefined, fallbackSourceFile: string): string {
    if (typeof (line as any)?.sourceFile === 'string' && String((line as any).sourceFile).trim()) {
      return String((line as any).sourceFile).trim();
    }

    if (typeof line?.__queueSourceFile === 'string' && String(line.__queueSourceFile).trim()) {
      return String(line.__queueSourceFile).trim();
    }

    return fallbackSourceFile;
  }
  
  // Load character data from characters.json (ID-first approach)
  $: if ($currentChapter && $bookRoot) {
    void loadCharacterData();
  }
  
  async function loadCharacterData() {
    const root = get(bookRoot);
    if (!root) return;
    
    try {
      const charactersData = await readCentralCharacters(root);
      if (!charactersData?.characters) return;
      
      // Use centralized helpers to build maps
      characterIdToName = buildIdToNameMap(charactersData.characters);
      characterNameToId = buildNameToIdMap(charactersData.characters);
      
      // Build ID to Character data map
      const idToData = new Map<string, Character>();
      for (const char of charactersData.characters) {
        if (char.id) {
          idToData.set(char.id, char);
        }
      }
      characterIdToData = idToData;
    } catch (err) {
      console.error('[AudioPanel] Error loading characters.json:', err);
    }
  }

  // Compute line counts by character ID
  $: lineCountsById = (() => {
    const counts = new Map<string, number>();
    const scr = $currentScript;
    if (!scr) return counts;
    
    for (const line of scr.lines) {
      const characterId = resolveCharacterIdFromLine(line);
      if (characterId) {
        counts.set(characterId, (counts.get(characterId) || 0) + 1);
      }
    }
    return counts;
  })();

  // All characters from characters.json, sorted by line count (descending)
  $: sortedCharactersById = (() => {
    const list = Array.from(characterIdToData.entries()).map(([id, char]) => ({
      id,
      name: char.name,
      character: char,
    }))
      .filter(item => (lineCountsById.get(item.id) || 0) > 0);
    
    list.sort((a, b) => {
      const countA = lineCountsById.get(a.id) || 0;
      const countB = lineCountsById.get(b.id) || 0;
      // Sort by line count descending, then by name ascending
      if (countB !== countA) {
        return countB - countA;
      }
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });
    return list;
  })();

  $: advancedCharacters = advancedPreview?.characters ?? [];
  $: selectedAdvancedCharacters = advancedCharacters.filter((item) => selectedAdvancedCharacterIds.has(item.characterId));
  $: selectedAdvancedChapterBatches = (advancedPreview?.chapterBatches ?? []).filter((item) => selectedAdvancedCharacterIds.has(item.characterId));
  $: advancedSpeakerSourceBatches = buildSpeakerSourceBatches(advancedCharacters);
  $: selectedAdvancedSpeakerSourceBatches = buildSpeakerSourceBatches(selectedAdvancedCharacters);
  $: speakerSourceBatchByCharacterId = (() => {
    const index = new Map<string, AdvancedSpeakerSourceBatch>();
    for (const batch of advancedSpeakerSourceBatches) {
      for (const line of batch.lines) {
        const lineCharacterId = typeof (line as any)?.characterId === 'string' ? String((line as any).characterId).trim() : '';
        if (lineCharacterId) {
          index.set(lineCharacterId, batch);
        }
      }
    }
    return index;
  })();
  $: selectedAdvancedLineCount = selectedAdvancedChapterBatches.reduce((sum, item) => sum + item.lines.length, 0);
  $: selectedAdvancedCharacterBatchCount = selectedAdvancedCharacters.length;
  $: selectedAdvancedChapterBatchCount = selectedAdvancedChapterBatches.length;
  $: selectedAdvancedSpeakerSourceBatchCount = selectedAdvancedSpeakerSourceBatches.length;
  $: selectedAdvancedParsedChapterCount = new Set(selectedAdvancedChapterBatches.map((item) => item.chapterTitle)).size;
  $: selectedAdvancedUnderTargetCharacters = selectedAdvancedCharacters.filter((item) => item.totalCharacters < CHARACTER_BATCH_TARGET);
  $: selectedAdvancedUnderTargetChapterBatchCount = selectedAdvancedChapterBatches.filter((item) => item.totalCharacters < CHARACTER_BATCH_TARGET).length;
  $: selectedAdvancedUnderTargetSpeakerSourceBatchCount = selectedAdvancedSpeakerSourceBatches.filter((item) => item.totalCharacters < CHARACTER_BATCH_TARGET).length;
  $: selectedAdvancedCharactersNeedingFillerCount = advancedGenerationMode === 'character'
    ? selectedAdvancedUnderTargetCharacters.length
    : advancedGenerationMode === 'chapter'
      ? selectedAdvancedCharacters.filter((item) => item.underTargetChapterBatchCount > 0).length
      : selectedAdvancedUnderTargetSpeakerSourceBatchCount;
  $: selectedAdvancedReadyCharacterCount = Math.max(
    0,
    (advancedGenerationMode === 'speaker-source' ? selectedAdvancedSpeakerSourceBatchCount : selectedAdvancedCharacterBatchCount) -
      selectedAdvancedCharactersNeedingFillerCount,
  );
  $: advancedAllCharactersSelected = advancedCharacters.length > 0 && selectedAdvancedCharacterIds.size === advancedCharacters.length;
  $: advancedSomeCharactersSelected = selectedAdvancedCharacterIds.size > 0 && !advancedAllCharactersSelected;
  $: if (advancedSelectAllInput) {
    advancedSelectAllInput.indeterminate = advancedSomeCharactersSelected;
  }

  async function handleGenerateWholeChapter() {
    const withLines = sortedCharactersById;
    if (withLines.length === 0) {
      alert('No characters with lines in this chapter.');
      return;
    }

    const missingVoices = withLines.filter(item => !getVoiceForCharacter(item.id));
    if (missingVoices.length > 0) {
      const proceed = confirm(
        `${missingVoices.length} character(s) have no voice assignment and will be skipped. Continue?`
      );
      if (!proceed) return;
    }

    const queueTargets = withLines.filter(item => getVoiceForCharacter(item.id));
    if (queueTargets.length === 0) {
      alert('No voiced characters available to generate.');
      return;
    }

    const ch = get(currentChapter);
    if (!ch) return;

    const missingVoiceLineCount = missingVoices.reduce((sum, item) => sum + (lineCountsById.get(item.id) || 0), 0);

    let totalQueuedLines = 0;
    for (const item of queueTargets) {
      const queuedCount = await enqueueCharacterGeneration(item.id, { missingOnly: true });
      totalQueuedLines += queuedCount;
    }

    setQueueChapterOverview(ch.title, totalQueuedLines, missingVoiceLineCount);

    if (totalQueuedLines === 0) {
      alert('No missing audio lines found for voiced characters in this chapter.');
    }
  }

  async function handleGenerateWholeBook() {
    const allChapters = get(chapters);
    if (allChapters.length === 0) {
      alert('No chapters available for this book.');
      return;
    }

    type ChapterQueueTotals = {
      queuedCharacterBatches: number;
      queuedLines: number;
      missingVoiceLines: number;
    };

    const missingVoiceCharacterIds = new Set<string>();
    const jobsByCharacterId = new Map<string, QueuedJobPayload>();
    const characterQueueOrder: string[] = [];
    const chapterQueueTotals = new Map<string, ChapterQueueTotals>();
    let totalLinesQueued = 0;

    for (const chapter of allChapters) {
      const script = await readDialogueForChapter(chapter.title);
      if (!script?.lines?.length) continue;

      const linesByCharacterId = new Map<string, DialogueLine[]>();
      for (const line of script.lines as any[]) {
        const characterId = resolveCharacterIdFromLine(line);

        if (!characterId) continue;

        const existingLines = linesByCharacterId.get(characterId) || [];
        existingLines.push(line as DialogueLine);
        linesByCharacterId.set(characterId, existingLines);
      }

      const chapterTotals: ChapterQueueTotals = {
        queuedCharacterBatches: 0,
        queuedLines: 0,
        missingVoiceLines: 0,
      };

      for (const [characterId, characterLines] of linesByCharacterId.entries()) {
        if (characterLines.length === 0) continue;

        const characterName = characterIdToName.get(characterId) || characterId;
        const voiceInfo = getVoiceForCharacter(characterId);
        if (!voiceInfo) {
          missingVoiceCharacterIds.add(characterId);
          chapterTotals.missingVoiceLines += characterLines.length;
          continue;
        }

        const checks = await Promise.all(
          characterLines.map(async (line) => ({
            line,
            exists: await checkAudioExistsForLine(chapter.title, characterName, line.id),
          }))
        );

        const linesToGenerate = checks.filter(check => !check.exists).map(check => check.line);
        if (linesToGenerate.length === 0) continue;

        const sourceFile = getChapterSourceFile(chapter);
        const linesWithContext = buildQueuedLinesWithContext(linesToGenerate, chapter.title, sourceFile, characterName);

        let payload = jobsByCharacterId.get(characterId);
        if (!payload) {
          payload = {
            characterId,
            characterName,
            lines: [],
            cumulativeCharacterCounts: [],
            totalCharacters: 0,
            voiceInfo,
            chapterTitle: chapter.title,
            sourceFile,
          };
          jobsByCharacterId.set(characterId, payload);
          characterQueueOrder.push(characterId);
        }

        payload.lines.push(...linesWithContext);

        totalLinesQueued += linesWithContext.length;
        chapterTotals.queuedCharacterBatches += 1;
        chapterTotals.queuedLines += linesWithContext.length;
      }

      if (chapterTotals.queuedLines > 0 || chapterTotals.missingVoiceLines > 0) {
        chapterQueueTotals.set(chapter.title, chapterTotals);
      }
    }

    if (jobsByCharacterId.size === 0) {
      const missingVoicesNote = missingVoiceCharacterIds.size > 0
        ? ` ${missingVoiceCharacterIds.size} character(s) are missing voice assignments.`
        : '';
      alert(`No missing audio lines found across this book.${missingVoicesNote}`);
      return;
    }

    const queueRunTimestamp = Date.now();
    let jobCounter = 0;

    for (const characterId of characterQueueOrder) {
      const payload = jobsByCharacterId.get(characterId);
      if (!payload || payload.lines.length === 0) continue;

      const { cumulative, total } = buildCumulativeCharacterCounts(payload.lines);
      payload.cumulativeCharacterCounts = cumulative;
      payload.totalCharacters = total;

      const jobId = `book-${characterId}-missing-${queueRunTimestamp}-${jobCounter++}`;
      queuedJobs.set(jobId, payload);
      enqueueAudioJob({
        id: jobId,
        characterId: payload.characterId,
        characterName: payload.characterName,
        chapterTitle: payload.chapterTitle,
        generationMode: 'missing',
        total: payload.lines.length,
        totalCharacters: payload.totalCharacters,
      });
    }

    for (const [chapterTitle, totals] of chapterQueueTotals.entries()) {
      setQueueChapterOverview(chapterTitle, totals.queuedLines, totals.missingVoiceLines);
    }

    if (!queueProcessing) {
      void processQueue();
    }

    const chaptersWithQueuedJobs = Array.from(chapterQueueTotals.values())
      .filter((totals) => totals.queuedCharacterBatches > 0)
      .length;
    const totalJobsQueued = jobsByCharacterId.size;
    const charactersWithQueuedJobs = totalJobsQueued;

    const missingVoicesNote = missingVoiceCharacterIds.size > 0
      ? ` Skipped ${missingVoiceCharacterIds.size} character(s) with no assigned voice.`
      : '';
    alert(
      `Queued ${totalLinesQueued} missing line${totalLinesQueued === 1 ? '' : 's'} ` +
      `across ${totalJobsQueued} character job${totalJobsQueued === 1 ? '' : 's'} in ` +
      `${chaptersWithQueuedJobs} chapter${chaptersWithQueuedJobs === 1 ? '' : 's'} ` +
      `for ${charactersWithQueuedJobs} character${charactersWithQueuedJobs === 1 ? '' : 's'}, ` +
      `batched by character across chapters.${missingVoicesNote}`
    );
  }

  async function buildAdvancedBookPreview(): Promise<AdvancedBookPreview> {
    if (characterIdToData.size === 0 && get(bookRoot)) {
      await loadCharacterData();
    }

    const allChapters = get(chapters);
    const parsedChapters = allChapters.filter((chapter) => chapter.parsed);

    type CharacterAccumulator = {
      characterId: string;
      characterName: string;
      voiceInfo: VoiceInfo;
      lines: QueuedDialogueLine[];
      totalCharacters: number;
      chapterBatchCount: number;
      underTargetChapterBatchCount: number;
      chapters: Set<string>;
    };

    const charactersById = new Map<string, CharacterAccumulator>();
    const chapterBatches: AdvancedBookChapterBatch[] = [];
    const chapterOverview = new Map<string, AdvancedChapterOverview>();
    const missingVoiceCharacterIds = new Set<string>();
    let totalMissingLines = 0;
    let missingVoiceLineCount = 0;

    for (const chapter of parsedChapters) {
      const script = await readDialogueForChapter(chapter.title);
      if (!script?.lines?.length) continue;

      const linesByCharacterId = new Map<string, DialogueLine[]>();
      for (const line of script.lines as any[]) {
        const characterId = resolveCharacterIdFromLine(line);
        if (!characterId) continue;

        const existingLines = linesByCharacterId.get(characterId) || [];
        existingLines.push(line as DialogueLine);
        linesByCharacterId.set(characterId, existingLines);
      }

      const sourceFile = getChapterSourceFile(chapter);
      let chapterMissingVoiceTotal = 0;

      for (const [characterId, characterLines] of linesByCharacterId.entries()) {
        if (characterLines.length === 0) continue;

        const characterName = characterIdToName.get(characterId) || characterId;
        const checks = await Promise.all(
          characterLines.map(async (line) => ({
            line,
            exists: await checkAudioExistsForLine(chapter.title, characterName, line.id),
          }))
        );

        const linesToGenerate = checks.filter((check) => !check.exists).map((check) => check.line);
        if (linesToGenerate.length === 0) continue;

        const voiceInfo = getVoiceForCharacter(characterId);
        if (!voiceInfo) {
          missingVoiceCharacterIds.add(characterId);
          chapterMissingVoiceTotal += linesToGenerate.length;
          missingVoiceLineCount += linesToGenerate.length;
          continue;
        }

        const linesWithContext = buildQueuedLinesWithContext(linesToGenerate, chapter.title, sourceFile, characterName);
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
        if (totalCharacters < CHARACTER_BATCH_TARGET) {
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

  async function handleOpenAdvancedOverlay() {
    const requestId = ++advancedPreviewRequestId;

    showAdvancedOverlay = true;
    loadingAdvancedPreview = true;
    advancedSubmitting = false;
    advancedPreviewError = null;
    advancedPreview = null;
    selectedAdvancedCharacterIds = new Set<string>();
    advancedGenerationMode = 'character';
    advancedUseFillerForShortBatch = false;
    advancedFillerText = DEFAULT_ADVANCED_FILLER_TEXT;

    try {
      const preview = await buildAdvancedBookPreview();
      if (requestId !== advancedPreviewRequestId) return;

      advancedPreview = preview;
      selectedAdvancedCharacterIds = new Set(preview.characters.map((item) => item.characterId));
    } catch (error) {
      if (requestId !== advancedPreviewRequestId) return;

      advancedPreviewError = error instanceof Error
        ? error.message
        : 'Failed to build the advanced audio preview.';
    } finally {
      if (requestId === advancedPreviewRequestId) {
        loadingAdvancedPreview = false;
      }
    }
  }

  function handleCloseAdvancedOverlay() {
    if (advancedSubmitting) return;

    advancedPreviewRequestId += 1;
    showAdvancedOverlay = false;
    loadingAdvancedPreview = false;
    advancedPreviewError = null;
  }

  function toggleAdvancedCharacterSelection(characterId: string) {
    const next = new Set(selectedAdvancedCharacterIds);
    if (next.has(characterId)) {
      next.delete(characterId);
    } else {
      next.add(characterId);
    }
    selectedAdvancedCharacterIds = next;
  }

  function selectAllAdvancedCharacters() {
    selectedAdvancedCharacterIds = new Set(advancedCharacters.map((item) => item.characterId));
  }

  function clearAdvancedCharacterSelection() {
    selectedAdvancedCharacterIds = new Set<string>();
  }

  function handleToggleAllAdvancedCharacters(checked: boolean) {
    if (checked) {
      selectAllAdvancedCharacters();
      return;
    }

    clearAdvancedCharacterSelection();
  }

  async function handleQueueAdvancedGeneration() {
    if (!advancedPreview || advancedSubmitting) return;

    if (selectedAdvancedCharacterIds.size === 0) {
      alert('Select at least one character to generate.');
      return;
    }

    const trimmedFillerText = advancedFillerText.trim();
    if (advancedUseFillerForShortBatch && !trimmedFillerText) {
      alert('Enter filler/pretext text or disable the filler option.');
      return;
    }

    const generationOptions: GenerateAudioCharacterOptions = {
      useFillerForShortBatch: advancedUseFillerForShortBatch,
      fillerText: advancedUseFillerForShortBatch ? trimmedFillerText : null,
    };

    advancedSubmitting = true;

    try {
      const selectedIds = new Set(selectedAdvancedCharacterIds);
      const queueRunTimestamp = Date.now();
      const chapterProcessableTotals = new Map<string, number>();
      const chapterMissingVoiceTotals = new Map(
        advancedPreview.chapterOverview.map((entry) => [entry.chapterTitle, entry.missingVoiceTotal])
      );

      let jobsQueued = 0;
      let linesQueued = 0;
      let jobCounter = 0;

      if (advancedGenerationMode === 'character') {
        for (const batch of selectedAdvancedCharacters) {
          if (!selectedIds.has(batch.characterId) || batch.lines.length === 0) continue;

          const firstLine = batch.lines[0];
          const chapterTitle = resolveQueuedLineChapterTitle(firstLine, batch.characterName);
          const sourceFile = resolveQueuedLineSourceFile(firstLine, `${chapterTitle}/chapter.txt`);
          const { cumulative, total } = buildCumulativeCharacterCounts(batch.lines);
          const jobId = `advanced-character-${batch.characterId}-${queueRunTimestamp}-${jobCounter++}`;

          queuedJobs.set(jobId, {
            characterId: batch.characterId,
            characterName: batch.characterName,
            lines: batch.lines,
            cumulativeCharacterCounts: cumulative,
            totalCharacters: total,
            voiceInfo: batch.voiceInfo,
            chapterTitle,
            sourceFile,
            generationOptions,
          });

          enqueueAudioJob({
            id: jobId,
            characterId: batch.characterId,
            characterName: batch.characterName,
            chapterTitle,
            generationMode: 'missing',
            total: batch.lines.length,
            totalCharacters: total,
          });

          for (const line of batch.lines) {
            const lineChapterTitle = resolveQueuedLineChapterTitle(line, chapterTitle);
            chapterProcessableTotals.set(lineChapterTitle, (chapterProcessableTotals.get(lineChapterTitle) || 0) + 1);
          }

          jobsQueued += 1;
          linesQueued += batch.lines.length;
        }
      } else if (advancedGenerationMode === 'chapter') {
        for (const batch of selectedAdvancedChapterBatches) {
          if (!selectedIds.has(batch.characterId) || batch.lines.length === 0) continue;

          const { cumulative, total } = buildCumulativeCharacterCounts(batch.lines);
          const jobId = `advanced-chapter-${batch.chapterTitle}-${batch.characterId}-${queueRunTimestamp}-${jobCounter++}`;

          queuedJobs.set(jobId, {
            characterId: batch.characterId,
            characterName: batch.characterName,
            lines: batch.lines,
            cumulativeCharacterCounts: cumulative,
            totalCharacters: total,
            voiceInfo: batch.voiceInfo,
            chapterTitle: batch.chapterTitle,
            sourceFile: batch.sourceFile,
            generationOptions,
          });

          enqueueAudioJob({
            id: jobId,
            characterId: batch.characterId,
            characterName: batch.characterName,
            chapterTitle: batch.chapterTitle,
            generationMode: 'missing',
            total: batch.lines.length,
            totalCharacters: total,
          });

          chapterProcessableTotals.set(batch.chapterTitle, (chapterProcessableTotals.get(batch.chapterTitle) || 0) + batch.lines.length);
          jobsQueued += 1;
          linesQueued += batch.lines.length;
        }
      } else {
        for (const batch of selectedAdvancedSpeakerSourceBatches) {
          if (batch.lines.length === 0) continue;

          const firstLine = batch.lines[0];
          const chapterTitle = resolveQueuedLineChapterTitle(firstLine, batch.primaryCharacterName);
          const sourceFile = resolveQueuedLineSourceFile(firstLine, `${chapterTitle}/chapter.txt`);
          const { cumulative, total } = buildCumulativeCharacterCounts(batch.lines);
          const jobId = `advanced-speaker-source-${batch.speakerSourceId.replace(/[^a-zA-Z0-9_-]/g, '_')}-${queueRunTimestamp}-${jobCounter++}`;

          queuedJobs.set(jobId, {
            characterId: batch.primaryCharacterId,
            characterName: batch.primaryCharacterName,
            lines: batch.lines,
            cumulativeCharacterCounts: cumulative,
            totalCharacters: total,
            voiceInfo: batch.voiceInfo,
            chapterTitle,
            sourceFile,
            generationOptions,
          });

          enqueueAudioJob({
            id: jobId,
            characterId: batch.primaryCharacterId,
            characterName: `${batch.speakerLabel} (speaker source)`,
            chapterTitle,
            generationMode: 'missing',
            total: batch.lines.length,
            totalCharacters: total,
          });

          for (const line of batch.lines) {
            const lineChapterTitle = resolveQueuedLineChapterTitle(line, chapterTitle);
            chapterProcessableTotals.set(lineChapterTitle, (chapterProcessableTotals.get(lineChapterTitle) || 0) + 1);
          }

          jobsQueued += 1;
          linesQueued += batch.lines.length;
        }
      }

      if (jobsQueued === 0 || linesQueued === 0) {
        alert('No missing audio lines found for the selected characters.');
        return;
      }

      const chapterTitles = new Set<string>([
        ...chapterProcessableTotals.keys(),
        ...chapterMissingVoiceTotals.keys(),
      ]);

      for (const chapterTitle of chapterTitles) {
        const processableTotal = chapterProcessableTotals.get(chapterTitle) || 0;
        const missingVoiceTotal = chapterMissingVoiceTotals.get(chapterTitle) || 0;
        if (processableTotal > 0 || missingVoiceTotal > 0) {
          setQueueChapterOverview(chapterTitle, processableTotal, missingVoiceTotal);
        }
      }

      if (!queueProcessing) {
        void processQueue();
      }

      showAdvancedOverlay = false;

      const batchLabel = advancedGenerationMode === 'character'
        ? 'character batch'
        : advancedGenerationMode === 'chapter'
          ? 'chapter batch'
          : 'speaker-source batch';
      const fillerNote = advancedUseFillerForShortBatch ? ' Short batches will use the configured filler/pretext.' : '';
      alert(
        `Queued ${linesQueued} missing line${linesQueued === 1 ? '' : 's'} ` +
        `across ${jobsQueued} ${batchLabel}${jobsQueued === 1 ? '' : 'es'} ` +
        `from ${selectedAdvancedParsedChapterCount} parsed chapter${selectedAdvancedParsedChapterCount === 1 ? '' : 's'}.` +
        fillerNote
      );
    } finally {
      advancedSubmitting = false;
    }
  }

  async function handlePruneStaleAudio() {
    const ch = get(currentChapter);
    const scr = get(currentScript);
    if (!ch || !scr) return;

    pruningStale = true;
    try {
      let removedMissingFileTotal = 0;
      let removedStaleLineTotal = 0;
      for (const item of sortedCharactersById) {
        const reconciled = await reconcileAudioManifestForCharacter(ch.title, item.name);
        removedMissingFileTotal += reconciled.removedCount;
        removedStaleLineTotal += await pruneStaleCharacterAudio(item.id, item.name, scr, ch.title, { suppressRefresh: true });
      }

      await loadAudioClipCounts();
      triggerAudioUpdate();

      const removedTotal = removedMissingFileTotal + removedStaleLineTotal;
      if (removedTotal > 0) {
        alert(
          `Removed ${removedTotal} stale audio clip${removedTotal === 1 ? '' : 's'} ` +
          `(${removedMissingFileTotal} missing file${removedMissingFileTotal === 1 ? '' : 's'}, ` +
          `${removedStaleLineTotal} invalid line mapping${removedStaleLineTotal === 1 ? '' : 's'}).`
        );
      } else {
        alert('No stale audio clips found.');
      }
    } finally {
      pruningStale = false;
    }
  }

  // Load audio clip counts for all characters (by ID)
  async function loadAudioClipCounts() {
    const ch = get(currentChapter);
    if (!ch) return;

    const newCounts = new Map<string, number>();
    
    for (const item of sortedCharactersById) {
      const characterName = characterIdToName.get(item.id) || item.id;
      const { clipCount } = await checkAudioExistsForCharacter(ch.title, characterName);
      newCounts.set(item.id, clipCount);
    }
    
    audioClipCounts = newCounts;
  }

  // Reload counts when chapter, characters, or audio updates change
  $: if ($currentChapter && sortedCharactersById.length > 0) {
    // Also reacts to $audioUpdateTrigger changes
    void $audioUpdateTrigger; // Reference it to make the reactive statement depend on it
    loadAudioClipCounts();
  }

  // Get voice assignment for a character by ID
  function getVoiceForCharacter(characterId: string): VoiceInfo | null {
    const assignment = $voices.assignments.find(a => a.characterId === characterId);
    if (!assignment) return null;
    
    const voice = $voices.voices.find(v => v.id === assignment.voiceId);
    if (!voice) return null;
    
    return {
      voiceId: voice.providerVoiceId,
      provider: voice.provider,
      displayName: voice.displayName,
      characterId: characterId,
    };
  }

  async function handleGenerateAudio(characterId: string) {
    const ch = get(currentChapter);
    const root = get(bookRoot);
    const scr = get(currentScript);
    
    if (!ch || !root || !scr) return;

    const characterName = characterIdToName.get(characterId) || characterId;
    const voiceInfo = getVoiceForCharacter(characterId);
    const selectedLineId = get(audioGenerateLineId);
    const characterLines = getCharacterLinesForCurrentScript(characterId, characterName, scr);
    const selectedLine = selectedLineId == null
      ? null
      : characterLines.find((line) => Number((line as any).id) === Number(selectedLineId)) || null;

    if (selectedLine) {
      if (!voiceInfo) {
        alert(`No voice assigned to ${characterName}`);
        return;
      }

      await generateSingleSelectedLine(
        selectedLine,
        characterId,
        characterName,
        voiceInfo,
        ch.title,
        getChapterSourceFile(ch),
      );
      return;
    }

    await pruneStaleCharacterAudio(characterId, characterName, scr, ch.title);
    
    // Check if audio already exists
    const { exists, clipCount } = await checkAudioExistsForCharacter(ch.title, characterName);
    const lineCount = lineCountsById.get(characterId) || 0;
    const hasMissing = exists && clipCount < lineCount;

    if (hasMissing) {
      await enqueueCharacterGeneration(characterId, { missingOnly: true });
      return;
    }
    
    if (exists && clipCount >= lineCount) {
      overwriteCharacterId = characterId;
      overwriteClipCount = clipCount;
      showOverwriteModal = true;
      return;
    }

    await enqueueCharacterGeneration(characterId, { missingOnly: false });
  }

  function getCharacterLinesForScript(characterId: string, characterName: string, script: any): DialogueLine[] {
    return script.lines.filter((line: any) => {
      if ('characterId' in line && line.characterId === characterId) return true;
      if ('chosenSpeaker' in line && line.chosenSpeaker === characterName) return true;
      return false;
    }) as unknown as DialogueLine[];
  }

  function getCharacterLinesForCurrentScript(characterId: string, characterName: string, scr: any): DialogueLine[] {
    return getCharacterLinesForScript(characterId, characterName, scr);
  }

  async function generateSingleSelectedLine(
    line: DialogueLine,
    characterId: string,
    characterName: string,
    voiceInfo: VoiceInfo,
    chapterTitle: string,
    sourceFile: string,
  ): Promise<boolean> {
    const lineId = Number((line as any)?.id);
    const text = String((line as any)?.text ?? '').trim();

    if (!Number.isFinite(lineId) || !text) {
      alert('Selected line is missing an ID or text and could not be generated.');
      return false;
    }

    generatingCharacter = characterId;
    generationProgress = { current: 0, total: 1 };

    try {
      const result = await generateAudioForLine(
        lineId,
        text,
        characterName,
        characterId,
        voiceInfo.voiceId,
        voiceInfo.provider,
        chapterTitle,
        sourceFile,
      );

      if (!result.success) {
        alert(result.error || 'Failed to generate selected line audio.');
        return false;
      }

      generationProgress = { current: 1, total: 1 };
      audioGenerateLineId.set(null);
      triggerAudioUpdate();
      await loadAudioClipCounts();
      return true;
    } finally {
      generatingCharacter = null;
      generationProgress = { current: 0, total: 0 };
    }
  }

  async function buildPayloadFromQueueItem(item: AudioQueueItem): Promise<{ payload: QueuedJobPayload | null; markComplete?: boolean; error?: string }> {
    const chapter = get(chapters).find((entry) => entry.title === item.chapterTitle) || null;
    const activeChapter = get(currentChapter);
    let script: any = null;

    if (activeChapter?.title === item.chapterTitle) {
      script = get(currentScript);
    }

    if (!script?.lines?.length) {
      script = await readDialogueForChapter(item.chapterTitle);
    }

    if (!script?.lines?.length) {
      return { payload: null, markComplete: true };
    }

    const characterName = characterIdToName.get(item.characterId) || item.characterName || item.characterId;
    const voiceInfo = getVoiceForCharacter(item.characterId);
    if (!voiceInfo) {
      return { payload: null, error: `No voice assigned to ${characterName}.` };
    }

    const linesForCharacter = getCharacterLinesForScript(item.characterId, characterName, script);
    if (linesForCharacter.length === 0) {
      return { payload: null, markComplete: true };
    }

    let linesToGenerate = linesForCharacter;
    if ((item.generationMode ?? 'missing') === 'missing') {
      const checks = await Promise.all(
        linesForCharacter.map(async (line) => ({
          line,
          exists: await checkAudioExistsForLine(item.chapterTitle, characterName, line.id),
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
        characterId: item.characterId,
        characterName,
        lines: linesToGenerate,
        cumulativeCharacterCounts: cumulative,
        totalCharacters: total,
        voiceInfo,
        chapterTitle: item.chapterTitle,
        sourceFile: chapter?.path || `${item.chapterTitle}/chapter.txt`,
      },
    };
  }

  async function pruneStaleCharacterAudio(
    characterId: string,
    characterName: string,
    scr: any,
    chapterTitle: string,
    options: { suppressRefresh?: boolean } = {}
  ): Promise<number> {
    const characterLines = getCharacterLinesForCurrentScript(characterId, characterName, scr);
    const validLineIds = new Set(characterLines.map(line => Number(line.id)).filter(id => Number.isFinite(id)));
    const manifest = await readManifest(chapterTitle, characterName);
    if (!manifest?.clips?.length) return 0;

    const staleClipIds = manifest.clips
      .filter(clip => clip.chapter === chapterTitle)
      .map(clip => Number((clip as any).id))
      .filter(id => Number.isFinite(id) && !validLineIds.has(id));

    if (staleClipIds.length === 0) return 0;

    let removed = 0;
    for (const clipId of staleClipIds) {
      const ok = await deleteAudioLineForCharacter(chapterTitle, characterName, clipId);
      if (ok) removed += 1;
    }

    if (removed > 0 && !options.suppressRefresh) {
      console.log(`[AudioPanel] Removed ${removed} stale clip(s) for ${characterName}`);
      triggerAudioUpdate();
      await loadAudioClipCounts();
    }

    return removed;
  }

  async function enqueueCharacterGeneration(characterId: string, options: { missingOnly?: boolean } = {}): Promise<number> {
    const ch = get(currentChapter);
    const root = get(bookRoot);
    const scr = get(currentScript);
    
    if (!ch || !root || !scr) return 0;

    const characterName = characterIdToName.get(characterId) || characterId;
    const voiceInfo = getVoiceForCharacter(characterId);
    if (!voiceInfo) {
      alert(`No voice assigned to ${characterName}`);
      return 0;
    }

    // Remove stale clips before enqueueing generation.
    await pruneStaleCharacterAudio(characterId, characterName, scr, ch.title);

    // Get all lines for this character (handle both v2.0 characterId and v1.0 chosenSpeaker)
    const characterLines = getCharacterLinesForCurrentScript(characterId, characterName, scr);

    if (characterLines.length === 0) {
      alert(`No lines found for ${characterName}`);
      return 0;
    }

    let linesToGenerate = characterLines;
    if (options.missingOnly) {
      const checks = await Promise.all(
        characterLines.map(async (line) => ({
          line,
          exists: await checkAudioExistsForLine(ch.title, characterName, line.id),
        }))
      );
      linesToGenerate = checks.filter(c => !c.exists).map(c => c.line);
      if (linesToGenerate.length === 0) {
        return 0;
      }
    }

    const mode = options.missingOnly ? 'missing' : 'full';
    const jobId = `${characterId}-${mode}-${Date.now()}`;
    const { cumulative, total } = buildCumulativeCharacterCounts(linesToGenerate);
    queuedJobs.set(jobId, {
      characterId,
      characterName,
      lines: linesToGenerate,
      cumulativeCharacterCounts: cumulative,
      totalCharacters: total,
      voiceInfo,
      chapterTitle: ch.title,
      sourceFile: getChapterSourceFile(ch),
    });

    enqueueAudioJob({
      id: jobId,
      characterId,
      characterName,
      chapterTitle: ch.title,
      generationMode: mode,
      total: linesToGenerate.length,
      totalCharacters: total,
    });

    if (!queueProcessing) {
      void processQueue();
    }

    return linesToGenerate.length;
  }

  async function processQueue() {
    if (queueProcessing) return;
    queueProcessing = true;

    while (true) {
      const next = getNextQueuedJob();
      if (!next) {
        queueProcessing = false;
        generatingCharacter = null;
        generationProgress = { current: 0, total: 0 };
        break;
      }

      let payload = queuedJobs.get(next.id);
      if (!payload) {
        const rebuilt = await buildPayloadFromQueueItem(next);
        if (rebuilt.markComplete) {
          markJobCompleted(next.id);
          continue;
        }
        if (!rebuilt.payload) {
          markJobFailed(next.id, [rebuilt.error || 'Unable to rebuild queued payload for job.']);
          continue;
        }

        payload = rebuilt.payload;
        queuedJobs.set(next.id, payload);
      }

      if (isJobCanceled(next.id)) {
        markJobCanceled(next.id);
        queuedJobs.delete(next.id);
        continue;
      }

      markJobRunning(next.id);
      generatingCharacter = payload.characterId;
      generationProgress = { current: 0, total: payload.lines.length };

      const result = await generateAudioForCharacter(
        payload.characterName,
        payload.characterId,
        payload.lines,
        payload.voiceInfo.voiceId,
        payload.voiceInfo.provider,
        payload.chapterTitle,
        payload.sourceFile,
        (current, total) => {
          generationProgress = { current, total };
          const boundedCurrent = Math.max(0, Math.min(current, payload.cumulativeCharacterCounts.length));
          const processedCharacters = boundedCurrent > 0 ? payload.cumulativeCharacterCounts[boundedCurrent - 1] : 0;
          updateJobProgress(next.id, current, total, processedCharacters, payload.totalCharacters);
        },
        () => isJobCanceled(next.id),
        payload.generationOptions
      );

      if (result.canceled) {
        markJobCanceled(next.id);
      } else if (result.success) {
        markJobCompleted(next.id);
      } else {
        markJobFailed(next.id, result.errors);
      }

      queuedJobs.delete(next.id);
      await loadAudioClipCounts();
      triggerAudioUpdate();
    }
  }

  function handleOverwriteConfirm() {
    if (overwriteCharacterId) {
      void enqueueCharacterGeneration(overwriteCharacterId, { missingOnly: false });
    }
    showOverwriteModal = false;
    overwriteCharacterId = null;
    overwriteClipCount = 0;
  }

  function handleOverwriteCancel() {
    showOverwriteModal = false;
    overwriteCharacterId = null;
    overwriteClipCount = 0;
  }

  onMount(() => {
    requeueRunningJobs();
    if (getNextQueuedJob()) {
      void processQueue();
    }
  });
</script>

<style>
  .audio-panel {
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: 14px;
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 4px;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .panel-title {
    margin: 2px 0 6px 0;
    color: #6b7280;
    font-weight: 600;
    font-size: 14px;
  }

  .header-icon-btn {
    width: 28px;
    height: 28px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: #fff;
    color: #374151;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .header-icon-btn:hover {
    background: #f3f4f6;
  }

  .header-icon-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .header-icon-btn.prune-btn {
    color: #4b5563;
  }

  .header-icon-btn.chapter-btn {
    color: #2563eb;
  }

  .header-icon-btn.book-btn {
    color: #7c3aed;
  }

  .header-icon-btn.advanced-btn {
    width: auto;
    padding: 0 10px;
    color: #0f766e;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .panel-divider {
    height: 1px;
    background-color: #eee;
    margin: 2px 0 6px 0;
  }

  .character-row {
    border: 1px solid #eee;
    padding: 8px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 8px;
    position: relative;
  }

  .line-count-badge {
    font-size: 12px;
    color: #222;
    padding: 2px 8px;
    border-radius: 10px;
    white-space: nowrap;
    flex-shrink: 0;
    border: 1px solid rgba(0, 0, 0, 0.1);
    min-width: 28px;
    text-align: center;
  }

  .name-display {
    flex: 1;
    font-size: 14px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .character-name {
    font-weight: 500;
  }

  .voice-name {
    font-size: 12px;
    color: #6b7280;
    font-style: italic;
  }

  .button-group {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .generate-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 6px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: #667eea;
    cursor: pointer;
    transition: background-color 0.2s ease;
    flex-shrink: 0;
  }

  .generate-btn:hover:not(:disabled) {
    background-color: rgba(0, 0, 0, 0.08);
  }

  .generate-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .generating-indicator {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0;
    font-size: 13px;
    color: #667eea;
    font-weight: 600;
  }

  .generating-indicator .completed {
    color: #b2b2b2;
  }

  .no-characters {
    color: #777;
    font-size: 14px;
    padding: 20px;
    text-align: center;
  }

  /* Modal */
  .modal-backdrop {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .modal-content {
    background: white;
    border-radius: 12px;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
    max-width: 400px;
    padding: 24px;
  }

  .modal-title {
    font-size: 18px;
    font-weight: 700;
    color: #1f2937;
    margin: 0 0 12px 0;
  }

  .modal-message {
    font-size: 14px;
    color: #6b7280;
    margin-bottom: 20px;
    line-height: 1.5;
  }

  .modal-actions {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
  }

  .modal-btn {
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .cancel-btn {
    background: #f3f4f6;
    color: #1f2937;
  }

  .cancel-btn:hover {
    background: #e5e7eb;
  }

  .overwrite-btn {
    background: #ef4444;
    color: white;
  }

  .overwrite-btn:hover {
    background: #dc2626;
  }

  .advanced-modal-content {
    background: white;
    border-radius: 14px;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
    width: min(760px, calc(100vw - 32px));
    max-height: calc(100vh - 48px);
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .advanced-modal-header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    padding: 24px 24px 18px;
    border-bottom: 1px solid #e5e7eb;
  }

  .advanced-modal-subtitle {
    margin: 6px 0 0 0;
    font-size: 13px;
    color: #6b7280;
    line-height: 1.5;
  }

  .advanced-close-btn {
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: #4b5563;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }

  .advanced-modal-body {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 20px 24px 24px;
    overflow-y: auto;
  }

  .advanced-loading,
  .advanced-empty,
  .advanced-error-card {
    padding: 24px;
    color: #4b5563;
    line-height: 1.6;
  }

  .advanced-summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
  }

  .advanced-summary-card {
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 12px;
    background: #f9fafb;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .advanced-summary-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: #6b7280;
  }

  .advanced-summary-card strong {
    font-size: 20px;
    color: #111827;
  }

  .advanced-summary-note {
    font-size: 12px;
    color: #6b7280;
  }

  .advanced-section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .advanced-section h4 {
    margin: 0;
    font-size: 14px;
    color: #111827;
  }

  .advanced-section-note,
  .advanced-helper {
    margin: 0;
    font-size: 12px;
    color: #6b7280;
    line-height: 1.5;
  }

  .advanced-mode-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
  }

  .advanced-mode-card {
    border: 1px solid #d1d5db;
    border-radius: 12px;
    padding: 12px;
    display: flex;
    gap: 10px;
    align-items: flex-start;
    cursor: pointer;
    background: #fff;
    transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
  }

  .advanced-mode-card.active {
    border-color: #2563eb;
    box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.18);
    background: #eff6ff;
  }

  .advanced-mode-card input {
    margin-top: 3px;
  }

  .advanced-mode-title {
    display: block;
    font-weight: 600;
    color: #111827;
    margin-bottom: 4px;
  }

  .advanced-mode-description {
    display: block;
    font-size: 12px;
    color: #6b7280;
    line-height: 1.5;
  }

  .advanced-checkbox-row {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    cursor: pointer;
    color: #111827;
  }

  .advanced-checkbox-row input {
    margin-top: 3px;
  }

  .advanced-textarea {
    min-height: 96px;
    border: 1px solid #d1d5db;
    border-radius: 10px;
    padding: 10px 12px;
    resize: vertical;
    font: inherit;
    color: #111827;
    background: #fff;
  }

  .advanced-textarea:disabled {
    background: #f3f4f6;
    color: #9ca3af;
  }

  .advanced-warning,
  .advanced-info {
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 13px;
    line-height: 1.5;
  }

  .advanced-warning {
    border: 1px solid #f59e0b;
    background: #fef3c7;
    color: #92400e;
  }

  .advanced-info {
    border: 1px solid #67e8f9;
    background: #ecfeff;
    color: #155e75;
  }

  .advanced-selection-toolbar {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: flex-start;
  }

  .advanced-selection-summary {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .advanced-selection-meta {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .advanced-master-checkbox {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 600;
    color: #111827;
    cursor: pointer;
  }

  .advanced-master-checkbox input,
  .advanced-character-row input {
    width: 16px;
    height: 16px;
    accent-color: #2563eb;
  }

  .advanced-badge-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .advanced-status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .advanced-status-badge.ready {
    background: #dcfce7;
    color: #166534;
  }

  .advanced-status-badge.warning {
    background: #fef3c7;
    color: #92400e;
  }

  .advanced-selection-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  .advanced-link-btn {
    border: none;
    background: transparent;
    color: #2563eb;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    padding: 0;
  }

  .advanced-link-btn:disabled {
    color: #9ca3af;
    cursor: not-allowed;
  }

  .advanced-character-list {
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    min-height: 220px;
    max-height: 320px;
    overflow-y: auto;
    background: #fff;
  }

  .advanced-character-section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .advanced-character-section-header {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: baseline;
    flex-wrap: wrap;
  }

  .advanced-character-section-title {
    font-size: 13px;
    font-weight: 700;
    color: #111827;
  }

  .advanced-character-preview {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .advanced-character-preview-pill {
    display: inline-flex;
    align-items: center;
    padding: 4px 9px;
    border-radius: 999px;
    background: #eef2ff;
    color: #3730a3;
    font-size: 11px;
    font-weight: 600;
    line-height: 1;
  }

  .advanced-character-preview-pill.more {
    background: #f3f4f6;
    color: #4b5563;
  }

  .advanced-character-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    padding: 12px 14px;
    border-top: 1px solid #f3f4f6;
    cursor: pointer;
    background: #fff;
  }

  .advanced-character-row:first-child {
    border-top: none;
  }

  .advanced-character-row.selected {
    background: #f8fafc;
  }

  .advanced-character-row input {
    margin-top: 4px;
    flex-shrink: 0;
  }

  .advanced-character-main {
    display: flex;
    flex-direction: column;
    gap: 6px;
    flex: 1;
    min-width: 0;
  }

  .advanced-character-header {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  .advanced-character-title-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .advanced-character-trailing {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .advanced-inline-badge {
    display: inline-flex;
    align-items: center;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    line-height: 1;
  }

  .advanced-inline-badge.ready {
    background: #dcfce7;
    color: #166534;
  }

  .advanced-inline-badge.warning {
    background: #fef3c7;
    color: #92400e;
  }

  .advanced-character-name {
    font-weight: 600;
    color: #111827;
  }

  .advanced-character-voice {
    font-size: 12px;
    color: #6b7280;
  }

  .advanced-character-stats {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 12px;
    color: #4b5563;
  }

  .advanced-stat-pill {
    padding: 2px 8px;
    border-radius: 999px;
    background: #f3f4f6;
  }

  .advanced-character-warning {
    margin: 0;
    font-size: 12px;
    color: #b45309;
  }

  .advanced-character-warning.ready {
    color: #0f766e;
  }

  .advanced-actions-row {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    padding-top: 8px;
    border-top: 1px solid #e5e7eb;
  }

  .advanced-secondary-btn,
  .advanced-primary-btn {
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background-color 0.2s ease, border-color 0.2s ease;
  }

  .advanced-secondary-btn {
    border: 1px solid #d1d5db;
    background: #fff;
    color: #111827;
  }

  .advanced-primary-btn {
    border: none;
    background: #2563eb;
    color: #fff;
  }

  .advanced-secondary-btn:hover:not(:disabled),
  .advanced-close-btn:hover:not(:disabled) {
    background: #f3f4f6;
  }

  .advanced-primary-btn:hover:not(:disabled) {
    background: #1d4ed8;
  }

  .advanced-secondary-btn:disabled,
  .advanced-primary-btn:disabled,
  .advanced-close-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  @media (max-width: 720px) {
    .advanced-modal-content {
      width: calc(100vw - 20px);
      max-height: calc(100vh - 20px);
    }

    .advanced-modal-header,
    .advanced-modal-body {
      padding-left: 16px;
      padding-right: 16px;
    }

    .advanced-selection-toolbar,
    .advanced-actions-row {
      flex-direction: column;
      align-items: stretch;
    }

    .advanced-selection-actions,
    .advanced-actions-row {
      justify-content: flex-start;
    }
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .spinning {
    animation: spin 1s linear infinite;
  }
</style>

<div class="audio-panel">
  <div class="panel-header">
    <h3 class="panel-title">Audio Generation</h3>
    <div class="header-actions">
      <button
        class="header-icon-btn prune-btn"
        on:click={handlePruneStaleAudio}
        disabled={sortedCharactersById.length === 0 || pruningStale || queueProcessing}
        title="Remove audio clips for lines that no longer exist for a character"
        aria-label="Prune stale audio"
      >
        {#if pruningStale}
          <span class="spinning"><Loader size={14} /></span>
        {:else}
          <Scissors size={14} />
        {/if}
      </button>
      <button
        class="header-icon-btn chapter-btn"
        on:click={handleGenerateWholeChapter}
        disabled={sortedCharactersById.length === 0 || pruningStale || queueProcessing}
        title="Queue generation for all voiced characters in this chapter"
        aria-label="Queue chapter missing audio"
      >
        <FileText size={14} />
      </button>
      <button
        class="header-icon-btn book-btn"
        on:click={handleGenerateWholeBook}
        disabled={$chapters.length === 0 || pruningStale || queueProcessing}
        title="Queue generation for all missing audio across the whole book"
        aria-label="Queue book missing audio"
      >
        <BookOpen size={14} />
      </button>
      <button
        class="header-icon-btn advanced-btn"
        on:click={handleOpenAdvancedOverlay}
        disabled={$chapters.length === 0 || pruningStale || queueProcessing}
        title="Open advanced batch generation"
        aria-label="Open advanced batch generation"
      >
        <span>ADV</span>
      </button>
    </div>
  </div>
  <div class="panel-divider"></div>

  {#each sortedCharactersById as item (item.id)}
    {@const voiceInfo = getVoiceForCharacter(item.id)}
    {@const lineCount = lineCountsById.get(item.id) || 0}
    {@const isGenerating = generatingCharacter === item.id}
    {@const characterName = item.name}
    {@const characterColor = item.character.color ?? colorForCharacter(characterName)}
    
    <div class="character-row">
      <div 
        class="line-count-badge" 
        style={`background:${rgbaToOpaqueHex(characterColor, DISPLAY_ALPHA)};`}>
        {#if lineCount > 0}
          {lineCount}
        {:else}
          &nbsp;
        {/if}
      </div>
      
      <div class="name-display">
        <span class="character-name">{characterName}</span>
        {#if voiceInfo}
          <span class="voice-name">{voiceInfo.displayName}</span>
        {:else}
          <span class="voice-name" style="color: #ef4444;">No voice assigned</span>
        {/if}
      </div>
      
      <div class="button-group">
        <div class="generating-indicator">
          {#if isGenerating}
            <span class="spinning">
              <Loader size={16} />
            </span>
            <span>{generationProgress.current}/{generationProgress.total}</span>
          {:else}
            {@const existingClips = audioClipCounts.get(item.id) || 0}
            {@const isComplete = existingClips === lineCount && lineCount > 0}
            <span class:completed={isComplete}>{existingClips}/{lineCount}</span>
          {/if}
        </div>
        <button 
          class="generate-btn" 
          on:click={() => handleGenerateAudio(item.id)}
          disabled={!voiceInfo || lineCount === 0 || isGenerating}
          title={!voiceInfo ? 'No voice assigned' : lineCount === 0 ? 'No lines for this character' : 'Generate audio'}>
          <Headphones size={16} />
        </button>
      </div>
    </div>
  {/each}

  {#if sortedCharactersById.length === 0}
    <p class="no-characters">No characters with lines in this chapter</p>
  {/if}
</div>

{#if showOverwriteModal}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="modal-backdrop" on:click={handleOverwriteCancel}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="modal-content" on:click|stopPropagation>
      <h3 class="modal-title">Overwrite Existing Audio?</h3>
      <p class="modal-message">
        Audio already exists for <strong>{overwriteCharacterId ? characterIdToName.get(overwriteCharacterId) || overwriteCharacterId : 'Unknown'}</strong> ({overwriteClipCount} clip{overwriteClipCount !== 1 ? 's' : ''}).
        Do you want to regenerate and overwrite the existing audio?
      </p>
      <div class="modal-actions">
        <button class="modal-btn cancel-btn" on:click={handleOverwriteCancel}>
          Cancel
        </button>
        <button class="modal-btn overwrite-btn" on:click={handleOverwriteConfirm}>
          Overwrite
        </button>
      </div>
    </div>
  </div>
{/if}

{#if showAdvancedOverlay}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="modal-backdrop" on:click={handleCloseAdvancedOverlay}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="advanced-modal-content" on:click|stopPropagation>
      <div class="advanced-modal-header">
        <div>
          <h3 class="modal-title">Advanced Batch Generation</h3>
          <p class="advanced-modal-subtitle">
            Choose how to queue missing audio across parsed chapters only. Chapters without dialogue data are ignored.
          </p>
        </div>
        <button
          class="advanced-close-btn"
          on:click={handleCloseAdvancedOverlay}
          disabled={advancedSubmitting}
          aria-label="Close advanced batch generation"
        >
          <X size={16} />
        </button>
      </div>

      {#if loadingAdvancedPreview}
        <div class="advanced-loading">Scanning parsed chapters and checking which missing lines are still eligible for generation...</div>
      {:else if advancedPreviewError}
        <div class="advanced-error-card">
          <p>{advancedPreviewError}</p>
          <div class="advanced-actions-row">
            <button class="advanced-secondary-btn" on:click={handleCloseAdvancedOverlay}>Close</button>
            <button class="advanced-primary-btn" on:click={handleOpenAdvancedOverlay}>Retry Scan</button>
          </div>
        </div>
      {:else if advancedPreview}
        <div class="advanced-modal-body">
          <div class="advanced-summary-grid">
            <div class="advanced-summary-card">
              <span class="advanced-summary-label">Parsed Chapters</span>
              <strong>{advancedPreview.parsedChapterCount}/{advancedPreview.totalChapterCount}</strong>
              <span class="advanced-summary-note">{advancedPreview.skippedUnparsedCount} ignored</span>
            </div>
            <div class="advanced-summary-card">
              <span class="advanced-summary-label">Eligible Characters</span>
              <strong>{advancedPreview.characters.length}</strong>
              <span class="advanced-summary-note">Voiced characters with missing lines</span>
            </div>
            <div class="advanced-summary-card">
              <span class="advanced-summary-label">Missing Lines</span>
              <strong>{advancedPreview.totalMissingLines}</strong>
              <span class="advanced-summary-note">Across all parsed chapters</span>
            </div>
            <div class="advanced-summary-card">
              <span class="advanced-summary-label">Skipped For Voice</span>
              <strong>{advancedPreview.missingVoiceLineCount}</strong>
              <span class="advanced-summary-note">{advancedPreview.missingVoiceCharacterCount} character(s) missing a voice</span>
            </div>
          </div>

          {#if advancedPreview.characters.length === 0}
            <p class="advanced-empty">No voiced characters with missing audio were found across the parsed chapters.</p>
          {:else}
            <div class="advanced-section">
              <h4>Batch Mode</h4>
              <p class="advanced-section-note">Choose whether to combine by character, keep chapter boundaries, or combine by speaker source so characters sharing one assigned voice are generated together.</p>
              <div class="advanced-mode-grid">
                <label class="advanced-mode-card" class:active={advancedGenerationMode === 'character'}>
                  <input type="radio" bind:group={advancedGenerationMode} value="character" />
                  <span>
                    <span class="advanced-mode-title">Generate By Character</span>
                    <span class="advanced-mode-description">Combine each selected character's missing lines across parsed chapters into a single batch before splitting them back out.</span>
                  </span>
                </label>
                <label class="advanced-mode-card" class:active={advancedGenerationMode === 'chapter'}>
                  <input type="radio" bind:group={advancedGenerationMode} value="chapter" />
                  <span>
                    <span class="advanced-mode-title">Generate By Chapter</span>
                    <span class="advanced-mode-description">Keep chapter boundaries intact by queueing a separate batch for each selected character inside each parsed chapter.</span>
                  </span>
                </label>
                <label class="advanced-mode-card" class:active={advancedGenerationMode === 'speaker-source'}>
                  <input type="radio" bind:group={advancedGenerationMode} value="speaker-source" />
                  <span>
                    <span class="advanced-mode-title">Generate By Speaker Source</span>
                    <span class="advanced-mode-description">Combine selected lines across characters when they share the same assigned voice source, reducing individual generation runs.</span>
                  </span>
                </label>
              </div>
            </div>

            <div class="advanced-section">
              <h4>Short-Batch Filler</h4>
              <label class="advanced-checkbox-row">
                <input type="checkbox" bind:checked={advancedUseFillerForShortBatch} />
                <span>Pad short batches with filler/pretext before the real dialogue lines.</span>
              </label>
              <p class="advanced-helper">When enabled, the filler text is repeated before the actual lines until the batch clears the {CHARACTER_BATCH_TARGET}-character target.</p>
              <textarea
                class="advanced-textarea"
                bind:value={advancedFillerText}
                disabled={!advancedUseFillerForShortBatch}
                rows="4"
                placeholder="Enter reusable filler/pretext text for short batches"
              ></textarea>
            </div>

            {#if advancedGenerationMode === 'character' && selectedAdvancedUnderTargetCharacters.length > 0 && !advancedUseFillerForShortBatch}
              <div class="advanced-warning">
                {selectedAdvancedUnderTargetCharacters.length} selected character batch{selectedAdvancedUnderTargetCharacters.length === 1 ? '' : 'es'} fall below {CHARACTER_BATCH_TARGET} characters. Enable filler/pretext if you want to pad them before generation.
              </div>
            {:else if advancedGenerationMode === 'chapter' && selectedAdvancedUnderTargetChapterBatchCount > 0 && !advancedUseFillerForShortBatch}
              <div class="advanced-warning">
                {selectedAdvancedUnderTargetChapterBatchCount} selected chapter batch{selectedAdvancedUnderTargetChapterBatchCount === 1 ? '' : 'es'} fall below {CHARACTER_BATCH_TARGET} characters. Enable filler/pretext if you want to pad them.
              </div>
            {:else if advancedGenerationMode === 'speaker-source' && selectedAdvancedUnderTargetSpeakerSourceBatchCount > 0 && !advancedUseFillerForShortBatch}
              <div class="advanced-warning">
                {selectedAdvancedUnderTargetSpeakerSourceBatchCount} selected speaker-source batch{selectedAdvancedUnderTargetSpeakerSourceBatchCount === 1 ? '' : 'es'} fall below {CHARACTER_BATCH_TARGET} characters. Enable filler/pretext if you want to pad them.
              </div>
            {:else if advancedUseFillerForShortBatch}
              <div class="advanced-info">Short batches will be padded with the configured filler/pretext before the real dialogue lines are generated.</div>
            {/if}

            <div class="advanced-selection-toolbar">
              <div class="advanced-selection-meta">
                <label class="advanced-master-checkbox">
                  <input
                    bind:this={advancedSelectAllInput}
                    type="checkbox"
                    checked={advancedAllCharactersSelected}
                    on:change={(event) => handleToggleAllAdvancedCharacters((event.currentTarget as HTMLInputElement).checked)}
                  />
                  <span>All characters</span>
                </label>
                <div class="advanced-selection-summary">
                  <strong>{selectedAdvancedCharacterBatchCount} character{selectedAdvancedCharacterBatchCount === 1 ? '' : 's'} selected</strong>
                  <span class="advanced-section-note">
                    {selectedAdvancedLineCount} missing line{selectedAdvancedLineCount === 1 ? '' : 's'} across {selectedAdvancedParsedChapterCount} parsed chapter{selectedAdvancedParsedChapterCount === 1 ? '' : 's'}.
                    {#if advancedGenerationMode === 'chapter'}
                      {' '}{selectedAdvancedChapterBatchCount} chapter batch{selectedAdvancedChapterBatchCount === 1 ? '' : 'es'} will be queued.
                    {:else if advancedGenerationMode === 'speaker-source'}
                      {' '}{selectedAdvancedSpeakerSourceBatchCount} speaker-source batch{selectedAdvancedSpeakerSourceBatchCount === 1 ? '' : 'es'} will be queued.
                    {/if}
                  </span>
                </div>
                <div class="advanced-badge-row">
                  <span class="advanced-status-badge ready">
                    {selectedAdvancedReadyCharacterCount} ready as-is
                  </span>
                  {#if selectedAdvancedCharactersNeedingFillerCount > 0}
                    <span class="advanced-status-badge warning">
                      {selectedAdvancedCharactersNeedingFillerCount} {advancedUseFillerForShortBatch ? 'will be padded' : 'below 800'}
                    </span>
                  {/if}
                </div>
              </div>
              <div class="advanced-selection-actions">
                <button class="advanced-link-btn" on:click={selectAllAdvancedCharacters} disabled={advancedCharacters.length === 0}>Select All</button>
                <button class="advanced-link-btn" on:click={clearAdvancedCharacterSelection} disabled={selectedAdvancedCharacterIds.size === 0}>Clear</button>
              </div>
            </div>

            <div class="advanced-character-section">
              <div class="advanced-character-section-header">
                <span class="advanced-character-section-title">Characters To Generate</span>
                <span class="advanced-section-note">{advancedCharacters.length} available</span>
              </div>

              {#if selectedAdvancedCharacters.length > 0}
                <div class="advanced-character-preview">
                  {#each selectedAdvancedCharacters.slice(0, 8) as item (item.characterId)}
                    <span class="advanced-character-preview-pill">{item.characterName}</span>
                  {/each}
                  {#if selectedAdvancedCharacters.length > 8}
                    <span class="advanced-character-preview-pill more">+{selectedAdvancedCharacters.length - 8} more</span>
                  {/if}
                </div>
              {/if}

              <div class="advanced-character-list">
                {#each advancedCharacters as item (item.characterId)}
                  {@const isSelected = selectedAdvancedCharacterIds.has(item.characterId)}
                  {@const isUnderTarget = item.totalCharacters < CHARACTER_BATCH_TARGET}
                  {@const speakerSourceBatch = speakerSourceBatchByCharacterId.get(item.characterId)}
                  <label class="advanced-character-row" class:selected={isSelected}>
                    <input
                      type="checkbox"
                      checked={isSelected}
                      on:change={() => toggleAdvancedCharacterSelection(item.characterId)}
                    />
                    <div class="advanced-character-main">
                      <div class="advanced-character-header">
                        <div class="advanced-character-title-group">
                          <span class="advanced-character-name">{item.characterName}</span>
                          <span class="advanced-character-voice">{item.voiceInfo.displayName}</span>
                        </div>
                        <div class="advanced-character-trailing">
                          {#if advancedGenerationMode === 'character'}
                            <span class="advanced-inline-badge" class:warning={isUnderTarget} class:ready={!isUnderTarget}>
                              {#if isUnderTarget}
                                {advancedUseFillerForShortBatch ? 'Will pad' : 'Needs filler'}
                              {:else}
                                Ready
                              {/if}
                            </span>
                          {:else if advancedGenerationMode === 'chapter'}
                            <span class="advanced-inline-badge" class:warning={item.underTargetChapterBatchCount > 0} class:ready={item.underTargetChapterBatchCount === 0}>
                              {#if item.underTargetChapterBatchCount > 0}
                                {item.underTargetChapterBatchCount} short batch{item.underTargetChapterBatchCount === 1 ? '' : 'es'}
                              {:else}
                                Ready
                              {/if}
                            </span>
                          {:else}
                            <span class="advanced-inline-badge" class:warning={Boolean(speakerSourceBatch) && speakerSourceBatch.totalCharacters < CHARACTER_BATCH_TARGET} class:ready={!speakerSourceBatch || speakerSourceBatch.totalCharacters >= CHARACTER_BATCH_TARGET}>
                              {#if speakerSourceBatch && speakerSourceBatch.totalCharacters < CHARACTER_BATCH_TARGET}
                                Shared source short batch
                              {:else}
                                Ready
                              {/if}
                            </span>
                          {/if}
                        </div>
                      </div>
                      <div class="advanced-character-stats">
                        <span class="advanced-stat-pill">{item.lines.length} lines</span>
                        <span class="advanced-stat-pill">{item.totalCharacters} chars</span>
                        <span class="advanced-stat-pill">{item.chapterCount} chapters</span>
                        <span class="advanced-stat-pill">{item.chapterBatchCount} chapter batch{item.chapterBatchCount === 1 ? '' : 'es'}</span>
                      </div>

                      {#if advancedGenerationMode === 'character' && isUnderTarget}
                        <p class="advanced-character-warning" class:ready={advancedUseFillerForShortBatch}>
                          {#if advancedUseFillerForShortBatch}
                            Below {CHARACTER_BATCH_TARGET} characters. Filler/pretext will pad this batch.
                          {:else}
                            Below {CHARACTER_BATCH_TARGET} characters. Consider enabling filler/pretext.
                          {/if}
                        </p>
                      {:else if advancedGenerationMode === 'chapter' && item.underTargetChapterBatchCount > 0}
                        <p class="advanced-character-warning" class:ready={advancedUseFillerForShortBatch}>
                          {item.underTargetChapterBatchCount} chapter batch{item.underTargetChapterBatchCount === 1 ? '' : 'es'} below {CHARACTER_BATCH_TARGET} characters{advancedUseFillerForShortBatch ? '; filler/pretext will pad them.' : '.'}
                        </p>
                      {:else if advancedGenerationMode === 'speaker-source' && speakerSourceBatch && speakerSourceBatch.totalCharacters < CHARACTER_BATCH_TARGET}
                        <p class="advanced-character-warning" class:ready={advancedUseFillerForShortBatch}>
                          Shared speaker-source batch is below {CHARACTER_BATCH_TARGET} characters{advancedUseFillerForShortBatch ? '; filler/pretext will pad it.' : '.'}
                        </p>
                      {/if}
                    </div>
                  </label>
                {/each}
              </div>
            </div>

            <div class="advanced-actions-row">
              <button class="advanced-secondary-btn" on:click={handleCloseAdvancedOverlay} disabled={advancedSubmitting}>Cancel</button>
              <button
                class="advanced-primary-btn"
                on:click={handleQueueAdvancedGeneration}
                disabled={advancedSubmitting || selectedAdvancedCharacterIds.size === 0}
              >
                {#if advancedSubmitting}
                  Queueing...
                {:else}
                  Queue Missing Audio
                {/if}
              </button>
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

