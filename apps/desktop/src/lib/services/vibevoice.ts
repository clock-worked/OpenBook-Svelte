export async function listVoiceSamples(samplesRoot: string): Promise<string[]> {
  const response = await fetch('http://127.0.0.1:8010/api/list-voice-samples', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ samples_root: samplesRoot }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || 'Failed to list voice samples');
  }

  const data = await response.json();
  return data.samples || [];
}
