import { resolve } from 'node:path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dependencies are externalized for main/preload by default (electron-vite 6).
export default defineConfig({
  main: {
    build: { outDir: 'out/main', lib: { entry: resolve('electron/main.ts') } },
  },
  preload: {
    build: { outDir: 'out/preload', lib: { entry: resolve('electron/preload.ts') } },
  },
  renderer: {
    root: '.',
    build: { outDir: 'out/renderer', minify: true, rollupOptions: { input: { index: resolve('index.html') } } },
    resolve: { alias: { '@': resolve('src'), '@shared': resolve('shared') } },
    plugins: [react(), tailwindcss()],
  },
})
