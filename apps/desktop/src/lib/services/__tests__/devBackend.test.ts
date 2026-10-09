import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('node:child_process', () => ({ spawn: vi.fn(() => ({ on: vi.fn() })) }));
vi.mock('node:fs', () => ({ existsSync: vi.fn() }));
vi.mock('tree-kill', () => ({ default: vi.fn() }));

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';

describe('backend certificate environment', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    vi.spyOn(process, 'on').mockReturnValue(process);
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubEnv('SSL_CERT_FILE', 'missing-ca.pem');
    vi.stubEnv('SSL_CERT_DIR', 'missing-ca-dir');
    vi.stubEnv('HTTPS_PROXY', 'http://proxy.example:8080');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('omits missing certificate paths only from the child environment', async () => {
    vi.mocked(existsSync).mockReturnValue(false);
    await import('../../../../scripts/dev-backend.mjs');

    const env = vi.mocked(spawn).mock.calls[0][2]?.env;
    expect(env).not.toHaveProperty('SSL_CERT_FILE');
    expect(env).not.toHaveProperty('SSL_CERT_DIR');
    expect(env?.HTTPS_PROXY).toBe('http://proxy.example:8080');
    expect(process.env.SSL_CERT_FILE).toBe('missing-ca.pem');
    expect(console.warn).toHaveBeenCalledTimes(2);
  });

  it('preserves valid custom certificate paths', async () => {
    vi.mocked(existsSync).mockReturnValue(true);
    await import('../../../../scripts/dev-backend.mjs');

    const env = vi.mocked(spawn).mock.calls[0][2]?.env;
    expect(env?.SSL_CERT_FILE).toBe('missing-ca.pem');
    expect(env?.SSL_CERT_DIR).toBe('missing-ca-dir');
    expect(console.warn).not.toHaveBeenCalled();
  });
});