<script lang="ts">
  import type { Character, Gender } from '$lib/types';
  import type { ClosedWorldReviewItem } from '$lib/services/closedWorldCharacterWorkflow';

  export let items: ClosedWorldReviewItem[] = [];
  export let characters: Character[] = [];
  export let onAdd: (item: ClosedWorldReviewItem, name: string, gender: Gender) => Promise<void>;
  export let onAlias: (item: ClosedWorldReviewItem, targetCharacterId: string) => Promise<void>;
  export let onNonSpeaker: (item: ClosedWorldReviewItem) => Promise<void>;
  export let onDescriptor: (item: ClosedWorldReviewItem, lineTargets: Record<number, string>) => Promise<void>;
  export let onJump: (lineId: number) => void;

  let names: Record<number, string> = {};
  let genders: Record<number, Gender> = {};
  let targets: Record<number, string> = {};
  // Keyed by line id: a descriptor is resolved line-by-line.
  let lineTargets: Record<number, string> = {};
  // Keyed by candidate (not lineId): partially resolving a descriptor changes the item's first lineId.
  let descriptorOpen: Record<string, boolean> = {};
  let jumpPositions: Record<string, number> = {};
  let errors: Record<string, string> = {};
  let busyKey: string | null = null;

  $: choices = characters
    .filter((character) => character.id !== 'narrator')
    .sort((left, right) => left.name.localeCompare(right.name));

  function keyFor(item: ClosedWorldReviewItem): string {
    return item.candidateName.trim().toLowerCase();
  }

  function nameFor(item: ClosedWorldReviewItem): string {
    return names[item.lineId] ?? item.candidateName;
  }

  async function run(item: ClosedWorldReviewItem, action: () => Promise<void>): Promise<void> {
    const key = keyFor(item);
    busyKey = key;
    errors = { ...errors, [key]: '' };
    try {
      await action();
    } catch (error) {
      errors = { ...errors, [key]: error instanceof Error ? error.message : String(error) };
    } finally {
      busyKey = null;
    }
  }

  function add(item: ClosedWorldReviewItem): Promise<void> {
    return run(item, () => onAdd(item, nameFor(item), genders[item.lineId] ?? 'Unknown'));
  }

  function alias(item: ClosedWorldReviewItem): Promise<void> {
    const target = targets[item.lineId];
    if (!target) return Promise.resolve();
    return run(item, () => onAlias(item, target));
  }

  function markAsNonSpeaker(item: ClosedWorldReviewItem): Promise<void> {
    return run(item, () => onNonSpeaker(item));
  }

  function toggleDescriptor(item: ClosedWorldReviewItem): void {
    const key = keyFor(item);
    descriptorOpen = { ...descriptorOpen, [key]: !descriptorOpen[key] };
  }

  function assignedTargets(item: ClosedWorldReviewItem, current: Record<number, string>): Record<number, string> {
    return Object.fromEntries(item.lineIds.filter((id) => current[id]).map((id) => [id, current[id]]));
  }

  function setAllLineTargets(item: ClosedWorldReviewItem, characterId: string): void {
    if (!characterId) return;
    const next = { ...lineTargets };
    for (const id of item.lineIds) next[id] = characterId;
    lineTargets = next;
  }

  function descriptor(item: ClosedWorldReviewItem): Promise<void> {
    const assigned = assignedTargets(item, lineTargets);
    if (!Object.keys(assigned).length) return Promise.resolve();
    return run(item, async () => {
      await onDescriptor(item, assigned);
      const next = { ...lineTargets };
      for (const id of Object.keys(assigned)) delete next[Number(id)];
      lineTargets = next;
    });
  }

  function jumpNext(item: ClosedWorldReviewItem): void {
    const key = keyFor(item);
    const next = ((jumpPositions[key] ?? -1) + 1) % item.lineIds.length;
    jumpPositions = { ...jumpPositions, [key]: next };
    onJump(item.lineIds[next]);
  }

  function jumpLabel(item: ClosedWorldReviewItem, positions: Record<string, number>): string {
    if (item.lineIds.length === 1) return 'Jump to line';
    const next = ((positions[keyFor(item)] ?? -1) + 1) % item.lineIds.length;
    return `Jump to line ${next + 1}/${item.lineIds.length}`;
  }
</script>

{#if items.length}
  <section class="review" aria-label="Unresolved character review">
    <header>
      <strong>Character continuity review</strong>
      <span>{items.length} unresolved candidate{items.length === 1 ? '' : 's'}</span>
    </header>
    {#each items as item (item.lineId)}
      {@const key = keyFor(item)}
      {@const busy = busyKey === key}
      <article>
        <div class="candidate">
          <div class="candidate-head">
            <strong>{item.candidateName}</strong>
            <button class="jump" title={`Scroll to ${item.candidateName} in the chapter`} on:click={() => jumpNext(item)}>
              {jumpLabel(item, jumpPositions)}
            </button>
          </div>
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
          <button disabled={busy} on:click={() => add(item)}>Add as New</button>
          <span class="or">or</span>
          <select
            aria-label={`Merge ${item.candidateName} as alias`}
            value={targets[item.lineId] ?? ''}
            on:change={(event) => targets[item.lineId] = event.currentTarget.value}
          >
            <option value="">Choose existing…</option>
            {#each choices as character}
              <option value={character.id}>{character.name}</option>
            {/each}
          </select>
          <button disabled={!targets[item.lineId] || busy} on:click={() => alias(item)}>Merge as Alias</button>
          <button
            class:active={descriptorOpen[key]}
            aria-expanded={!!descriptorOpen[key]}
            disabled={busy}
            on:click={() => toggleDescriptor(item)}
          >Merge as Descriptor</button>
          <button disabled={busy} on:click={() => markAsNonSpeaker(item)}>Not a Speaker</button>
        </div>
        {#if descriptorOpen[key]}
          {@const assignedCount = Object.keys(assignedTargets(item, lineTargets)).length}
          <div class="descriptor-panel" aria-label={`Assign descriptor ${item.candidateName} per line`}>
            <div class="descriptor-head">
              <span class="hint">Pick who “{item.candidateName}” refers to on each line. Blank lines stay in review.</span>
              {#if item.lines.length > 1}
                <select
                  aria-label={`Set all lines of ${item.candidateName}`}
                  value=""
                  on:change={(event) => { setAllLineTargets(item, event.currentTarget.value); event.currentTarget.value = ''; }}
                >
                  <option value="">Set all to…</option>
                  {#each choices as character}
                    <option value={character.id}>{character.name}</option>
                  {/each}
                </select>
              {/if}
            </div>
            <ul class="descriptor-lines">
              {#each item.lines as line (line.id)}
                <li>
                  <button class="jump" title={`Jump to line ${line.id}`} on:click={() => onJump(line.id)}>Line {line.id}</button>
                  <span class="line-text" title={line.text}>{line.text}</span>
                  <select
                    aria-label={`Character for ${item.candidateName} on line ${line.id}`}
                    value={lineTargets[line.id] ?? ''}
                    on:change={(event) => lineTargets = { ...lineTargets, [line.id]: event.currentTarget.value }}
                  >
                    <option value="">Leave unresolved</option>
                    {#each choices as character}
                      <option value={character.id}>{character.name}</option>
                    {/each}
                  </select>
                </li>
              {/each}
            </ul>
            <div class="descriptor-foot">
              <button disabled={!assignedCount || busy} on:click={() => descriptor(item)}>
                Apply Descriptor ({assignedCount}/{item.lines.length})
              </button>
              <button disabled={busy} on:click={() => toggleDescriptor(item)}>Cancel</button>
            </div>
          </div>
        {/if}
        {#if errors[key]}
          <p class="error" role="alert">{errors[key]}</p>
        {/if}
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
  .candidate-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .candidate span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
  input, select, button { border: 1px solid var(--app-border); border-radius: 5px; background: var(--app-surface-raised); color: var(--app-text); padding: 5px 7px; }
  input { min-width: 130px; }
  button { cursor: pointer; }
  button:disabled { cursor: default; opacity: .5; }
  button.active { border-color: var(--app-primary, #2563eb); }
  button.jump { padding: 2px 7px; font-size: 12px; white-space: nowrap; flex-shrink: 0; }
  .descriptor-panel { margin-top: 8px; border: 1px solid var(--app-border-subtle); border-radius: 6px; background: var(--app-surface-raised); padding: 7px; }
  .descriptor-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .hint { color: var(--app-text-muted); font-size: 12px; }
  .descriptor-lines { list-style: none; margin: 0; padding: 0; max-height: 260px; overflow-y: auto; }
  .descriptor-lines li { display: grid; grid-template-columns: auto minmax(0, 1fr) minmax(120px, 40%); align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--app-border-subtle); }
  .descriptor-lines li:last-child { border-bottom: 0; }
  .line-text { font-size: 12px; color: var(--app-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .descriptor-foot { display: flex; justify-content: flex-end; gap: 6px; margin-top: 7px; }
  .error { margin: 6px 0 0; color: var(--app-danger, #b91c1c); font-size: 12px; }
</style>