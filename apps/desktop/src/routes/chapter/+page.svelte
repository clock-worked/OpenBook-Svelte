<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import TableOfContents from '$lib/components/chapter/TableOfContents.svelte';
  import ChapterView from '$lib/components/chapter/ChapterView.svelte';
  import ChapterCharacterPanel from '$lib/components/chapter/ChapterCharacterPanel.svelte';
  import AudioPanel from '$lib/components/chapter/AudioPanel.svelte';
  import AudioQueueOverlay from '$lib/components/chapter/AudioQueueOverlay.svelte';
  import PlaybackBar from '$lib/components/chapter/PlaybackBar.svelte';
  import Toolbar from '$lib/components/chapter/Toolbar.svelte';
  import { bookRoot, chapters, autoSelectChapter } from '$lib/stores/bookState';
  import { listChaptersFromBackend, readSettings, scanChapters } from '$lib/services/fs';
  import { initRouteProjectContext } from '$lib/services/projectSession';
  import { getProjectInitFailurePolicy } from '$lib/services/routePolicy';
  import { get } from 'svelte/store';
  import { toolMode } from '$lib/stores/selection';
  import { isAudioActive, audioState } from '$lib/stores/audio';
  import { parserHints } from '$lib/stores/settings';

  const MIN_CHARACTER_PANEL_WIDTH = 300;
  const MAX_CHARACTER_PANEL_WIDTH = 700;
  const CHARACTER_PANEL_RESIZE_STEP = 20;

  let layout = { left: 20, center: 60, right: 20 };
  let reviewLayoutElement: HTMLDivElement;
  let characterPanelWidth = 550;
  let isLoading = true;
  let loadError = '';

  function resizeCharacterPanel(width: number) {
    characterPanelWidth = Math.max(
      MIN_CHARACTER_PANEL_WIDTH,
      Math.min(MAX_CHARACTER_PANEL_WIDTH, width)
    );
  }

  function handleCharacterPanelResize(event: PointerEvent) {
    if (!reviewLayoutElement) return;
    const layoutRight = reviewLayoutElement.getBoundingClientRect().right;
    resizeCharacterPanel(layoutRight - event.clientX);
  }

  function handleCharacterPanelResizeKeydown(event: KeyboardEvent) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const direction = event.key === 'ArrowLeft' ? 1 : -1;
    resizeCharacterPanel(characterPanelWidth + direction * CHARACTER_PANEL_RESIZE_STEP);
  }

  async function loadParserHints() {
    const settings = await readSettings(get(bookRoot));
    if (!settings?.parserHints) return;

    const defaults = get(parserHints);
    const storedHints = settings.parserHints;
    const protagonistNames = storedHints.protagonistNames?.length
      ? storedHints.protagonistNames
      : (storedHints.protagonistName ? [storedHints.protagonistName] : defaults.protagonistNames);
    parserHints.set({
      ...defaults,
      ...storedHints,
      protagonistNames,
      povMode: storedHints.povMode || defaults.povMode || 'first_person',
      learnVerbs: storedHints.learnVerbs ?? defaults.learnVerbs ?? false,
      heuristics: {
        ...(defaults.heuristics || {}),
        ...(storedHints.heuristics || {}),
      },
      manualBlockList: storedHints.manualBlockList || [],
    });
  }
  
  $: console.log('[Review] isAudioActive:', $isAudioActive, 'audioState:', {
    isPlaying: $audioState.isPlaying,
    isPaused: $audioState.isPaused,
    currentLineId: $audioState.currentLineId
  });

  onMount(async () => {
    try {
      const context = await initRouteProjectContext({
        allowDevModeWithoutHandle: true,
        requestPermission: true,
        touchLastAccessed: true,
      });

      if (!context.ok) {
        console.warn('[Review] No handle in store, checking persistence...');
        const reason = ('reason' in context ? context.reason : 'error');
        const policy = getProjectInitFailurePolicy(reason);

        if (policy.logLevel === 'error') {
          console.error('[Review] Project init failed:', reason, 'error' in context ? context.error : undefined);
        } else {
          console.warn('[Review] Project init failed:', reason);
        }

        loadError = policy.chapterMessage;
        isLoading = false;
        setTimeout(() => goto('/'), policy.delayMs);
        return;
      }

      const rootHandle = context.rootHandle;
      const isDevMode = context.isDevMode;
      if (isDevMode) {
        console.log('[Review] Running in dev mode with path:', context.devModePath);
      } else {
        console.log('[Review] Using active project:', get(bookRoot));
      }
      
      // Now we have a handle or are in dev mode, handle chapters
      if (rootHandle) {
        console.log('[Review] Scanning chapters...');
        const list = await scanChapters(rootHandle);
        console.log('[Review] Found', list.length, 'chapters');
        chapters.set(list);
        
        if (list.length > 0) {
          // Auto-select last viewed chapter or first chapter
          autoSelectChapter(list);
        } else {
          console.warn('[Review] No chapters found in the scanned directory');
        }
      } else if (isDevMode) {
        const backendChapters = await listChaptersFromBackend();
        chapters.set(backendChapters);
        if (backendChapters.length > 0) autoSelectChapter(backendChapters);
      }

      await loadParserHints();
      
      isLoading = false;
    } catch (error) {
      console.error('[Review] Error loading project:', error);
      loadError = `Failed to load project: ${error instanceof Error ? error.message : 'Unknown error'}`;
      isLoading = false;
      setTimeout(() => goto('/'), 2000);
    }
  });
</script>

{#if isLoading}
  <div class="loading-container">
    <div class="loading-message">
      {loadError || 'Loading project...'}
    </div>
  </div>
{:else if loadError}
  <div class="loading-container">
    <div class="loading-message error">
      {loadError}
    </div>
  </div>
{:else}
  <div class="review-page">
    <Toolbar />
    <div class="review-layout" bind:this={reviewLayoutElement}>
      <div class="toc-panel">
        <TableOfContents />
      </div>
      <div class="chapter-panel">
        <ChapterView />
      </div>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions a11y_no_static_element_interactions (A focusable separator supports pointer and keyboard resizing.) -->
      <div
        class="character-panel-resizer"
        role="separator"
        aria-label="Resize character panel"
        aria-orientation="vertical"
        aria-valuemin={MIN_CHARACTER_PANEL_WIDTH}
        aria-valuemax={MAX_CHARACTER_PANEL_WIDTH}
        aria-valuenow={characterPanelWidth}
        tabindex="0"
        on:pointerdown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          handleCharacterPanelResize(event);
        }}
        on:pointermove={(event) => {
          if (event.buttons > 0) handleCharacterPanelResize(event);
        }}
        on:keydown={handleCharacterPanelResizeKeydown}
      ></div>
      <div
        class="character-panel"
        style={`width: ${characterPanelWidth}px; flex-basis: ${characterPanelWidth}px;`}
      >
        {#if $toolMode === 'audio'}
          <AudioPanel />
        {:else}
          <ChapterCharacterPanel />
        {/if}
      </div>
    </div>
    {#if $toolMode === 'audio'}
      <PlaybackBar />
      <AudioQueueOverlay />
    {/if}
  </div>
{/if}

<style>
  .loading-container {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    background-color: var(--app-bg);
  }

  .loading-message {
    font-family: serif;
    font-size: 18px;
    color: var(--app-text-muted);
    text-align: center;
    padding: 20px;
  }

  .loading-message.error {
    color: var(--app-danger);
  }

  .review-page {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
    background: var(--app-bg);
    color: var(--app-text);
  }

  .review-layout {
    display: flex;
    flex: 1;
    overflow: hidden;
    min-height: 0;
  }

  .toc-panel {
    min-width: 200px;
    max-width: 300px;
    border-right: 1px solid var(--app-border-subtle);
    overflow: auto;
  }

  .chapter-panel {
    flex: 1;
    width: 95%;
    overflow: auto;
  }

  .character-panel-resizer {
    flex: 0 0 8px;
    cursor: col-resize;
    touch-action: none;
    user-select: none;
    position: relative;
    z-index: 1;
  }

  .character-panel-resizer::after {
    content: '';
    position: absolute;
    inset: 0 3px;
    background: transparent;
    transition: background-color 120ms ease;
  }

  .character-panel-resizer:hover::after,
  .character-panel-resizer:focus-visible::after {
    background: var(--app-primary);
  }

  .character-panel-resizer:focus-visible {
    outline: none;
  }

  .character-panel {
    flex: 0 0 550px;
    box-sizing: border-box;
    min-width: 300px;
    max-width: 700px;
    border-left: 1px solid var(--app-border-subtle);
    overflow: auto;
  }
</style>
