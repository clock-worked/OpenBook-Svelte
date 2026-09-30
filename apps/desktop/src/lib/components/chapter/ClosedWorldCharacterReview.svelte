<script lang="ts">
  import type { Character, Gender } from '$lib/types';
  import type { ClosedWorldReviewItem } from '$lib/services/closedWorldCharacterWorkflow';

  export let items: ClosedWorldReviewItem[] = [];
  export let characters: Character[] = [];
  export let onAdd: (item: ClosedWorldReviewItem, name: string, gender: Gender) => Promise<void>;
  export let onAlias: (item: ClosedWorldReviewItem, targetCharacterId: string) => Promise<void>;
  export let onNonSpeaker: (item: ClosedWorldReviewItem) => Promise<void>;

  let names: Record<number, string> = {};
  let genders: Record<number, Gender> = {};
  let targets: Record<number, string> = {};
  let busyLineId: number | null = null;

  function nameFor(item: ClosedWorldReviewItem): string {
    return names[item.lineId] ?? item.candidateName;
  }

  async function add(item: ClosedWorldReviewItem): Promise<void> {
    busyLineId = item.lineId;
    try {
      await onAdd(item, nameFor(item), genders[item.lineId] ?? 'Unknown');
    } finally {
      busyLineId = null;
    }
  }

  async function alias(item: ClosedWorldReviewItem): Promise<void> {
    const target = targets[item.lineId];
    if (!target) return;
    busyLineId = item.lineId;
    try {
      await onAlias(item, target);
    } finally {
      busyLineId = null;
    }
  }

  async function markAsNonSpeaker(item: ClosedWorldReviewItem): Promise<void> {
    busyLineId = item.lineId;
    try {
      await onNonSpeaker(item);
    } finally {
      busyLineId = null;
    }
  }
</script>

{#if items.length}
  <section class="review" aria-label="Unresolved character review">
    <header>
      <strong>Character continuity review</strong>
      <span>{items.length} unresolved candidate{items.length === 1 ? '' : 's'}</span>
    </header>
    {#each items as item (item.lineId)}
      <article>
        <div class="candidate">
          <strong>{item.candidateName}</strong>
          <span>{item.lineIds.length === 1 ? `Line ${item.lineId}` : `${item.lineIds.length} lines`}: {item.text}</span>
        </div>
        <div class="actions">
          <input
            aria-label={`Display name for ${item.candidateName}`}
            value={nameFor(item)}
            on:input={(event) => names[item.lineId] = event.currentTarget.value}
          />
          <select
            aria-label={`Gender for ${item.candidateName}`}
            value={genders[item.lineId] ?? 'Unknown'}
            on:change={(event) => genders[item.lineId] = event.currentTarget.value as Gender}
          >
            <option>Unknown</option><option>Female</option><option>Male</option>
          </select>
          <button disabled={busyLineId === item.lineId} on:click={() => add(item)}>Add as New</button>
          <span class="or">or</span>
          <select
            aria-label={`Merge ${item.candidateName} as alias`}
            value={targets[item.lineId] ?? ''}
            on:change={(event) => targets[item.lineId] = event.currentTarget.value}
          >
            <option value="">Choose existing…</option>
            {#each characters.filter((character) => character.id !== 'narrator').sort((left, right) => left.name.localeCompare(right.name)) as character}
              <option value={character.id}>{character.name}</option>
            {/each}
          </select>
          <button disabled={!targets[item.lineId] || busyLineId === item.lineId} on:click={() => alias(item)}>Merge as Alias</button>
          <button disabled={busyLineId === item.lineId} on:click={() => markAsNonSpeaker(item)}>Not a Speaker</button>
        </div>
      </article>
    {/each}
  </section>
{/if}

<style>
  .review { border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-surface-subtle); overflow: hidden; }
  header { display: flex; justify-content: space-between; padding: 8px 10px; border-bottom: 1px solid var(--app-border); }
  header span, .candidate span, .or { color: var(--app-text-muted); font-size: 12px; }
  article { padding: 9px 10px; border-bottom: 1px solid var(--app-border-subtle); }
  article:last-child { border-bottom: 0; }
  .candidate { display: flex; flex-direction: column; gap: 2px; margin-bottom: 7px; }
  .candidate span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
  input, select, button { border: 1px solid var(--app-border); border-radius: 5px; background: var(--app-surface-raised); color: var(--app-text); padding: 5px 7px; }
  input { min-width: 130px; }
  button { cursor: pointer; }
  button:disabled { cursor: default; opacity: .5; }
</style>