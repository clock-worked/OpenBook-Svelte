import { writable } from 'svelte/store';

/**
 * Google service-account key JSON (the shape the settings store persists).
 * Formerly exported by the deleted, unused FE Google TTS client; all real
 * cloud TTS runs BE-side (py_services/audio_generation_service.py).
 */
export interface GoogleCredentials {
    type: string;
    project_id: string;
    private_key_id: string;
    private_key: string;
    client_email: string;
    client_id: string;
    auth_uri: string;
    token_uri: string;
    auth_provider_x509_cert_url: string;
    client_x509_cert_url: string;
}

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
