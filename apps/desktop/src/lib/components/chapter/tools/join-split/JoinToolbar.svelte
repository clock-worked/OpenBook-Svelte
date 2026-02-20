<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import ButtonToolbar from '$lib/components/common/ButtonToolbar.svelte';
  import JoinLeftIcon from './JoinLeftIcon.svelte';
  import JoinBothIcon from './JoinBothIcon.svelte';
  import JoinRightIcon from './JoinRightIcon.svelte';

  export let lineId: number;
  export let x: number;
  export let y: number;
  export let visible: boolean = false;
  export let isFirstLine: boolean = false;
  export let isLastLine: boolean = false;

  const dispatch = createEventDispatcher<{
    action: { action: string; lineId: number };
    close: {};
  }>();

  $: buttons = [
    {
      icon: JoinLeftIcon,
      action: 'join-left',
      title: 'Join with previous line',
      disabled: isFirstLine
    },
    {
      icon: JoinBothIcon,
      action: 'join-both',
      title: 'Join with both adjacent lines',
      disabled: isFirstLine || isLastLine
    },
    {
      icon: JoinRightIcon,
      action: 'join-right',
      title: 'Join with next line',
      disabled: isLastLine
    }
  ];

  function handleAction(e: CustomEvent<{ action: string }>) {
    dispatch('action', { action: e.detail.action, lineId });
  }

  function handleClose() {
    dispatch('close');
  }
</script>

<ButtonToolbar
  {buttons}
  {x}
  {y}
  {visible}
  on:action={handleAction}
  on:close={handleClose}
/>


