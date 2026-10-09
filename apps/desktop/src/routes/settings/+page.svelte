<script lang="ts">
    import { get } from 'svelte/store';
    import { appTheme, bookRootPathOverride, parserHints, type AppTheme } from '$lib/stores/settings';
    import { bookRoot, bookRootAbsolutePath, chapters } from '$lib/stores/bookState';
    import { forceRefreshBookCharacters } from '$lib/stores/bookCharacters';
    import { setBackendBookRoot, getRootDirInfo } from '$lib/services/fs';
    import { API_ENDPOINTS, apiFetch } from '$lib/services/apiClient';
    import { migrateBookToV3, type BookMigrationReport } from '$lib/services/bookMigration';
    import { clampConfidence } from '$lib/components/chapter/tools/shared/chapterNormalization';
    import Toolbar from '$lib/components/chapter/Toolbar.svelte';

    let bookRootPathInput = $bookRootPathOverride || '';

    let migrationRunning = false;
    let migrationProgress = '';
    let migrationReport: BookMigrationReport | null = null;

    $: openBookName = $bookRoot ? (getRootDirInfo().hasHandle ? getRootDirInfo().name : $bookRoot) : null;

    async function handleMigrateBook() {
        const root = get(bookRoot);
        if (!root || migrationRunning) return;
        const confirmed = confirm(
            'Migrate this book\'s characters and dialogue files to the current format?\n\n' +
            'Backups of every rewritten file are written to _backups/ first. Speakers with no matching character get a new character file.'
        );
        if (!confirmed) return;

        migrationRunning = true;
        migrationReport = null;
        migrationProgress = 'Starting…';
        try {
            migrationReport = await migrateBookToV3({
                root,
                chapters: get(chapters),
                unknownThreshold: clampConfidence($parserHints.attribution?.unknownThreshold ?? 0.62),
                apiSave: (relativePath, content) =>
                    apiFetch(API_ENDPOINTS.save, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ file_path: relativePath, content }),
                    }),
                getBackendRootAbsolutePath: () => get(bookRootAbsolutePath),
                onProgress: (message) => {
                    migrationProgress = message;
                },
            });
            await forceRefreshBookCharacters();
        } finally {
            migrationRunning = false;
            migrationProgress = '';
        }
    }

    function plural(count: number, noun: string): string {
        return `${count} ${noun}${count === 1 ? '' : 's'}`;
    }

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

<section class="settings-section">
    <h2>Data Migration</h2>
    <p>
        Convert the open book to the current data formats: the legacy <code>characters.json</code> becomes the
        <code>characters/</code> folder, and every chapter's <code>dialogue.json</code> still using the old
        name-based format (or stale character references) is rewritten with character IDs.
        Pre-images are saved under <code>_backups/</code> before anything is changed.
    </p>
    {#if openBookName}
        <p>Open book: <strong>{openBookName}</strong> ({plural($chapters.length, 'chapter')})</p>
    {:else}
        <p>Open a book first to enable migration.</p>
    {/if}
    <button on:click={handleMigrateBook} disabled={!$bookRoot || migrationRunning}>
        {migrationRunning ? 'Migrating…' : 'Migrate Characters & Dialogue'}
    </button>
    {#if migrationRunning && migrationProgress}
        <p class="migration-progress" aria-live="polite">{migrationProgress}</p>
    {/if}
    {#if migrationReport}
        <div class="migration-report" class:failed={!migrationReport.ok} aria-live="polite">
            <strong>{migrationReport.ok ? 'Migration complete' : 'Migration did not finish'}</strong>
            {#if migrationReport.abortReason}
                <p>{migrationReport.abortReason}</p>
            {/if}
            <ul>
                <li>Characters: {migrationReport.characters.message}
                    {#if migrationReport.characters.unresolvedRefs > 0}
                        ({plural(migrationReport.characters.unresolvedRefs, 'unresolvable reference')} left in place)
                    {/if}
                </li>
                <li>
                    Dialogue: {plural(migrationReport.dialogue.scanned, 'file')} scanned,
                    {migrationReport.dialogue.migrated.length} migrated, {migrationReport.dialogue.skipped} already current.
                </li>
                {#if migrationReport.dialogue.createdCharacters.length > 0}
                    <li>New characters: {migrationReport.dialogue.createdCharacters.join(', ')}</li>
                {/if}
                {#if migrationReport.dialogue.droppedCandidateNames.length > 0}
                    <li>Dropped candidate-only names (never a chosen speaker): {migrationReport.dialogue.droppedCandidateNames.join(', ')}</li>
                {/if}
                {#if migrationReport.dialogue.unresolvedNames.length > 0}
                    <li>Could not create characters for: {migrationReport.dialogue.unresolvedNames.join(', ')}</li>
                {/if}
                {#if migrationReport.dialogue.rostersRewritten > 0 || migrationReport.dialogue.staleRosterEntriesDropped > 0}
                    <li>
                        Chapter rosters: {migrationReport.dialogue.rostersRewritten} rewritten,
                        {plural(migrationReport.dialogue.staleRosterEntriesDropped, 'stale entry')} removed.
                    </li>
                {/if}
                {#if migrationReport.dialogue.failed.length > 0}
                    <li>
                        Failed:
                        <ul>
                            {#each migrationReport.dialogue.failed as failure}
                                <li>{failure.chapter}: {failure.reason}</li>
                            {/each}
                        </ul>
                    </li>
                {/if}
                {#if migrationReport.backupDir}
                    <li>Backups: <code>{migrationReport.backupDir}/</code></li>
                {/if}
            </ul>
        </div>
    {/if}
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

    button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
    }

    code {
        font-size: 0.9em;
        padding: 1px 4px;
        border-radius: var(--app-radius-sm);
        background: var(--app-surface-subtle);
    }

    .migration-progress {
        margin-top: 10px;
        font-style: italic;
    }

    .migration-report {
        margin-top: 14px;
        padding: 12px 14px;
        border: 1px solid var(--app-border);
        border-radius: var(--app-radius-sm);
        background: var(--app-surface-subtle);
        color: var(--app-text);
    }

    .migration-report.failed {
        border-color: var(--app-danger);
    }

    .migration-report ul {
        margin: 8px 0 0;
        padding-left: 20px;
    }

    .migration-report li {
        color: var(--app-text-muted);
        line-height: 1.5;
    }

    .migration-report p {
        margin: 6px 0 0;
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
