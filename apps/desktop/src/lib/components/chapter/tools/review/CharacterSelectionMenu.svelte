<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import CharacterMenu from '$lib/components/common/CharacterMenu.svelte';

  export let items: { value: string; label: string }[] = [];
  export let selected: string | null = null;
  export let x: number = 0;
  export let y: number = 0;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{
    select: { value: string };
    close: {};
  }>();

  function handleSelect(e: CustomEvent<{ value: string }>) {
    dispatch('select', e.detail);
  }

  function handleClose() {
    dispatch('close');
  }
</script>

<CharacterMenu
  {items}
  {selected}
  {x}
  {y}
  {visible}
  on:select={handleSelect}
  on:close={handleClose}
/>


