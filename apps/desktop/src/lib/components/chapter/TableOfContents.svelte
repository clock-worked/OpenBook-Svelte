<script lang="ts">
  import { chapters, currentChapter } from '$lib/stores/bookState';
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { writable } from 'svelte/store';

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
          {#if ch.reviewed}
            <span class="reviewed-icon" title="Chapter reviewed">✓</span>
          {/if}
          {#if ch.parsed}
            <span class="doc-icon" title="Script available">🗎</span>
          {/if}
        </div>
        <span class="toc-title">{displayTitle(ch.title)}</span>
        <div class="toc-icons">
          {#if ch.audio}
            <span
              class:audio-badge-complete={ch.complete}
              class="audio-badge"
              title={ch.complete ? 'Audio complete' : 'Has audio'}
            >♪</span>
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
    background: var(--app-border-subtle); 
    margin: 4px 10px; 
    list-style: none;
  }
  .toc-button { width:100%; padding:8px 10px; cursor:pointer; display:flex; align-items:center; gap:8px; border:none; background:transparent; border-radius:10px; text-align:left; color:var(--app-text); }
  .toc-button:hover { background:var(--app-surface-hover); }
  .toc-button .toc-title { font-style: italic; }
  .toc-button.is-active { background:var(--app-primary-soft); color:var(--app-text); }
  .toc-button.is-active .toc-title { font-style: normal; font-weight: bold; }
  .toc-icons-left { display:flex; align-items:center; gap:3px; min-width:20px; }
  .doc-icon { font-size:16px; line-height:1; color:var(--app-text-subtle); }
  .reviewed-icon { font-size:14px; line-height:1; color:#237a4b; font-weight:700; }
  .toc-title { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .toc-icons { display:flex; gap:8px; margin-left:auto; align-items:center; }
  .audio-badge { display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:4px; background:var(--app-primary-soft); color:var(--app-primary-text); }
  .audio-badge.audio-badge-complete { background:color-mix(in srgb, #2e8b57 22%, var(--app-surface-raised)); color:#1f5136; }
  .toc-empty { padding:8px; color:var(--app-text-muted); }

  /* Fade overlay to hint more content */
  .fade-bottom { position:absolute; left:0; right:0; bottom:0; height:14px; pointer-events:none; background:linear-gradient(to bottom, transparent, var(--app-bg)); border-bottom-left-radius:10px; border-bottom-right-radius:10px; }

  /* Modern thin scrollbar */
  /* Firefox */
  .toc-list { scrollbar-width: none; }
  .toc-wrap:hover .toc-list, .toc-list.scrolling { scrollbar-width: thin; scrollbar-color: var(--app-border-strong) var(--app-surface-subtle); }
  /* WebKit */
  .toc-list::-webkit-scrollbar { width:8px; }
  .toc-list::-webkit-scrollbar-track { background:transparent; border-radius:8px; }
  .toc-list::-webkit-scrollbar-thumb { background:transparent; border-radius:8px; border:2px solid transparent; }
  .toc-wrap:hover .toc-list::-webkit-scrollbar-track, .toc-list.scrolling::-webkit-scrollbar-track { background:var(--app-surface-subtle); }
  .toc-wrap:hover .toc-list::-webkit-scrollbar-thumb, .toc-list.scrolling::-webkit-scrollbar-thumb { background:var(--app-border-strong); border:2px solid var(--app-surface-subtle); }
  .toc-wrap:hover .toc-list::-webkit-scrollbar-thumb:hover, .toc-list.scrolling::-webkit-scrollbar-thumb:hover { background:var(--app-primary); }
</style>


