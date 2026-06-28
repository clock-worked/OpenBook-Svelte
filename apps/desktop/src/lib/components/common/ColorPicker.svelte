<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { defaultColors } from '$lib/theme/colors';
  import { rgbaToOpaqueHex } from '$lib/stores/characters';

  export let value: string | null = null; // opaque hex (e.g., #aabbcc) or null
  export let alpha: number = 1.0;
  export let swatches: string[] = defaultColors;

  const dispatch = createEventDispatcher<{ select: { color: string | null } }>();
  function pick(color: string | null) { dispatch('select', { color }); }
</script>

<div class="color-picker">
  {#each swatches as col}
    <button class="color-swatch" title={col} on:click={() => pick(rgbaToOpaqueHex(col, alpha))} style={`background:${rgbaToOpaqueHex(col, alpha)};`}></button>
  {/each}
  <div class="color-controls">
    <input type="color" value={value ?? '#e6e6e6'} on:input={(e: any) => pick(e.currentTarget.value)} class="color-input" />
  </div>
</div>

<style>
  .color-picker {
    background: var(--app-surface);
    border: none;
    border-radius: 8px;
    padding: 12px;
    position: relative;
    z-index: 30;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    max-width: 200px;
  }

  .color-swatch {
    width: 24px;
    height: 24px;
    border-radius: 6px;
    border: 2px solid var(--app-border);
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  }

  .color-swatch:hover {
    transform: scale(1.15);
    border-color: var(--app-primary);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
  }

  .color-swatch:active {
    transform: scale(1.05);
  }

  .color-controls {
    display: flex;
    gap: 8px;
    align-items: center;
    width: 100%;
    margin-top: 6px;
    padding-top: 8px;
    border-top: 1px solid var(--app-border-subtle);
  }

  .color-input {
    width: 36px;
    height: 28px;
    border: 2px solid var(--app-border);
    border-radius: 6px;
    background: transparent;
    padding: 2px;
    cursor: pointer;
    transition: border-color 0.2s ease;
  }

  .color-input:hover {
    border-color: var(--app-primary);
  }
</style>

