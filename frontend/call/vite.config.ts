import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The patient portal talks to our Express process, which is also the
    // participant in the call. One origin in dev keeps it simple.
    proxy: { '/api': { target: 'http://localhost:3000', changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') } },
  },
});
