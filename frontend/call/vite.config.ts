import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The patient portal talks to our Express process, which is also the
    // participant in the call. One origin in dev keeps it simple.
    //
    // `API_TARGET` is for `make test-e2e`, which runs its own backend on its own
    // port so a browser test never attaches to — or races — a `make dev` you
    // left running. Unset is the dev default and the only thing anyone types.
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://localhost:3000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
});
