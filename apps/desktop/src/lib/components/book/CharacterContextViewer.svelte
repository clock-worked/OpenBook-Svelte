<script lang="ts">
  import { onMount } from 'svelte';
  import { ChevronLeft, ChevronRight } from 'lucide-svelte';
  
  export let characterName: string;
  export let characterId: string | null;
  export let firstAppearanceChapter: string | null;
  
  let contextText = '';
  let characterRanges: Array<{ start: number; end: number; text: string }> = [];
  let viewportStart = 0;
  let viewportEnd = 2000; // Show ~2000 chars at a time
  let maxScroll = 0;
  let loading = true;
  let error: string | null = null;
  
  const CONTEXT_CHARS = 3000; // Characters to load around first appearance
  
  async function loadCharacterContext() {
    if (!firstAppearanceChapter) {
      error = 'No first appearance chapter';
      loading = false;
      return;
    }
    
    try {
      const { readDialogueForChapter, readTextFile } = await import('$lib/services/fs');
      
      // Load dialogue to find character's lines
      const dialogue = await readDialogueForChapter(firstAppearanceChapter);
      if (!dialogue) {
        error = 'Could not load dialogue';
        loading = false;
        return;
      }
      
      // Load raw text
      const rawText = await readTextFile(`${firstAppearanceChapter}.txt`);
      if (!rawText) {
        error = 'Could not load chapter text';
        loading = false;
        return;
      }
      
      // Find character's lines and their positions
      const lines = 'lines' in dialogue ? dialogue.lines : [];
      const charLines = lines.filter(line => {
        if ('characterId' in line && characterId) {
          if (line.characterId === characterId) return true;
        }
        if ('chosenSpeaker' in line) {
          return line.chosenSpeaker === characterName;
        }
        return false;
      });
      
      if (charLines.length === 0) {
        error = 'No lines found for character';
        loading = false;
        return;
      }
      
      // Get first line's position
      const firstLine = charLines[0];
      const firstSpan = 'span' in firstLine ? firstLine.span : null;
      
      if (!firstSpan || firstSpan.start === 0 && firstSpan.end === 0) {
        // No valid span, show error
        error = 'No position data available';
        loading = false;
        return;
      }
      
      // Extract context around first appearance
      const startPos = Math.max(0, firstSpan.start - CONTEXT_CHARS);
      const endPos = Math.min(rawText.length, firstSpan.end + CONTEXT_CHARS);
      
      // Find paragraph boundaries near our range
      let actualStart = startPos;
      while (actualStart > 0 && rawText[actualStart] !== '\n') actualStart--;
      if (actualStart > 0) actualStart++; // Skip the newline
      
      let actualEnd = endPos;
      while (actualEnd < rawText.length && rawText[actualEnd] !== '\n') actualEnd++;
      
      contextText = rawText.slice(actualStart, actualEnd);
      maxScroll = Math.max(0, contextText.length - 2000);
      
      // Build character ranges (relative to contextText)
      characterRanges = charLines
        .filter(line => {
          const span = 'span' in line ? line.span : null;
          if (!span || span.start === 0 && span.end === 0) return false;
          return span.start >= actualStart && span.end <= actualEnd;
        })
        .map(line => {
          const span = ('span' in line ? line.span : null)!;
          const text = 'text' in line ? line.text : '';
          return {
            start: span.start - actualStart,
            end: span.end - actualStart,
            text
          };
        });
      
      // Center viewport on first character line
      if (characterRanges.length > 0) {
        const firstCharPos = characterRanges[0].start;
        viewportStart = Math.max(0, firstCharPos - 500);
        viewportEnd = viewportStart + 2000;
      }
      
      loading = false;
    } catch (err) {
      console.error('Error loading character context:', err);
      error = 'Failed to load context';
      loading = false;
    }
  }
  
  function scrollBackward() {
    viewportStart = Math.max(0, viewportStart - 500);
    viewportEnd = viewportStart + 2000;
  }
  
  function scrollForward() {
    if (viewportEnd < contextText.length) {
      viewportStart = Math.min(maxScroll, viewportStart + 500);
      viewportEnd = viewportStart + 2000;
    }
  }
  
  // Build display text with character highlights
  $: displaySegments = (() => {
    if (!contextText) return [];
    
    const viewText = contextText.slice(viewportStart, viewportEnd);
    const segments: Array<{ text: string; isCharacter: boolean }> = [];
    
    // Find character ranges that overlap with viewport
    const relevantRanges = characterRanges.filter(range => 
      range.start < viewportEnd && range.end > viewportStart
    );
    
    if (relevantRanges.length === 0) {
      return [{ text: viewText, isCharacter: false }];
    }
    
    let cursor = 0;
    for (const range of relevantRanges) {
      const relStart = Math.max(0, range.start - viewportStart);
      const relEnd = Math.min(viewText.length, range.end - viewportStart);
      
      // Add text before this character's speech
      if (relStart > cursor) {
        segments.push({ text: viewText.slice(cursor, relStart), isCharacter: false });
      }
      
      // Add character's speech
      if (relEnd > relStart) {
        segments.push({ text: viewText.slice(relStart, relEnd), isCharacter: true });
      }
      
      cursor = relEnd;
    }
    
    // Add remaining text
    if (cursor < viewText.length) {
      segments.push({ text: viewText.slice(cursor), isCharacter: false });
    }
    
    return segments;
  })();
  
  onMount(() => {
    loadCharacterContext();
  });
</script>

<div class="context-viewer">
  {#if loading}
    <div class="context-loading">
      <div class="spinner"></div>
      <span>Loading context...</span>
    </div>
  {:else if error}
    <div class="context-error">
      <span>{error}</span>
    </div>
  {:else}
    <div class="context-controls">
      <button 
        class="nav-btn" 
        on:click={scrollBackward}
        disabled={viewportStart === 0}
        title="Scroll backward"
      >
        <ChevronLeft size={16} />
      </button>
      <span class="context-label">
        {firstAppearanceChapter ? `First appears in: ${firstAppearanceChapter.replace(/^\d+-/, '').replace(/-/g, ' ')}` : 'Context'}
      </span>
      <button 
        class="nav-btn" 
        on:click={scrollForward}
        disabled={viewportEnd >= contextText.length}
        title="Scroll forward"
      >
        <ChevronRight size={16} />
      </button>
    </div>
    
    <div class="context-text">
      {#each displaySegments as segment}
        {#if segment.isCharacter}
          <span class="character-dialogue">{segment.text}</span>
        {:else}
          <span>{segment.text}</span>
        {/if}
      {/each}
      
      {#if viewportStart > 0}
        <div class="scroll-indicator start">...</div>
      {/if}
      {#if viewportEnd < contextText.length}
        <div class="scroll-indicator end">...</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .context-viewer {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 0;
  }
  
  .context-loading,
  .context-error {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 16px;
    color: #6b7280;
    font-size: 13px;
  }
  
  .context-error {
    color: #dc2626;
  }
  
  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid #e5e7eb;
    border-top-color: #3b82f6;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  
  .context-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
  }
  
  .context-label {
    flex: 1;
    font-size: 12px;
    font-weight: 600;
    color: #4b5563;
    text-align: center;
  }
  
  .nav-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 4px;
    border: 1px solid #d1d5db;
    background: #ffffff;
    border-radius: 4px;
    cursor: pointer;
    color: #374151;
    transition: all 0.15s ease;
  }
  
  .nav-btn:hover:not(:disabled) {
    background: #f9fafb;
    border-color: #9ca3af;
  }
  
  .nav-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  
  .context-text {
    position: relative;
    padding: 12px;
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.6;
    color: #374151;
    white-space: pre-wrap;
    max-height: 300px;
    overflow-y: auto;
  }
  
  .character-dialogue {
    background: #dbeafe;
    color: #1e40af;
    padding: 1px 2px;
    border-radius: 2px;
    font-weight: 500;
  }
  
  .scroll-indicator {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    font-size: 18px;
    color: #9ca3af;
    pointer-events: none;
  }
  
  .scroll-indicator.start {
    top: 0;
  }
  
  .scroll-indicator.end {
    bottom: 0;
  }
</style>

