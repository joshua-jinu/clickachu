import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {
    build: {
      // electron-store is ESM-only; bundle it into the CJS main build instead of require()-ing it.
      externalizeDeps: { exclude: ['electron-store'] },
      rollupOptions: {
        input: { index: resolve(__dirname, 'electron/main/index.ts') },
      },
    },
  },
  preload: {
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'electron/preload/index.ts') },
      },
    },
  },
  renderer: {
    root: resolve(__dirname, 'src'),
    resolve: {
      alias: { '@shared': resolve(__dirname, 'src/shared') },
    },
    plugins: [react(), tailwindcss()],
    build: {
      rollupOptions: {
        input: {
          orb: resolve(__dirname, 'src/orb/index.html'),
          quickbar: resolve(__dirname, 'src/quickbar/index.html'),
          panel: resolve(__dirname, 'src/panel/index.html'),
        },
      },
    },
  },
})
