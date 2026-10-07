<script lang="ts">
  import { colorForCharacter, hexToRgba, rgbaToOpaqueHex, characters } from '$lib/stores/characters';
  import { bookCharacters } from '$lib/stores/bookCharacters';
  import { defaultColors } from '$lib/theme/colors';
  import ColorPicker from '$lib/components/common/ColorPicker.svelte';
  import { Trash2, CornerDownRight } from 'lucide-svelte';

  export let items: Array<{ character: { name: string; color: string | null; voice?: any }, origIndex: number }> = [];
  export let openColorIndex: number | null;
  export let selectionActive: boolean;
  export let DISPLAY_ALPHA: number = 1.0;
  export let lineCounts: Map<string, number> = new Map();
  export let jumpLineCounts: Map<string, number> = new Map();

  export let onSelect: (name: string) => void;
  export let onToggleColor: (index: number) => void;
  export let onSetColor: (index: number, opaqueColor: string | null) => void;
  export let onApplyToSelection: (name: string) => void;
  export let onMerge: (sourceIndex: number, targetIndex: number) => void;
  export let onDropBookCharacter: (name: string) => void;
  export let onDelete: (index: number) => void;
  export let onJumpToNext: (name: string) => void;

  // Subscribe to both stores to force re-render when colors change
  $: charsVersion = $characters;
  $: bookCharsVersion = $bookCharacters;

  // Create a reactive map of character colors
  $: colorMap = new Map<string, string>(
    items.map(item => {
      // Check book-level first
      const bookChar = $bookCharacters?.characters?.find(c => c.name === item.character.name);
      if (bookChar?.color) return [item.character.name, bookChar.color];

      // Check chapter-level
      const chapterChar = $characters?.characters?.find(c => c.name === item.character.name);
      if (chapterChar?.color) return [item.character.name, chapterChar.color];

      // Fall back to hash-based default
      return [item.character.name, colorForCharacter(item.character.name)];
    })
  );

  // Helper function to get current color from the reactive map
  function getCurrentColor(name: string): string {
    return colorMap.get(name) || colorForCharacter(name);
  }

  type GenderTone = 'male' | 'female' | 'unknown';

  function normalizeGenderTone(value: unknown): GenderTone {
    const normalized = String(value ?? '').trim().toLowerCase();
    if (normalized === 'm' || normalized === 'male') return 'male';
    if (normalized === 'f' || normalized === 'female') return 'female';
    return 'unknown';
  }

  function getGenderGradient(name: string): string {
    const bookChar = $bookCharacters?.characters?.find((character) => character.name === name);
    const chapterChar = $characters?.characters?.find((character) => character.name === name);
    const tone = normalizeGenderTone(bookChar?.gender ?? chapterChar?.gender ?? null);

    if (tone === 'male') {
      return 'linear-gradient(90deg, rgba(168, 218, 220, 0.30) 0%, var(--app-surface-raised) 78%)';
    }

    if (tone === 'female') {
      return 'linear-gradient(90deg, rgba(255, 193, 204, 0.30) 0%, var(--app-surface-raised) 78%)';
    }

    return 'linear-gradient(90deg, rgba(179, 156, 208, 0.22) 0%, var(--app-surface-raised) 78%)';
  }
</script>

<style>
  .character-row {
    border: 1px solid var(--app-border-subtle);
    background: var(--gender-gradient, var(--app-surface-raised));
    padding: 8px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 8px;
    position: relative;
    cursor: pointer;
    -webkit-app-region: no-drag;
    box-sizing: border-box;
    transition: border-color 120ms ease, box-shadow 120ms ease;
  }

  .character-row:hover {
    border-color: var(--app-border-strong);
    box-shadow: var(--app-shadow-sm);
  }

  .line-count-badge {
    font-size: 12px;
    color: #111827 !important;
    -webkit-text-fill-color: #111827;
    padding: 2px 8px;
    border-radius: 10px;
    white-space: nowrap;
    flex-shrink: 0;
    cursor: pointer;
    border: 1px solid var(--app-border-subtle);
    min-width: 28px;
    text-align: center;
  }

  .line-count-badge:hover {
    opacity: 0.85;
  }

  .name-display {
    flex: 1;
    font-size: 14px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .name-text {
    flex: 1;
  }

  .icon-btn {
    width: 24px;
    height: 24px;
    border: none;
    background: transparent;
    color: var(--app-text-muted);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    padding: 0;
    border-radius: 4px;
    flex-shrink: 0;
  }

  .icon-btn:hover {
    background: var(--app-surface-hover);
    color: var(--app-primary-text);
  }

  .apply-btn {
    font-size: 13px;
  }

  .hover-btn {
    opacity: 0;
    pointer-events: none;
    transition: opacity 120ms ease;
  }

  .character-row:hover .hover-btn {
    opacity: 1;
    pointer-events: auto;
  }

  .color-picker-container {
    position: absolute;
    top: 34px;
    left: 0px;
  }

  .chapter-list-drop {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
</style>

{#key `${charsVersion}-${bookCharsVersion}`}
  <div
    class="chapter-list-drop"
    role="region"
    aria-label="Chapter characters"
    on:dragover={(e) => {
      const bookName = e.dataTransfer?.getData('application/x-openbook-book-character-name') || '';
      if (!bookName) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    }}
    on:drop={(e) => {
      const bookName = e.dataTransfer?.getData('application/x-openbook-book-character-name') || '';
      if (!bookName) return;
      e.preventDefault();
      e.stopPropagation();
      onDropBookCharacter(bookName);
    }}
  >
    {#each items as item (item.character.name)}
      <div class="character-row" draggable="true"
           role="button"
           tabindex="0"
           aria-label={`Select ${item.character.name}`}
           style={`--gender-gradient:${getGenderGradient(item.character.name)};`}
           on:click={() => onSelect(item.character.name)}
           on:keydown={(e) => { if (e.key === 'Enter') onSelect(item.character.name); }}
           on:dragstart={(e) => {
              if (!e.dataTransfer) return;
              e.dataTransfer.setData('application/x-openbook-chapter-character-index', String(item.origIndex));
              e.dataTransfer.setData('text/plain', String(item.origIndex));
              e.dataTransfer.effectAllowed = 'move';
            }}
            on:dragover={(e) => {
              const hasBookName = !!(e.dataTransfer?.getData('application/x-openbook-book-character-name') || '');
              e.preventDefault();
              if (e.dataTransfer) e.dataTransfer.dropEffect = hasBookName ? 'copy' : 'move';
            }}
            on:drop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const droppedBookName = e.dataTransfer?.getData('application/x-openbook-book-character-name') || '';
              if (droppedBookName) {
                onDropBookCharacter(droppedBookName);
                return;
              }
              const sourceIndexRaw =
                e.dataTransfer?.getData('application/x-openbook-chapter-character-index')
                || e.dataTransfer?.getData('text/plain')
                || '-1';
              const sourceIndex = parseInt(sourceIndexRaw, 10);
              if (!Number.isNaN(sourceIndex) && sourceIndex >= 0) {
                onMerge(sourceIndex, item.origIndex);
              }
            }}>
        <button
          class="line-count-badge"
          title="Change color"
          on:click|stopPropagation={() => onToggleColor(item.origIndex)}
          style={`background:${hexToRgba(getCurrentColor(item.character.name), 0.58)};`}>
          {#if lineCounts.has(item.character.name)}
            {lineCounts.get(item.character.name)}
          {:else}
            &nbsp;
          {/if}
        </button>
        <div class="name-display">
          <span class="name-text">{item.character.name}</span>
        </div>
        {#if (jumpLineCounts.get(item.character.name) || 0) > 0}
          <button class="icon-btn hover-btn" title="Jump to next" aria-label="Jump to next" on:click|stopPropagation={() => onJumpToNext(item.character.name)}>
            <CornerDownRight size={16} />
          </button>
        {/if}
        {#if selectionActive}
          <button class="apply-btn" on:click|stopPropagation={() => onApplyToSelection(item.character.name)}>Apply</button>
        {/if}
        <button class="icon-btn hover-btn" title="Delete character" aria-label="Delete character" on:click|stopPropagation={() => onDelete(item.origIndex)}>
          <Trash2 size={16} />
        </button>
        {#if openColorIndex === item.origIndex}
          <div class="color-picker-container">
            <ColorPicker value={rgbaToOpaqueHex(getCurrentColor(item.character.name), DISPLAY_ALPHA)}
                         alpha={DISPLAY_ALPHA}
                         on:select={(e) => onSetColor(item.origIndex, e.detail.color)} />
          </div>
        {/if}
      </div>
    {/each}
  </div>
{/key}
