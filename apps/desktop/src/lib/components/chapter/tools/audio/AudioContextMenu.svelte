<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import ContextMenu from '$lib/components/common/ContextMenu.svelte';
  import { Volume2 } from 'lucide-svelte';

  export let x: number;
  export let y: number;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{
    action: { action: string };
    close: {};
  }>();

  const menuItems = [
    {
      label: 'Regenerate Audio',
      icon: Volume2,
      action: 'regenerate',
      color: '#1967d2'
    },
    {
      label: 'Delete Audio',
      icon: null,
      action: 'delete',
      color: '#d93025'
    }
  ];

  function handleAction(e: CustomEvent<{ action: string }>) {
    dispatch('action', e.detail);
  }

  function handleClose() {
    dispatch('close');
  }
</script>

<ContextMenu
  items={menuItems}
  {x}
  {y}
  {visible}
  on:action={handleAction}
  on:close={handleClose}
/>


