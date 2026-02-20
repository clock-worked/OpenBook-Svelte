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
    color: #6b7280;
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
  }

  .caret-btn:hover {
    background-color: rgba(0,0,0,0.05);
  }

  .book-list-panel {
    border: 1px solid #eee;
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
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
    color: #111;
    background: #f3f4f6;
    line-height: 1.2;
  }

  .book-pill.is-selected {
    box-shadow: inset 0 0 0 2px rgba(0,0,0,0.2);
  }

  .delete-btn {
    position: absolute;
    top: -6px;
    right: -6px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 1px solid rgba(0,0,0,0.2);
    background: #fff;
    color: #6b7280;
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
    color: #777;
    font-size: 14px;
  }
</style>

<div class="section-block">
  <div class="section-header-row">
    <button class="caret-btn" on:click={onToggle} aria-label="Toggle book characters">
      {#if open}
        <ChevronDown size={16} color="#6b7280" />
      {:else}
        <ChevronRight size={16} color="#6b7280" />
      {/if}
    </button>
    <span>Book Characters</span>
  </div>
  {#if open}
    <div class="book-list-panel">
      {#if items.length}
        <div class="book-pill-list">
          {#each items as item (item.index)}
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
                style={`background:${resolveColor(item.character.name, item.character.color ?? null)};`}
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
