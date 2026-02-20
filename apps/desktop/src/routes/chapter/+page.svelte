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
  import { scanChapters } from '$lib/services/fs';
  import { initRouteProjectContext } from '$lib/services/projectSession';
  import { getProjectInitFailurePolicy } from '$lib/services/routePolicy';
  import { get } from 'svelte/store';
  import { toolMode } from '$lib/stores/selection';
  import { isAudioActive, audioState } from '$lib/stores/audio';

  let layout = { left: 20, center: 60, right: 20 };
  let isLoading = true;
  let loadError = '';
  
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
        // In dev mode, check if chapters are already in the store
        const existingChapters = get(chapters);
        console.log('[Review] Dev mode: Using chapters from store, count:', existingChapters.length);
        
        if (existingChapters.length > 0) {
          autoSelectChapter(existingChapters);
        } else {
          console.log('[Review] Dev mode: No chapters in store, they need to be loaded via backend');
        }
      }
      
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
    <div class="review-layout">
      <div class="toc-panel">
        <TableOfContents />
      </div>
      <div class="chapter-panel">
        <ChapterView />
      </div>
      <div class="character-panel">
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
    background-color: #f5f5f5;
  }

  .loading-message {
    font-family: serif;
    font-size: 18px;
    color: #666;
    text-align: center;
    padding: 20px;
  }

  .loading-message.error {
    color: #dc2626;
  }

  .review-page {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
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
    border-right: 1px solid #eee;
    overflow: auto;
  }

  .chapter-panel {
    flex: 1;
    width: 95%;
    overflow: auto;
  }

  .character-panel {
    min-width: 300px;
    max-width: 550px;
    border-left: 1px solid #eee;
    overflow: auto;
  }
</style>

