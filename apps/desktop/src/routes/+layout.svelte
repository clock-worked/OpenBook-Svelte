<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { appTheme } from '$lib/stores/settings';
  import '$lib/theme/app.css';

  let unsubscribe: (() => void) | null = null;

  onMount(() => {
    unsubscribe = appTheme.subscribe((theme) => {
      document.documentElement.dataset.theme = theme;
    });

    return () => {
      unsubscribe?.();
      unsubscribe = null;
    };
  });

  onDestroy(() => {
    unsubscribe?.();
  });
</script>

<slot />
