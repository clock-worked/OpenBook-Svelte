<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  import { currentChapter } from '$lib/stores/bookState';
  import CharacterChips from './CharacterChips.svelte';
  import ParagraphText from './ParagraphText.svelte';
  import type { ParagraphRun } from '$lib/types';
  import type { ToolMode } from '$lib/stores/selection';

  export let runs: ParagraphRun[] = [];
  export let hoveredCharacter: string | null = null;
  export let setHovered: (s: string | null) => void;
  export let toolMode: ToolMode = 'select';
  export let planReady = false;
  export let animateOnLoad = true;
  export let typingDelayMs = 0;
  export let highlightDelayMs = 0;
  export let revealDelayMs = 0;
  export let animationPlanVersion = 0;
  export let generatedAudioLineIds: Set<number> = new Set();
  export let generatedAudioLineTexts: Set<string> = new Set();

  const dispatch = createEventDispatcher();

  let rowVisible = false;
  let rowDelayTimeout: number | null = null;
  let lastAppliedPlanKey = '';

  function clearRowDelayTimeout() {
    if (rowDelayTimeout != null) {
      clearTimeout(rowDelayTimeout);
      rowDelayTimeout = null;
    }
  }

  function scheduleRowReveal(startDelayMs: number) {
    clearRowDelayTimeout();
    const delayMs = Math.max(0, Math.floor(startDelayMs || 0));
    if (delayMs === 0) {
      rowVisible = true;
      return;
    }

    rowVisible = false;
    rowDelayTimeout = window.setTimeout(() => {
      rowDelayTimeout = null;
      rowVisible = true;
    }, delayMs);
  }

  function applyRowPlan() {
    if (!planReady) {
      clearRowDelayTimeout();
      rowVisible = false;
      return;
    }

    if (animateOnLoad) {
      scheduleRowReveal(typingDelayMs);
      return;
    }
    scheduleRowReveal(revealDelayMs);
  }

  $: {
    const chapterTitle = $currentChapter?.title ?? null;
    if (chapterTitle) {
      const planKey = `${chapterTitle}::${animationPlanVersion}::${planReady ? 'ready' : 'pending'}`;
      if (planKey !== lastAppliedPlanKey) {
        lastAppliedPlanKey = planKey;
        applyRowPlan();
      }
    }
  }

  $: {
    if (!$currentChapter) {
      rowVisible = false;
      clearRowDelayTimeout();
    }
  }

  onDestroy(() => {
    clearRowDelayTimeout();
  });
</script>

<div class="paragraph-row" class:is-visible={rowVisible}>
  <CharacterChips
    {runs}
    {setHovered}
    {toolMode}
    on:paragraphMenu={(e) => dispatch('paragraphMenu', e.detail)}
    on:paragraphAssign={(e) => dispatch('paragraphAssign', e.detail)}
  />

  <ParagraphText
    {runs}
    {planReady}
    {animateOnLoad}
    {animationPlanVersion}
    {typingDelayMs}
    {highlightDelayMs}
    {hoveredCharacter}
    {setHovered}
    {toolMode}
    {generatedAudioLineIds}
    {generatedAudioLineTexts}
    on:lineMenu={(e) => dispatch('lineMenu', e.detail)}
    on:selectionMenu={(e) => dispatch('selectionMenu', e.detail)}
    on:joinMenu={(e) => dispatch('joinMenu', e.detail)}
    on:splitMenu={(e) => dispatch('splitMenu', e.detail)}
    on:editLine={(e) => dispatch('editLine', e.detail)}
    on:audioClick={(e) => dispatch('audioClick', e.detail)}
    on:audioContextMenu={(e) => dispatch('audioContextMenu', e.detail)}
  />
</div>

<style>
  .paragraph-row {
    display:flex;
    gap:12px;
    align-items:flex-start;
    margin:8px 0;
    opacity: 0;
    transition: none;
  }
  .paragraph-row.is-visible {
    opacity: 1;
  }
  .paragraph-row:not(.is-visible) {
    pointer-events: none;
  }
</style>


