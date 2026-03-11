// src/lib/services/google-tts.ts
import * as jose from 'jose';

const GOOGLE_AUTH_URL = 'https://oauth2.googleapis.com/token';
const GOOGLE_TTS_API_URL = 'https://texttospeech.googleapis.com/v1beta1/text:synthesize';
const GOOGLE_VOICES_API_URL = 'https://texttospeech.googleapis.com/v1/voices';


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

interface AccessToken {
    token: string;
    expiresAt: number;
}

let accessToken: AccessToken | null = null;

async function getAccessToken(credentials: GoogleCredentials): Promise<string> {
    if (accessToken && accessToken.expiresAt > Date.now()) {
        return accessToken.token;
    }

    const privateKey = await jose.importPKCS8(credentials.private_key, 'RS256');
    
    const jwt = await new jose.SignJWT({})
        .setProtectedHeader({ alg: 'RS256', typ: 'JWT' })
        .setIssuedAt()
        .setIssuer(credentials.client_email)
        .setAudience(GOOGLE_AUTH_URL)
        .setExpirationTime('1h')
        .setSubject(credentials.client_email)
        .sign(privateKey);

    const response = await fetch(GOOGLE_AUTH_URL, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            assertion: jwt,
        }),
    });

    if (!response.ok) {
        throw new Error('Failed to get access token from Google');
    }

    const tokenData = await response.json();
    accessToken = {
        token: tokenData.access_token,
        expiresAt: Date.now() + (tokenData.expires_in - 60) * 1000,
    };
    return accessToken.token;
}

export async function listVoices(credentials: GoogleCredentials): Promise<any[]> {
    const token = await getAccessToken(credentials);
    const response = await fetch(GOOGLE_VOICES_API_URL, {
        headers: {
            Authorization: `Bearer ${token}`,
        },
    });
    if (!response.ok) {
        console.error('Failed to list voices');
        return [];
    }
    const data = await response.json();
    return data.voices || [];
}

export async function synthesizeAudio(
    text: string,
    voiceName: string,
    accent: string,
    credentials: GoogleCredentials
): Promise<ArrayBuffer | null> {
    const token = await getAccessToken(credentials);
    const fullVoiceName = `${accent}-${voiceName}`;

    const body = {
        input: { text },
        voice: { languageCode: accent, name: fullVoiceName },
        audioConfig: { audioEncoding: 'LINEAR16', sampleRateHertz: 24000 },
    };

    const response = await fetch(GOOGLE_TTS_API_URL, {
        method: 'POST',
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        console.error('Google TTS request failed', await response.text());
        return null;
    }

    const data = await response.json();
    if (data.audioContent) {
        const binary = atob(data.audioContent);
        const len = binary.length;
        const buffer = new ArrayBuffer(len);
        const view = new Uint8Array(buffer);
        for (let i = 0; i < len; i++) {
            view[i] = binary.charCodeAt(i);
        }
        return buffer;
    }
    return null;
}

export async function validateCredentials(credentials: GoogleCredentials): Promise<boolean> {
    try {
        await getAccessToken(credentials);
        return true;
    } catch (e) {
        console.error('Credential validation failed', e);
        return false;
    }
}
