<script lang="ts">
  export let rawText: string | null = null;
</script>

{#if rawText}
  {#each rawText.trim().split(/(?:\r?\n\s*){2,}/) as para}
    <p class="raw-paragraph">{para}</p>
  {/each}
{/if}

<style>
  .raw-paragraph { background:rgba(0,0,0,0.04); padding:8px 10px; border-radius:6px; white-space:pre-wrap; }
</style>


