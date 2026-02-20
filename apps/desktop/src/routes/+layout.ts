// Disable SSR for all pages — this app uses browser-only APIs
// (File System Access API, localStorage, etc.) and gains nothing from SSR.
// SSR module-level store subscriptions leak memory during Vite HMR.
export const ssr = false;
