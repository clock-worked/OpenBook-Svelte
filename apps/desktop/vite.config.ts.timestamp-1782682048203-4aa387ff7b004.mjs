// vite.config.ts
import devtoolsJson from "file:///c:/Users/Chad/Documents/Code/JavaScript/OpenBook-svelte/apps/desktop/node_modules/vite-plugin-devtools-json/dist/index.mjs";
import { sveltekit } from "file:///c:/Users/Chad/Documents/Code/JavaScript/OpenBook-svelte/apps/desktop/node_modules/@sveltejs/kit/src/exports/vite/index.js";
import { defineConfig, loadEnv } from "file:///c:/Users/Chad/Documents/Code/JavaScript/OpenBook-svelte/apps/desktop/node_modules/vite/dist/node/index.js";
var vite_config_default = defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const enableDevtoolsJson = env.VITE_DEVTOOLS_JSON === "1";
  const disableHmr = env.VITE_DISABLE_HMR === "1";
  return {
    plugins: [
      sveltekit(),
      ...enableDevtoolsJson ? [devtoolsJson()] : []
    ],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      hmr: disableHmr ? false : void 0,
      fs: {
        strict: true,
        allow: [process.cwd()]
      }
    }
  };
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcudHMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCJjOlxcXFxVc2Vyc1xcXFxDaGFkXFxcXERvY3VtZW50c1xcXFxDb2RlXFxcXEphdmFTY3JpcHRcXFxcT3BlbkJvb2stc3ZlbHRlXFxcXGFwcHNcXFxcZGVza3RvcFwiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9maWxlbmFtZSA9IFwiYzpcXFxcVXNlcnNcXFxcQ2hhZFxcXFxEb2N1bWVudHNcXFxcQ29kZVxcXFxKYXZhU2NyaXB0XFxcXE9wZW5Cb29rLXN2ZWx0ZVxcXFxhcHBzXFxcXGRlc2t0b3BcXFxcdml0ZS5jb25maWcudHNcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfaW1wb3J0X21ldGFfdXJsID0gXCJmaWxlOi8vL2M6L1VzZXJzL0NoYWQvRG9jdW1lbnRzL0NvZGUvSmF2YVNjcmlwdC9PcGVuQm9vay1zdmVsdGUvYXBwcy9kZXNrdG9wL3ZpdGUuY29uZmlnLnRzXCI7aW1wb3J0IGRldnRvb2xzSnNvbiBmcm9tICd2aXRlLXBsdWdpbi1kZXZ0b29scy1qc29uJztcclxuaW1wb3J0IHsgc3ZlbHRla2l0IH0gZnJvbSAnQHN2ZWx0ZWpzL2tpdC92aXRlJztcclxuaW1wb3J0IHsgZGVmaW5lQ29uZmlnLCBsb2FkRW52IH0gZnJvbSAndml0ZSc7XHJcblxyXG5leHBvcnQgZGVmYXVsdCBkZWZpbmVDb25maWcoKHsgbW9kZSB9KSA9PiB7XHJcbiAgY29uc3QgZW52ID0gbG9hZEVudihtb2RlLCBwcm9jZXNzLmN3ZCgpLCAnJyk7XHJcbiAgY29uc3QgZW5hYmxlRGV2dG9vbHNKc29uID0gZW52LlZJVEVfREVWVE9PTFNfSlNPTiA9PT0gJzEnO1xyXG4gIGNvbnN0IGRpc2FibGVIbXIgPSBlbnYuVklURV9ESVNBQkxFX0hNUiA9PT0gJzEnO1xyXG5cclxuICByZXR1cm4ge1xyXG4gICAgcGx1Z2luczogW1xyXG4gICAgICBzdmVsdGVraXQoKSxcclxuICAgICAgLi4uKGVuYWJsZURldnRvb2xzSnNvbiA/IFtkZXZ0b29sc0pzb24oKV0gOiBbXSksXHJcblxyXG4gICAgXSxcclxuICAgIHNlcnZlcjoge1xyXG4gICAgICBob3N0OiAnMTI3LjAuMC4xJyxcclxuICAgICAgcG9ydDogNTE3MyxcclxuICAgICAgc3RyaWN0UG9ydDogdHJ1ZSxcclxuICAgICAgaG1yOiBkaXNhYmxlSG1yID8gZmFsc2UgOiB1bmRlZmluZWQsXHJcbiAgICAgIGZzOiB7XHJcbiAgICAgICAgc3RyaWN0OiB0cnVlLFxyXG4gICAgICAgIGFsbG93OiBbcHJvY2Vzcy5jd2QoKV1cclxuICAgICAgfVxyXG4gICAgfVxyXG4gIH07XHJcbn0pOyJdLAogICJtYXBwaW5ncyI6ICI7QUFBZ1osT0FBTyxrQkFBa0I7QUFDemEsU0FBUyxpQkFBaUI7QUFDMUIsU0FBUyxjQUFjLGVBQWU7QUFFdEMsSUFBTyxzQkFBUSxhQUFhLENBQUMsRUFBRSxLQUFLLE1BQU07QUFDeEMsUUFBTSxNQUFNLFFBQVEsTUFBTSxRQUFRLElBQUksR0FBRyxFQUFFO0FBQzNDLFFBQU0scUJBQXFCLElBQUksdUJBQXVCO0FBQ3RELFFBQU0sYUFBYSxJQUFJLHFCQUFxQjtBQUU1QyxTQUFPO0FBQUEsSUFDTCxTQUFTO0FBQUEsTUFDUCxVQUFVO0FBQUEsTUFDVixHQUFJLHFCQUFxQixDQUFDLGFBQWEsQ0FBQyxJQUFJLENBQUM7QUFBQSxJQUUvQztBQUFBLElBQ0EsUUFBUTtBQUFBLE1BQ04sTUFBTTtBQUFBLE1BQ04sTUFBTTtBQUFBLE1BQ04sWUFBWTtBQUFBLE1BQ1osS0FBSyxhQUFhLFFBQVE7QUFBQSxNQUMxQixJQUFJO0FBQUEsUUFDRixRQUFRO0FBQUEsUUFDUixPQUFPLENBQUMsUUFBUSxJQUFJLENBQUM7QUFBQSxNQUN2QjtBQUFBLElBQ0Y7QUFBQSxFQUNGO0FBQ0YsQ0FBQzsiLAogICJuYW1lcyI6IFtdCn0K
