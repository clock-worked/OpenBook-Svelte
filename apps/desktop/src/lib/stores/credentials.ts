import { writable } from 'svelte/store';
import type { GoogleCredentials } from '$lib/services/google-tts';

function createStoredWritable<T>(key: string, defaultValue: T) {
    const { subscribe, set, update } = writable<T>(defaultValue);

    if (typeof window !== 'undefined') {
        const storedValue = localStorage.getItem(key);
        if (storedValue) {
            set(JSON.parse(storedValue));
        }
    }

    return {
        subscribe,
        set: (value: T) => {
            if (typeof window !== 'undefined') {
                localStorage.setItem(key, JSON.stringify(value));
            }
            set(value);
        },
        update,
    };
}

export const googleTtsCredentials = createStoredWritable<GoogleCredentials | null>('googleTtsCredentials', null);
export const elevenLabsApiKey = createStoredWritable<string | null>('elevenLabsApiKey', null);
