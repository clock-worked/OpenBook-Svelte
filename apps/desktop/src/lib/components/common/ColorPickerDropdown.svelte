<script lang="ts">
  import Dropdown from '$lib/components/common/Dropdown.svelte';
  import ColorPicker from '$lib/components/common/ColorPicker.svelte';

  export let value: string | null = null; // opaque hex or null
  export let alpha: number = 1.0;
  export let title: string = 'Pick color';
  export let triggerStyle: string = '';
  export let minWidth: number = 180;

  // Re-emit select event with Svelte dispatch from parent
  import { createEventDispatcher } from 'svelte';
  const dispatch = createEventDispatcher<{ select: { color: string | null } }>();
</script>

<Dropdown showCaret={false} align="right" {minWidth} {title}>
  <span>
    <span class="color-trigger" style={`${triggerStyle};background:${value ?? '#ccc'}`}></span>
  </span>
  <div slot="panel" let:close>
    <ColorPicker {value} {alpha} on:select={(e) => { dispatch('select', { color: e.detail.color }); close(); }} />
  </div>
</Dropdown>

<style>
  .color-trigger {
    display: inline-block;
    width: 22px;
    height: 22px;
    border-radius: 6px;
    border: 2px solid #e0e0e0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    transition: all 0.2s ease;
  }

  .color-trigger:hover {
    border-color: #4285f4;
    box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    transform: scale(1.05);
  }
</style>

