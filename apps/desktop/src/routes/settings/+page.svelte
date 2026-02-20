<script lang="ts">
    import { googleTtsCredentials, elevenLabsApiKey } from '$lib/stores/credentials';
    import { bookRootPathOverride, parserHints } from '$lib/stores/settings';
    import { validateCredentials as validateGoogle } from '$lib/services/google-tts';
    import { fetchElevenLabsVoices } from '$lib/services/elevenlabs';

    let googleCredsFile: FileList;
    let elApiKeyInput = $elevenLabsApiKey || '';
    let bookRootPathInput = $bookRootPathOverride || '';

    async function handleGoogleCredsUpload() {
        const file = googleCredsFile[0];
        if (file) {
            const text = await file.text();
            try {
                const json = JSON.parse(text);
                const isValid = await validateGoogle(json);
                if (isValid) {
                    googleTtsCredentials.set(json);
                    alert('Google Credentials are valid and saved.');
                } else {
                    alert('Invalid Google Credentials.');
                }
            } catch (e) {
                alert('Failed to parse credentials file.');
            }
        }
    }

    async function handleElApiKeySave() {
        if (elApiKeyInput) {
            try {
                await fetchElevenLabsVoices(elApiKeyInput);
                elevenLabsApiKey.set(elApiKeyInput);
                alert('ElevenLabs API key is valid and saved.');
            } catch (e) {
                alert('Invalid ElevenLabs API Key.');
            }
        }
    }

    async function handleBookRootPathSave() {
        const trimmed = bookRootPathInput.trim();
        const value = trimmed.length ? trimmed : null;
        bookRootPathOverride.set(value);
        
        if (value) {
            try {
                // Try to sync with backend immediately
                const response = await fetch('http://127.0.0.1:8010/api/set-book-root', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ root_path: value })
                });
                if (response.ok) {
                    alert('Book root path saved and synced with backend.');
                } else {
                    alert('Book root path saved locally, but backend sync failed: ' + (await response.text()));
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
    <h2>Google Cloud TTS</h2>
    {#if $googleTtsCredentials}
        <p>Google Credentials are configured.</p>
        <button on:click={() => googleTtsCredentials.set(null)}>Clear Credentials</button>
    {:else}
        <p>Upload your Google Cloud service account JSON file.</p>
        <input type="file" bind:files={googleCredsFile} on:change={handleGoogleCredsUpload} accept=".json" />
    {/if}
</section>

<section>
    <h2>ElevenLabs TTS</h2>
    <p>Enter your ElevenLabs API key.</p>
    <input type="password" bind:value={elApiKeyInput} placeholder="ElevenLabs API Key" />
    <button on:click={handleElApiKeySave}>Save and Validate</button>
    {#if $elevenLabsApiKey}
        <p>ElevenLabs API key is configured.</p>
        <button on:click={() => {elevenLabsApiKey.set(null); elApiKeyInput = ''}}>Clear Key</button>
    {/if}
</section>

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
