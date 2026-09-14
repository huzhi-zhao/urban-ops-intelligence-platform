import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import {defineConfig} from 'vite';

// Relative base so the build works from any static host path, not only a domain root.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
});
