import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Cross-origin isolation headers. The Lean backend links Lean's wasm32 runtime,
// which is built with -pthread, so it needs SharedArrayBuffer — only available
// in a cross-origin-isolated context. These headers set that up for `vite dev`
// and `vite preview`; the production equivalent is in `vercel.json`.
const coiHeaders = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { headers: coiHeaders },
  preview: { headers: coiHeaders },
})
