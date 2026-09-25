import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Malharia from './Malharia.jsx';
import { ToastProvider } from '../../components/ui.jsx';
import '../../index.css';
import '../../styles/gc-theme.css';

const queryClient = new QueryClient();

const el = document.getElementById('gestao-malharia-root');
if (el) {
  createRoot(el).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <Malharia />
        </ToastProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}
