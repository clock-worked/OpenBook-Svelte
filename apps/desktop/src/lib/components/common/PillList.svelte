<script lang="ts">
  import { Plus, X } from 'lucide-svelte';
  import { createEventDispatcher } from 'svelte';

  export let available: string[] = [];
  export let selected: string[] = [];
  export let maxPills: number | undefined = undefined;
  export let placeholder: string = 'Search...';
  export let addButtonLabel: string = 'Add item';
  export let allowDirectEntry: boolean = false;
  export let getColorForItem: ((item: string) => string | null) | undefined = undefined;
  export let readonly: boolean = false;

  const dispatch = createEventDispatcher<{ add: string; remove: string }>();

  let showInput = false;
  let searchText = '';
  let hoveredPill: string | null = null;

  $: filtered = available.filter(
    (item) =>
      !selected.includes(item) &&
      item.toLowerCase().includes(searchText.toLowerCase())
  );

  $: canAddMore = maxPills === undefined || selected.length < maxPills;

  function toggleInput() {
    if (canAddMore) {
      showInput = !showInput;
      if (!showInput) {
        searchText = '';
      }
    }
  }

  function selectItem(item: string) {
    dispatch('add', item);
    searchText = '';
    if (maxPills === 1) {
      showInput = false;
    }
  }

  function removeItem(item: string) {
    dispatch('remove', item);
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      if (allowDirectEntry && searchText.trim()) {
        // Direct entry mode: create pill from text input
        selectItem(searchText.trim());
      } else if (filtered.length > 0) {
        // Search mode: select first filtered item
        selectItem(filtered[0]);
      }
    } else if (e.key === 'Escape') {
      showInput = false;
      searchText = '';
    }
  }
</script>

<style>
  .pill-list-container {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    background: var(--app-surface-hover);
    border: 1px solid var(--app-border);
    border-radius: 16px;
    font-size: 13px;
    color: var(--app-text);
    transition: all 0.15s ease;
    position: relative;
    font-weight: 500;
  }

  .pill:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    filter: brightness(0.95);
  }

  .pill-text {
    user-select: none;
  }

  .remove-btn {
    display: none;
    width: 16px;
    height: 16px;
    padding: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    color: var(--app-text-muted);
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    transition: background-color 0.15s ease;
  }

  .pill:hover .remove-btn {
    display: flex;
  }

  .remove-btn:hover {
    background: var(--app-surface-active);
    color: var(--app-text);
  }

  .plus-btn {
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px dashed var(--app-text-subtle);
    background: transparent;
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s ease;
    flex-shrink: 0;
  }

  .plus-btn:hover:not(:disabled) {
    background: var(--app-surface-hover);
    border-color: var(--app-text-muted);
  }

  .plus-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .input-container {
    position: relative;
    display: inline-flex;
    flex-direction: column;
  }

  .search-input {
    padding: 4px 8px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    font-size: 13px;
    outline: none;
    min-width: 180px;
    background: var(--app-surface-raised);
    color: var(--app-text);
  }

  .search-input:focus {
    border-color: var(--app-primary);
    box-shadow: var(--app-focus-ring);
  }

  .dropdown {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    max-height: 200px;
    overflow-y: auto;
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 6px;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    z-index: 1000;
  }

  .dropdown-item {
    padding: 8px 12px;
    cursor: pointer;
    font-size: 13px;
    color: var(--app-text);
    transition: background-color 0.15s ease;
  }

  .dropdown-item:hover {
    background: var(--app-surface-hover);
  }

  .no-results {
    padding: 8px 12px;
    font-size: 13px;
    color: var(--app-text-muted);
    text-align: center;
  }
</style>

<div class="pill-list-container">
  {#each selected as item, index (`${item}-${index}`)}
    {@const color = getColorForItem ? getColorForItem(item) : null}
    {@const bgColor = color ? color : (item === 'Narrator' ? 'var(--app-text-subtle)' : 'var(--app-surface-hover)')}
    {@const borderColor = color ? color : (item === 'Narrator' ? 'var(--app-text-muted)' : 'var(--app-border)')}
    <div
      class="pill"
      role="listitem"
      style={`background: ${bgColor}; border-color: ${borderColor}; color: var(--app-text);`}
      on:mouseenter={() => (hoveredPill = item)}
      on:mouseleave={() => (hoveredPill = null)}
    >
      <span class="pill-text">{item}</span>
      {#if !readonly}
        <button
          class="remove-btn"
          title="Remove {item}"
          aria-label="Remove {item}"
          on:click={() => removeItem(item)}
        >
          <X size={12} />
        </button>
      {/if}
    </div>
  {/each}

  {#if !readonly}
    {#if showInput}
      <div class="input-container">
        <input
          class="search-input"
          type="text"
          {placeholder}
          bind:value={searchText}
          on:keydown={handleKeydown}
        />
        {#if !allowDirectEntry}
          {#if searchText && filtered.length > 0}
            <div class="dropdown" role="listbox">
              {#each filtered as item (item)}
                <div 
                  class="dropdown-item" 
                  role="option"
                  aria-selected="false"
                  tabindex="0"
                  on:click={() => selectItem(item)}
                  on:keydown={(e) => e.key === 'Enter' && selectItem(item)}
                >
                  {item}
                </div>
              {/each}
            </div>
          {:else if searchText}
            <div class="dropdown">
              <div class="no-results">No results found</div>
            </div>
          {/if}
        {/if}
      </div>
    {:else}
      <button
        class="plus-btn"
        title={canAddMore ? addButtonLabel : 'Maximum reached'}
        aria-label={canAddMore ? addButtonLabel : 'Maximum reached'}
        on:click={toggleInput}
        disabled={!canAddMore}
      >
        <Plus size={16} color={canAddMore ? 'var(--app-text-muted)' : 'var(--app-text-subtle)'} />
      </button>
    {/if}
  {/if}
</div>

