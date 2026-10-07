<script lang="ts">
  import type { Character, Gender } from '$lib/types';
  import { bookCharacters } from '$lib/stores/bookCharacters';
  import { colorForCharacter } from '$lib/stores/characters';
  import PillList from '$lib/components/common/PillList.svelte';
  import { Plus, X } from 'lucide-svelte';

  // Identity-only props from the panel; the card re-resolves the live record
  // from the folder-loaded store so renames/edits reflect without a panel
  // round-trip (design: "subscribes to $bookCharacters itself").
  export let character: Character | null = null;
  export let chapterLineCount: number = 0;
  export let onSetTitle: (newName: string) => void;
  export let onSetGender: (gender: Gender) => void;
  export let onAddAlias: (alias: string) => void;
  export let onRemoveAlias: (alias: string) => void;
  export let onAddDescriptor: (descriptor: string) => void;
  export let onRemoveDescriptor: (descriptor: string) => void;

  const genderOptions: Gender[] = ['Male', 'Female', 'Unknown'];

  // Fresh record by identity (falls back to the prop if the store lags).
  $: liveCharacter = character
    ? ($bookCharacters.characters.find((c) => c.name === character.name) ?? character)
    : null;

  // === Title editor (R7: click → input; Enter/blur commit; Escape cancel;
  // empty → revert + stay in edit) ===
  let titleEditing = false;
  let titleValue = '';
  let titleError = false;
  let titleInputEl: HTMLInputElement | null = null;

  $: if (titleEditing && titleInputEl) {
    titleInputEl.focus();
    titleInputEl.select();
  }

  function startTitleEdit(): void {
    if (!liveCharacter) return;
    titleValue = liveCharacter.name;
    titleError = false;
    titleEditing = true;
  }

  function commitTitle(): void {
    if (!titleEditing || !liveCharacter) return;
    titleEditing = false;
    const trimmed = titleValue.trim();
    if (!trimmed) {
      // Empty → revert + stay in edit with a subtle red border.
      titleValue = liveCharacter.name;
      titleError = true;
      titleEditing = true;
      return;
    }
    if (trimmed === liveCharacter.name) return;
    onSetTitle(trimmed);
  }

  function cancelTitle(): void {
    if (!liveCharacter) return;
    titleValue = liveCharacter.name;
    titleError = false;
    titleEditing = false;
  }

  // === Stats: three-state rendering (bold > 0 / muted 0 / muted — for
  // missing). Never render a missing stat as 0. ===
  function statText(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—';
    return String(value);
  }
  function statMuted(value: number | null | undefined): boolean {
    return value === null || value === undefined || value === 0;
  }

  // === Aliases: per-item color via the shared color helper ===
  function aliasColor(alias: string): string | null {
    return colorForCharacter(alias);
  }

  // === Descriptors: bulleted list; quiet hover-revealed + to add ===
  let descriptorAdding = false;
  let newDescriptor = '';
  let descriptorNoteVisible = false;

  function handleRemoveDescriptor(descriptor: string): void {
    descriptorNoteVisible = true;
    onRemoveDescriptor(descriptor);
  }

  function commitNewDescriptor(): void {
    const value = newDescriptor.trim();
    newDescriptor = '';
    descriptorAdding = false;
    if (value) onAddDescriptor(value);
  }
</script>

<div class="details-panel">
  {#if liveCharacter}
    <!-- Title (editable) -->
    <div class="details-title">Name</div>
    {#if titleEditing}
      <input
        class="title-input"
        class:title-error={titleError}
        bind:this={titleInputEl}
        bind:value={titleValue}
        on:keydown={(e) => {
          if (e.key === 'Enter') commitTitle();
          else if (e.key === 'Escape') cancelTitle();
        }}
        on:blur={commitTitle}
      />
    {:else}
      <button class="title-btn" type="button" on:click={startTitleEdit} title="Rename character">
        {liveCharacter.name}
      </button>
    {/if}

    <!-- Stats: one column, three rows -->
    <div class="stats-block">
      <div class="stat-row">
        <span class="meta-label">Lines this chapter</span>
        <span class="stat-value" class:muted={statMuted(chapterLineCount)}>{statText(chapterLineCount)}</span>
      </div>
      <div class="stat-row">
        <span class="meta-label">Lines in book</span>
        <span class="stat-value" class:muted={statMuted(liveCharacter.stats?.totalLines)}>{statText(liveCharacter.stats?.totalLines)}</span>
      </div>
      <div class="stat-row">
        <span class="meta-label">Chapters</span>
        <span class="stat-value" class:muted={statMuted(liveCharacter.stats?.chapterCount)}>{statText(liveCharacter.stats?.chapterCount)}</span>
      </div>
    </div>

    <!-- Gender: unchanged 3-pill picker -->
    <div class="details-title">Gender</div>
    <div class="gender-row">
      {#each genderOptions as option}
        <button
          class="gender-btn"
          class:active={liveCharacter.gender === option}
          class:male={option === 'Male'}
          class:female={option === 'Female'}
          class:unknown={option === 'Unknown'}
          type="button"
          on:click={() => onSetGender(option)}
        >
          {option}
        </button>
      {/each}
    </div>

    <!-- Aliases: PillList (the title is NOT a pill; no primary/Star) -->
    <div class="details-title">Aliases</div>
    <PillList
      available={[]}
      selected={liveCharacter.aliases ?? []}
      allowDirectEntry={true}
      placeholder="Add alias"
      addButtonLabel="Add alias"
      getColorForItem={aliasColor}
      on:add={(e) => onAddAlias(e.detail)}
      on:remove={(e) => onRemoveAlias(e.detail)}
    />

    <!-- Descriptors: bulleted list (phrases, not pills) -->
    <div class="details-title">Descriptors</div>
    <div class="descriptor-block">
      {#if (liveCharacter.descriptors ?? []).length}
        <ul class="descriptor-list">
          {#each liveCharacter.descriptors as descriptor (descriptor)}
            <li class="descriptor-item">
              <button
                class="descriptor-bullet"
                type="button"
                title="Remove descriptor"
                aria-label={`Remove ${descriptor}`}
                on:click={() => handleRemoveDescriptor(descriptor)}
              >
                <X size={12} />
              </button>
              <span class="descriptor-text">{descriptor}</span>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="empty-copy">No descriptors yet.</p>
      {/if}

      {#if descriptorAdding}
        <div class="descriptor-add-row">
          <input
            class="descriptor-input"
            type="text"
            placeholder="Add descriptor"
            bind:value={newDescriptor}
            on:keydown={(e) => {
              if (e.key === 'Enter') commitNewDescriptor();
              else if (e.key === 'Escape') {
                newDescriptor = '';
                descriptorAdding = false;
              }
            }}
          />
        </div>
      {:else}
        <button
          class="descriptor-plus"
          type="button"
          title="Add descriptor"
          aria-label="Add descriptor"
          on:click={() => {
            descriptorAdding = true;
          }}
        >
          <Plus size={14} />
        </button>
      {/if}

      {#if descriptorNoteVisible}
        <p class="descriptor-note">Removed descriptors may reappear after the next chapter review.</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .details-panel {
    border: 1px solid var(--app-border-subtle);
    background: var(--app-surface-subtle);
    border-radius: 6px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .details-title {
    font-weight: 600;
    font-size: 13px;
    color: var(--app-text-muted);
    margin: 2px 0 -4px;
  }

  /* Title */
  .title-btn {
    border: none;
    background: transparent;
    color: var(--app-text);
    font-size: 15px;
    font-weight: 600;
    text-align: left;
    cursor: pointer;
    padding: 2px 4px;
    margin: -2px -4px;
    border-radius: 6px;
  }

  .title-btn:hover {
    background: var(--app-surface-hover);
  }

  .title-input {
    width: 100%;
    box-sizing: border-box;
    padding: 4px 6px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    font-size: 15px;
    font-weight: 600;
    background: var(--app-surface-raised);
    color: var(--app-text);
  }

  .title-input.title-error {
    border-color: var(--app-danger, #ef4444);
  }

  /* Stats (meta-label idiom from SpeakerCard) */
  .stats-block {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 10px;
    border: 1px solid var(--app-border-subtle);
    border-radius: 6px;
    background: var(--app-surface-raised);
  }

  .stat-row {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .meta-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--app-text-muted);
  }

  .stat-value {
    font-size: 20px;
    font-weight: 700;
    color: #111827;
    line-height: 1.1;
  }

  .stat-value.muted {
    color: var(--app-text-subtle);
    font-weight: 500;
  }

  /* Gender */
  .gender-row {
    display: flex;
    gap: 8px;
  }

  .gender-btn {
    border: 1px solid var(--app-border);
    background: var(--app-surface-raised);
    color: var(--app-text-muted);
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 120ms ease, background-color 120ms ease, color 120ms ease;
  }

  .gender-btn.active {
    color: var(--app-text);
    border-color: transparent;
  }

  .gender-btn.male.active {
    background: rgba(168, 218, 220, 0.24);
  }

  .gender-btn.female.active {
    background: rgba(255, 193, 204, 0.24);
  }

  .gender-btn.unknown.active {
    background: var(--app-primary-soft);
  }

  /* Descriptors */
  .descriptor-block {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .descriptor-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .descriptor-item {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .descriptor-bullet {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--app-text-muted);
    cursor: pointer;
    flex-shrink: 0;
  }

  .descriptor-bullet:hover {
    background: var(--app-surface-hover);
    color: var(--app-text);
  }

  .descriptor-text {
    flex: 1;
    font-size: 13px;
    color: var(--app-text);
  }

  .descriptor-add-row {
    display: flex;
  }

  .descriptor-input {
    flex: 1;
    padding: 4px 8px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    font-size: 13px;
    background: var(--app-surface-raised);
    color: var(--app-text);
  }

  .descriptor-input:focus {
    border-color: var(--app-primary);
  }

  .descriptor-plus {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 1px dashed var(--app-text-subtle);
    border-radius: 50%;
    background: transparent;
    color: var(--app-text-muted);
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
    transition: opacity 120ms ease;
  }

  .descriptor-block:hover .descriptor-plus,
  .descriptor-plus:focus {
    opacity: 1;
    pointer-events: auto;
  }

  .descriptor-note {
    margin: 0;
    font-size: 11px;
    color: var(--app-text-subtle);
  }

  .empty-copy {
    margin: 0;
    font-size: 12px;
    color: var(--app-text-subtle);
  }
</style>
