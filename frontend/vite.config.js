import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// Builda cada pagina React como um bundle separado, direto pra dentro de
// app/static/dist — o Flask so precisa de um <script type="module"> apontando
// pro arquivo .js gerado (nome fixo, sem hash, pra simplificar o template).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../app/static/dist',
    emptyOutDir: false,
    rollupOptions: {
      input: {
        'dashboard-tempo-real': resolve(__dirname, 'src/pages/dashboard-tempo-real/main.jsx'),
        'historico-artigo': resolve(__dirname, 'src/pages/historico-artigo/main.jsx'),
        'gestao-malharia': resolve(__dirname, 'src/pages/malharia/main.jsx'),
        'controle-producao': resolve(__dirname, 'src/pages/controle-producao/main.jsx'),
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: 'chunks/[name]-[hash].js',
        assetFileNames: '[name][extname]',
      },
    },
  },
});
