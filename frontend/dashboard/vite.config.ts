import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    // 5174, because :5173 is the patient portal and `make dev` runs both.
    port: 5174,
    // The same relative `/api` the portal uses, so neither frontend holds an
    // API base URL. In production a Vercel rewrite does this instead.
    proxy: { '/api': { target: 'http://localhost:3000', changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') } },
  },
});
