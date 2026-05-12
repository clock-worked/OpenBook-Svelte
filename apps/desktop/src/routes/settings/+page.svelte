<script lang="ts">
    import { appTheme, bookRootPathOverride, parserHints, type AppTheme } from '$lib/stores/settings';
    import { setBackendBookRoot } from '$lib/services/fs';
    import Toolbar from '$lib/components/chapter/Toolbar.svelte';

    let bookRootPathInput = $bookRootPathOverride || '';

    function handleThemeChange(theme: AppTheme) {
        appTheme.set(theme);
    }

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

<Toolbar />

<div class="settings-page">
<h1>Settings</h1>

<section class="settings-section">
    <div>
        <h2>Appearance</h2>
        <p>Choose the app theme used across OpenBook.</p>
    </div>
    <div class="theme-toggle" role="group" aria-label="Theme">
        <button
            class:active={$appTheme === 'light'}
            aria-pressed={$appTheme === 'light'}
            on:click={() => handleThemeChange('light')}
        >
            Light
        </button>
        <button
            class:active={$appTheme === 'dark'}
            aria-pressed={$appTheme === 'dark'}
            on:click={() => handleThemeChange('dark')}
        >
            Dark
        </button>
    </div>
</section>

<section class="settings-section">
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

<section class="settings-section">
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
</div>

<style>
    .settings-page {
        min-height: calc(100vh - 57px);
        padding: 32px;
        background: var(--app-bg);
        color: var(--app-text);
    }

    .settings-section {
        margin: 2rem 0;
        max-width: 880px;
        padding: 20px;
        border: 1px solid var(--app-border);
        border-radius: var(--app-radius-md);
        background: var(--app-surface);
        box-shadow: var(--app-shadow-sm);
    }

    h1,
    h2 {
        color: var(--app-text);
    }

    p {
        color: var(--app-text-muted);
        line-height: 1.5;
    }

    input {
        min-width: min(560px, 100%);
        padding: 9px 11px;
        border: 1px solid var(--app-border);
        border-radius: var(--app-radius-sm);
        background: var(--app-surface-raised);
        color: var(--app-text);
    }

    button {
        border: 1px solid var(--app-border);
        border-radius: var(--app-radius-sm);
        background: var(--app-surface-raised);
        color: var(--app-text);
        padding: 8px 12px;
        cursor: pointer;
    }

    button:hover {
        background: var(--app-surface-hover);
    }

    .theme-toggle {
        display: inline-flex;
        padding: 3px;
        gap: 2px;
        border: 1px solid var(--app-border);
        border-radius: var(--app-radius-md);
        background: var(--app-surface-subtle);
    }

    .theme-toggle button {
        min-width: 72px;
        border: none;
        background: transparent;
        color: var(--app-text-muted);
    }

    .theme-toggle button.active {
        background: var(--app-primary);
        color: var(--app-text-inverse);
        box-shadow: var(--app-shadow-sm);
    }
</style>
