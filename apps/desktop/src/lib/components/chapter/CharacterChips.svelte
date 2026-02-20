<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { colorForCharacter, hexToRgba, characters } from '$lib/stores/characters';
  import type { ParagraphRun } from '$lib/types';
  import type { ToolMode } from '$lib/stores/selection';
  import { currentScript } from '$lib/stores/bookState';
  import { get } from 'svelte/store';

  export let runs: ParagraphRun[] = [];
  export let setHovered: (s: string | null) => void;
  export let toolMode: ToolMode = 'review';

  const DISPLAY_ALPHA = 1.0; // Must match CharacterPanel

  const dispatch = createEventDispatcher();
  
  // Subscribe to characters to force re-render when colors change
  $: charsVersion = $characters;

  function formatCharacterName(name: string | null | undefined): string {
    if (!name) return 'Unknown';
    if (name.toLowerCase() === 'narrator') return 'Narrator';
    // Capitalize first letter of each word
    return name.split(/\s+/).map(word => 
      word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
    ).join(' ');
  }

  function onChipClick(e: MouseEvent, characterName: string) {
    if (toolMode !== 'review') return; // Only works in review mode
    const ids = Array.from(
      new Set(
        runs
          .filter((r) => r.lineId != null && r.characterId === characterName && characterName?.toLowerCase() !== 'narrator')
          .map((r) => r.lineId as number)
      )
    );
    if (ids.length) {
      dispatch('paragraphMenu', { ev: e, lineIds: ids });
    }
  }

  type ConflictPair = { left: string; right: string };

  function getConflictPairsForParagraph(scrVal: any): ConflictPair[] {
    const scr = scrVal;
    if (!scr) return [];
    const ids = Array.from(new Set(runs.map(r => r.lineId).filter((v): v is number => typeof v === 'number')));
    const seen = new Set<string>();
    const pairs: ConflictPair[] = [];
    for (const id of ids) {
      const line = scr.lines.find(l => l.id === id);
      if (!line || !line.isConflict || !Array.isArray(line.candidates) || line.candidates.length === 0) continue;
      const left = (line.candidates[0]?.name ?? line.chosenSpeaker ?? 'Unknown').toString();
      const right = (line.candidates[1]?.name ?? null);
      if (!right) continue;
      const key = left + '|' + right;
      if (seen.has(key)) continue;
      seen.add(key);
      pairs.push({ left, right });
    }
    return pairs;
  }

  function onConflictChipClick(e: MouseEvent, pair: ConflictPair) {
    if (toolMode !== 'review') return; // Only works in review mode
    const target = e.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const isLeft = clickX <= rect.width / 2;
    const chosen = isLeft ? pair.left : pair.right;
    const scr = get(currentScript);
    if (!scr) return;
    // Assign to all conflicted lines in this paragraph
    const ids = Array.from(new Set(
      runs
        .map(r => r.lineId)
        .filter((v): v is number => typeof v === 'number')
        .map(id => scr.lines.find(l => l.id === id))
        .filter((l): l is NonNullable<typeof l> => !!l)
        .filter(l => l.isConflict)
        .map(l => l.id)
    ));
    if (ids.length) {
      dispatch('paragraphAssign', { characterName: chosen, lineIds: ids });
    }
  }
</script>

<div class="character-col">
  {#key charsVersion}
    {#each getConflictPairsForParagraph($currentScript) as pair}
      <span
        class="character-chip conflict-chip"
        class:chip-disabled={toolMode !== 'review' && toolMode !== 'audio'}
                style={`--bg:linear-gradient(to right, ${hexToRgba(colorForCharacter(pair.left), 1)}, ${hexToRgba(colorForCharacter(pair.right), 1)});`}
        role="button"
        tabindex={toolMode === 'review' || toolMode === 'audio' ? 0 : -1}
        aria-label={`Conflict between ${pair.left} and ${pair.right}`}
        title={toolMode === 'review' ? `${pair.left} | ${pair.right}` : toolMode === 'audio' ? `${pair.left} | ${pair.right}` : 'Switch to Review mode to assign characters'}
        on:mouseenter={() => setHovered(null)}
        on:mouseleave={() => setHovered(null)}
        on:click={(e) => onConflictChipClick(e, pair)}
        on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onConflictChipClick(e as any, pair); } }}
      >{formatCharacterName(pair.left)} | {formatCharacterName(pair.right)}</span>
    {/each}

    {#each Array.from(new Set(runs
        .filter(r => r.characterId)
        .filter(r => {
          // hide chips for characters that only appear on conflicted lines in this paragraph
          const scr = $currentScript;
          if (!scr || r.lineId == null) return true;
          const line = scr.lines.find(l => l.id === r.lineId);
          if (!line) return true;
          return !line.isConflict;
        })
        .map(r => r.characterId))) as charName}
      <span
        class="character-chip"
        class:narrator-chip={charName?.toLowerCase() === 'narrator'}
        class:chip-disabled={toolMode !== 'review' && toolMode !== 'audio'}
        style={charName?.toLowerCase() === 'narrator' ? '' : `--bg:${hexToRgba(colorForCharacter(charName as string), 1)};`}
        role="button"
        tabindex={toolMode === 'review' || toolMode === 'audio' ? 0 : -1}
        aria-label={`Character ${charName}`}
        title={toolMode === 'review' || toolMode === 'audio' ? undefined : 'Switch to Review mode to assign characters'}
        on:mouseenter={() => { if (charName?.toLowerCase() !== 'narrator') setHovered(charName as string); }}
        on:mouseleave={() => setHovered(null)}
        on:focus={() => { if (charName?.toLowerCase() !== 'narrator') setHovered(charName as string); }}
        on:blur={() => setHovered(null)}
        on:click={(e) => onChipClick(e, charName as string)}
        on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onChipClick(e as any, charName as string); } }}
      >{formatCharacterName(charName)}</span>
    {/each}
  {/key}
</div>

<style>
  .character-col { width:56px; display:flex; flex-direction:column; align-items:flex-end; gap:4px; padding-top:2px; }
  .character-chip { cursor:default; user-select:none; font-size:12px; padding:2px 6px; border-radius:10px; background: var(--bg, transparent); color:#222; white-space:nowrap; }
  .character-chip.narrator-chip { font-style:italic; color:#555; background:#ffffff; opacity:1; }
  .character-chip.chip-disabled { opacity:0.4; cursor:not-allowed; pointer-events:none; }
</style>


