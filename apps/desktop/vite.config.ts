import devtoolsJson from 'vite-plugin-devtools-json';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const enableDevtoolsJson = env.VITE_DEVTOOLS_JSON === '1';
  const disableHmr = env.VITE_DISABLE_HMR === '1';

  return {
    plugins: [
      sveltekit(),
      ...(enableDevtoolsJson ? [devtoolsJson()] : []),

    ],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      hmr: disableHmr ? false : undefined,
      fs: {
        strict: true,
        allow: [process.cwd()]
      }
    }
  };
});