<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { currentChapter, bookRoot, currentScript, chapters } from '$lib/stores/bookState';
  import { characters, colorForCharacter, rgbaToOpaqueHex, buildNameToIdMap, buildIdToNameMap } from '$lib/stores/characters';
  import { voices } from '$lib/stores/speakers';
  import { checkAudioExistsForCharacter, checkAudioExistsForLine, generateAudioForCharacter, readManifest, deleteAudioLineForCharacter } from '$lib/services/audio';
  import { Headphones, Loader, Scissors, FileText, BookOpen } from 'lucide-svelte';
  import type { DialogueLine, Character } from '$lib/types';
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

  let generatingCharacter: string | null = null;
  let generationProgress = { current: 0, total: 0 };
  let showOverwriteModal = false;
  let overwriteCharacterId: string | null = null;
  let overwriteClipCount = 0;
  let pruningStale = false;
  let audioClipCounts = new Map<string, number>();
  let queueProcessing = false;
  type QueuedJobPayload = {
    characterId: string;
    characterName: string;
    lines: DialogueLine[];
    cumulativeCharacterCounts: number[];
    totalCharacters: number;
    voiceInfo: { voiceId: string; provider: string; displayName: string; characterId: string };
    chapterTitle: string;
    sourceFile: string;
  };

  const queuedJobs = new Map<string, QueuedJobPayload>();

  function countLineCharacters(line: DialogueLine): number {
    return Math.max(1, String((line as any)?.text ?? '').length);
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
      // Handle both v2.0 format (characterId) and v1.0 format (chosenSpeaker)
      let characterId: string | null = null;
      
      if ('characterId' in line && line.characterId) {
        characterId = String(line.characterId);
      } else if ('chosenSpeaker' in line && line.chosenSpeaker) {
        // Legacy: convert name to ID using centralized helper
        characterId = characterNameToId.get(String(line.chosenSpeaker).toLowerCase()) || null;
      }
      
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

    const missingVoiceCharacterIds = new Set<string>();
    let totalJobsQueued = 0;
    let totalLinesQueued = 0;
    let chaptersWithQueuedJobs = 0;
    let jobCounter = 0;

    for (const chapter of allChapters) {
      const script = await readDialogueForChapter(chapter.title);
      if (!script?.lines?.length) continue;

      const linesByCharacterId = new Map<string, DialogueLine[]>();
      for (const line of script.lines as any[]) {
        let characterId: string | null = null;

        if ('characterId' in line && line.characterId) {
          characterId = String(line.characterId);
        } else if ('chosenSpeaker' in line && line.chosenSpeaker) {
          characterId = characterNameToId.get(String(line.chosenSpeaker).toLowerCase()) || null;
        }

        if (!characterId) continue;

        const existingLines = linesByCharacterId.get(characterId) || [];
        existingLines.push(line as DialogueLine);
        linesByCharacterId.set(characterId, existingLines);
      }

      let chapterQueuedJobs = 0;
      let chapterQueuedLines = 0;
      let chapterMissingVoiceLines = 0;

      for (const [characterId, characterLines] of linesByCharacterId.entries()) {
        if (characterLines.length === 0) continue;

        const characterName = characterIdToName.get(characterId) || characterId;
        const voiceInfo = getVoiceForCharacter(characterId);
        if (!voiceInfo) {
          missingVoiceCharacterIds.add(characterId);
          chapterMissingVoiceLines += characterLines.length;
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

        const jobId = `${chapter.title}-${characterId}-missing-${Date.now()}-${jobCounter++}`;
        const { cumulative, total } = buildCumulativeCharacterCounts(linesToGenerate);
        queuedJobs.set(jobId, {
          characterId,
          characterName,
          lines: linesToGenerate,
          cumulativeCharacterCounts: cumulative,
          totalCharacters: total,
          voiceInfo,
          chapterTitle: chapter.title,
          sourceFile: chapter.path || `${chapter.title}/chapter.txt`,
        });

        enqueueAudioJob({
          id: jobId,
          characterId,
          characterName,
          chapterTitle: chapter.title,
          generationMode: 'missing',
          total: linesToGenerate.length,
          totalCharacters: total,
        });

        totalJobsQueued += 1;
        totalLinesQueued += linesToGenerate.length;
        chapterQueuedJobs += 1;
        chapterQueuedLines += linesToGenerate.length;
      }

      if (chapterQueuedLines > 0 || chapterMissingVoiceLines > 0) {
        setQueueChapterOverview(chapter.title, chapterQueuedLines, chapterMissingVoiceLines);
      }

      if (chapterQueuedJobs > 0) {
        chaptersWithQueuedJobs += 1;
      }
    }

    if (totalJobsQueued === 0) {
      const missingVoicesNote = missingVoiceCharacterIds.size > 0
        ? ` ${missingVoiceCharacterIds.size} character(s) are missing voice assignments.`
        : '';
      alert(`No missing audio lines found across this book.${missingVoicesNote}`);
      return;
    }

    if (!queueProcessing) {
      void processQueue();
    }

    const missingVoicesNote = missingVoiceCharacterIds.size > 0
      ? ` Skipped ${missingVoiceCharacterIds.size} character(s) with no assigned voice.`
      : '';
    alert(
      `Queued ${totalLinesQueued} missing line${totalLinesQueued === 1 ? '' : 's'} ` +
      `across ${totalJobsQueued} job${totalJobsQueued === 1 ? '' : 's'} in ` +
      `${chaptersWithQueuedJobs} chapter${chaptersWithQueuedJobs === 1 ? '' : 's'}.${missingVoicesNote}`
    );
  }

  async function handlePruneStaleAudio() {
    const ch = get(currentChapter);
    const scr = get(currentScript);
    if (!ch || !scr) return;

    pruningStale = true;
    try {
      let removedTotal = 0;
      for (const item of sortedCharactersById) {
        removedTotal += await pruneStaleCharacterAudio(item.id, item.name, scr, ch.title, { suppressRefresh: true });
      }

      await loadAudioClipCounts();
      triggerAudioUpdate();

      if (removedTotal > 0) {
        alert(`Removed ${removedTotal} stale audio clip${removedTotal === 1 ? '' : 's'}.`);
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
  function getVoiceForCharacter(characterId: string): { voiceId: string; provider: string; displayName: string; characterId: string } | null {
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
      sourceFile: ch.path || '',
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
        () => isJobCanceled(next.id)
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

