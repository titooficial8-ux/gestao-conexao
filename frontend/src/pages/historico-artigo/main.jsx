import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import HistoricoArtigo from './HistoricoArtigo.jsx';
import '../../index.css';
import '../../styles/gc-theme.css';
import './historico-artigo.css';

const el = document.getElementById('historico-artigo-root');
if (el) {
  createRoot(el).render(
    <StrictMode>
      <HistoricoArtigo />
    </StrictMode>,
  );
}
