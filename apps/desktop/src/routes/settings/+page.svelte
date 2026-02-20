<script lang="ts">
    import { bookRootPathOverride, parserHints } from '$lib/stores/settings';
    import { setBackendBookRoot } from '$lib/services/fs';

    let bookRootPathInput = $bookRootPathOverride || '';

    async function handleBookRootPathSave() {
        const trimmed = bookRootPathInput.trim();
        const value = trimmed.length ? trimmed : null;
        bookRootPathOverride.set(value);
        
        if (value) {
            try {
                const didSync = await setBackendBookRoot(value);
                if (didSync) {
                    alert('Book root path saved and synced with backend.');
                } else {
                    alert('Book root path saved locally, but backend sync failed.');
                }
            } catch (e) {
                console.error(e);
                alert('Book root path saved locally. Backend is unreachable.');
            }
        } else {
            alert('Book root path cleared.');
        }
    }

    function handleBookRootPathClear() {
        bookRootPathInput = '';
        bookRootPathOverride.set(null);
    }

    function clamp01(value: number): number {
        if (!Number.isFinite(value)) return 0;
        if (value < 0) return 0;
        if (value > 1) return 1;
        return value;
    }

    function handleUnknownThresholdInput(event: Event) {
        const input = event.target as HTMLInputElement;
        const next = clamp01(Number(input.value));
        parserHints.update((h) => ({
            ...h,
            attribution: {
                ...(h.attribution || {}),
                unknownThreshold: next,
            },
        }));
    }
</script>

<h1>Settings</h1>

<section>
    <h2>Book Root Path (Backend)</h2>
    <p>
        The browser file picker does not expose absolute paths. If the backend needs a full path,
        enter it here (for example: C:\\Users\\Chad\\Documents\\Code\\Python\\Useful-Scripts\\Data\\Resources\\Primal-Hunter\\Book-14).
    </p>
    <input type="text" bind:value={bookRootPathInput} placeholder="Absolute book root path" />
    <button on:click={handleBookRootPathSave}>Save Path</button>
    {#if $bookRootPathOverride}
        <p>Book root path override is set.</p>
        <button on:click={handleBookRootPathClear}>Clear Path</button>
    {/if}
</section>

<section>
    <h2>Dialogue Attribution</h2>
    <p>Confidence threshold for auto-speaker assignment. Quotes below this value are labeled Unknown for review.</p>
    <input
        type="number"
        min="0"
        max="1"
        step="0.01"
        value={$parserHints.attribution?.unknownThreshold ?? 0.62}
        on:input={handleUnknownThresholdInput}
    />
</section>

<style>
    section {
        margin: 2rem 0;
    }
</style>
