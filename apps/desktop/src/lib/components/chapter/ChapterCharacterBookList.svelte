<script lang="ts">
  import { ChevronDown, ChevronRight } from 'lucide-svelte';

  export let open: boolean;
  export let items: Array<{ character: { name: string; color: string | null; count?: number }, index: number }> = [];
  export let selectedName: string | null = null;
  export let onToggle: () => void;
  export let onSelect: (name: string) => void;
  export let onDelete: (name: string, count: number) => void;
  export let onMerge: (sourceName: string, targetName: string) => void;
  export let resolveColor: (name: string, colorOverride?: string | null) => string;

  function textColorForBackground(background: string): string {
    const hex = background.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)?.[1];
    if (!hex) return 'var(--app-text)';

    const expanded = hex.length === 3
      ? hex.split('').map((character) => character + character).join('')
      : hex;
    const red = Number.parseInt(expanded.slice(0, 2), 16);
    const green = Number.parseInt(expanded.slice(2, 4), 16);
    const blue = Number.parseInt(expanded.slice(4, 6), 16);
    const luminance = (red * 299 + green * 587 + blue * 114) / 1000;

    return luminance > 155 ? '#1f1f1f' : '#f4f1f8';
  }
</script>

<style>
  .section-block {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .section-header-row {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--app-text-muted);
    font-weight: 600;
    font-size: 13px;
  }

  .caret-btn {
    width: 22px;
    height: 22px;
    border: none;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    color: var(--app-text-muted);
  }

  .caret-btn:hover {
    background-color: var(--app-surface-hover);
    color: var(--app-text);
  }

  .book-list-panel {
    border: 1px solid var(--app-border-subtle);
    background: var(--app-surface-subtle);
    border-radius: 6px;
    padding: 8px;
    box-sizing: border-box;
  }

  .book-pill-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .pill-wrapper {
    position: relative;
    display: inline-flex;
    align-items: center;
  }

  .book-pill {
    border: 1px solid var(--app-border-subtle);
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
    color: var(--app-text);
    background: var(--app-surface-raised);
    line-height: 1.2;
  }

  .book-pill.is-selected {
    box-shadow: inset 0 0 0 2px var(--app-primary);
  }

  .delete-btn {
    position: absolute;
    top: -6px;
    right: -6px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 1px solid var(--app-border);
    background: var(--app-surface-raised);
    color: var(--app-text-muted);
    font-size: 11px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
  }

  .pill-wrapper:hover .delete-btn {
    opacity: 1;
    pointer-events: auto;
  }

  .no-characters {
    color: var(--app-text-muted);
    font-size: 14px;
  }
</style>

<div class="section-block">
  <div class="section-header-row">
    <button class="caret-btn" on:click={onToggle} aria-label="Toggle book characters">
      {#if open}
        <ChevronDown size={16} />
      {:else}
        <ChevronRight size={16} />
      {/if}
    </button>
    <span>Book Characters</span>
  </div>
  {#if open}
    <div class="book-list-panel">
      {#if items.length}
        <div class="book-pill-list">
          {#each items as item (item.index)}
            {@const background = resolveColor(item.character.name, item.character.color ?? null)}
            <div
              class="pill-wrapper"
              draggable="true"
              role="button"
              tabindex="0"
              on:dragstart={(e) => {
                if (!e.dataTransfer) return;
                e.dataTransfer.setData('application/x-openbook-book-character-name', item.character.name);
                e.dataTransfer.setData('text/plain', item.character.name);
                e.dataTransfer.effectAllowed = 'move';
              }}
              on:dragover={(e) => {
                e.preventDefault();
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
              }}
              on:drop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                const sourceName = e.dataTransfer?.getData('text/plain') || '';
                if (sourceName && sourceName !== item.character.name) {
                  onMerge(sourceName, item.character.name);
                }
              }}
            >
              <button
                class={`book-pill ${selectedName === item.character.name ? 'is-selected' : ''}`}
                style={`background:${background};color:${textColorForBackground(background)};`}
                draggable="true"
                on:dragstart={(e) => {
                  if (!e.dataTransfer) return;
                  e.dataTransfer.setData('application/x-openbook-book-character-name', item.character.name);
                  e.dataTransfer.setData('text/plain', item.character.name);
                  e.dataTransfer.effectAllowed = 'move';
                }}
                on:dragover={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
                }}
                on:drop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  const sourceName = e.dataTransfer?.getData('text/plain') || '';
                  if (sourceName && sourceName !== item.character.name) {
                    onMerge(sourceName, item.character.name);
                  }
                }}
                on:click={() => onSelect(item.character.name)}
                title={item.character.name}
              >
                {item.character.name}
              </button>
              <button
                class="delete-btn"
                title={`Delete ${item.character.name}`}
                on:click|stopPropagation={() => onDelete(item.character.name, item.character.count || 0)}
              >
                x
              </button>
            </div>
          {/each}
        </div>
      {:else}
        <p class="no-characters">No book characters detected</p>
      {/if}
    </div>
  {/if}
</div>
