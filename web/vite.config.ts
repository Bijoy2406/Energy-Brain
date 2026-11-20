import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // Tailwind is used only by src/components/ui (see tweenly-ai-recorder-utils/review-theme.css).
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    restoreMocks: true,
    unstubGlobals: true,
    // These tests render the whole app with charts. Under CPU load a single test can pass 5 s, so a short
    // limit gives false failures; a really hung test still fails, just later.
    testTimeout: 30000,
    hookTimeout: 30000,
  },
})
