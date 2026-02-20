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
  import { bookRootHandle, bookRoot, chapters, autoSelectChapter, bookRootAbsolutePath } from '$lib/stores/bookState';
  import { scanChapters, setRootDirHandle } from '$lib/services/fs';
  import { getStoredProjectHandle, verifyHandlePermission, updateLastAccessed } from '$lib/services/persistence';
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
      // First check if we already have a handle in the store
      let rootHandle = get(bookRootHandle);
      
      // Check if we're in dev mode (have path but no handle)
      const devModePath = get(bookRootAbsolutePath);
      const isDevMode = devModePath && !rootHandle;
      
      // If not in store and not in dev mode, try to load from persistence
      if (!rootHandle && !isDevMode) {
        console.warn('[Review] No handle in store, checking persistence...');
        const stored = await getStoredProjectHandle();
        
        if (stored && stored.handle) {
          console.log('[Review] Found stored project:', stored.name);
          // Verify we still have permission
          const hasPermission = await verifyHandlePermission(stored.handle, true);
          
          if (hasPermission) {
            console.log('[Review] Permission granted, restoring project:', stored.name);
            rootHandle = stored.handle;
            
            // Set up the stores
            setRootDirHandle(rootHandle);
            bookRootHandle.set(rootHandle);
            bookRoot.set(rootHandle.name);
            
            // Update last accessed time
            await updateLastAccessed();
          } else {
            console.error('[Review] Permission denied for stored project');
            loadError = 'Permission denied. Please select a project from the landing page.';
            isLoading = false;
            // Redirect back to landing page after a delay
            setTimeout(() => goto('/'), 2000);
            return;
          }
        } else {
          console.warn('[Review] No stored project found');
          loadError = 'No project loaded. Redirecting to landing page...';
          isLoading = false;
          // Redirect back to landing page
          setTimeout(() => goto('/'), 1500);
          return;
        }
      } else if (isDevMode) {
        console.log('[Review] Running in dev mode with path:', devModePath);
      } else {
        console.log('[Review] Using existing handle from store:', get(bookRoot));
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

