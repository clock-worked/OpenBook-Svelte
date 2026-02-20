<script lang="ts">
  import { chapters, currentChapter } from '$lib/stores/bookState';
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { writable } from 'svelte/store';
  import { chapterStats } from '$lib/stores/stats';

  function openChapter(idx: number) {
    const list = get(chapters);
    currentChapter.set(list[idx] ?? null);
  }

  function displayTitle(title: string): string {
    // remove leading numeric prefix like "00-" or "12-"
    let result = title.replace(/^\d+\-/, '');
    
    // Convert "Chapter-7-Sword" to "Chapter 7: Sword"
    // Also handles "Prologue", "Epilogue", "Interlude-Title", etc.
    result = result.replace(/^(Chapter|Interlude|Extra)-(\d+)-(.+)/, '$1 $2: $3');
    
    // Handle cases like "Prologue-Title" or "Epilogue-Title" (no number)
    result = result.replace(/^(Prologue|Epilogue)-(.+)/, '$1: $2');
    
    // Replace remaining hyphens with spaces for better readability
    result = result.replace(/-/g, ' ');
    
    return result;
  }

  let hoveredIndex: number | null = null;
  // stats now provided by centralized store
  let tocListEl: HTMLUListElement;
  let showFade: boolean = false;
  let scrollTimer: ReturnType<typeof setTimeout> | null = null;
  let isScrolling = false;

  function refreshFade() {
    if (!tocListEl) { showFade = false; return; }
    const remaining = tocListEl.scrollHeight - tocListEl.scrollTop - tocListEl.clientHeight;
    showFade = remaining > 2; // show when not scrolled to bottom
  }

  onMount(() => {
    const ro = new ResizeObserver(() => refreshFade());
    if (tocListEl) ro.observe(tocListEl);
    // Initial calc after mount/content fill
    queueMicrotask(refreshFade);
    return () => ro.disconnect();
  });

  function handleScroll() {
    refreshFade();
    if (!tocListEl) return;
    isScrolling = true;
    if (scrollTimer) {
      clearTimeout(scrollTimer);
    }
    scrollTimer = setTimeout(() => {
      isScrolling = false;
      scrollTimer = null;
    }, 700);
  }
</script>

<div class="toc-wrap">
<ul class="toc-list" class:scrolling={isScrolling} bind:this={tocListEl} on:scroll={handleScroll}>
  {#each $chapters as ch, i}
    {#if i > 0}
      <li class="toc-divider"></li>
    {/if}
    <li class="toc-item">
      <button
        type="button"
        class="toc-button {($currentChapter && $currentChapter.title === ch.title) ? 'is-active' : ''}"
        on:mouseenter={() => hoveredIndex = i}
        on:mouseleave={() => hoveredIndex = null}
        on:click={() => openChapter(i)}
      >
        <div class="toc-icons-left">
          {#if ch.parsed}
            <span class="doc-icon" title="Script available">🗎</span>
          {/if}
        </div>
        <span class="toc-title">{displayTitle(ch.title)}</span>
        <div class="toc-icons">
          {#if ch.audio}
            <span class="audio-badge" title="Has audio">♪</span>
          {/if}
        </div>
      </button>
    </li>
  {/each}
  {#if !$chapters.length}
    <li class="toc-empty">No chapters detected</li>
  {/if}
  
  </ul>
  {#if showFade}
    <div class="fade-bottom" aria-hidden="true"></div>
  {/if}
</div>

<style>
  .toc-wrap { position:relative; height:100%; display:flex; flex-direction:column; overflow:hidden; max-width:300px; width:100%; }
  .toc-list { list-style:none; padding:0; margin:0; overflow:auto; flex:1; min-height:0; }
  .toc-item { padding:0; }
  .toc-divider { 
    height: 1px; 
    background: #e5e7eb; 
    margin: 4px 10px; 
    list-style: none;
  }
  .toc-button { width:100%; padding:8px 10px; cursor:pointer; display:flex; align-items:center; gap:8px; border:none; background:transparent; border-radius:10px; text-align:left; }
  .toc-button .toc-title { font-style: italic; }
  .toc-button.is-active { background:#e0f2ff; }
  .toc-button.is-active .toc-title { font-style: normal; font-weight: bold; }
  .toc-icons-left { display:flex; align-items:center; width:20px; }
  .doc-icon { font-size:16px; line-height:1; }
  .toc-title { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .toc-icons { display:flex; gap:8px; margin-left:auto; align-items:center; }
  /* .status-dot { width:8px; height:8px; border-radius:50%; }
  .status-dot.green { background:#4caf50; }
  .status-dot.red { background:#f44336; } */
  .audio-badge { display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:4px; background:#e0f2ff; color:#111; }
  .toc-empty { padding:8px; color:#777; }

  /* Fade overlay to hint more content */
  .fade-bottom { position:absolute; left:0; right:0; bottom:0; height:14px; pointer-events:none; background:linear-gradient(to bottom, rgba(255,255,255,0), rgba(255,255,255,1)); border-bottom-left-radius:10px; border-bottom-right-radius:10px; }

  /* Modern thin scrollbar */
  /* Firefox */
  .toc-list { scrollbar-width: none; }
  .toc-wrap:hover .toc-list, .toc-list.scrolling { scrollbar-width: thin; scrollbar-color: #cbd5e1 #f1f5f9; }
  /* WebKit */
  .toc-list::-webkit-scrollbar { width:8px; }
  .toc-list::-webkit-scrollbar-track { background:transparent; border-radius:8px; }
  .toc-list::-webkit-scrollbar-thumb { background:transparent; border-radius:8px; border:2px solid transparent; }
  .toc-wrap:hover .toc-list::-webkit-scrollbar-track, .toc-list.scrolling::-webkit-scrollbar-track { background:#f1f5f9; }
  .toc-wrap:hover .toc-list::-webkit-scrollbar-thumb, .toc-list.scrolling::-webkit-scrollbar-thumb { background:#cbd5e1; border:2px solid #f1f5f9; }
  .toc-wrap:hover .toc-list::-webkit-scrollbar-thumb:hover, .toc-list.scrolling::-webkit-scrollbar-thumb:hover { background:#94a3b8; }
</style>


