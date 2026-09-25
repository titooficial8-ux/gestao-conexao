import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DashboardTempoReal from './DashboardTempoReal';
import '../../index.css';

const queryClient = new QueryClient();

const el = document.getElementById('dashboard-tempo-real-root');
if (el) {
  createRoot(el).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <DashboardTempoReal />
      </QueryClientProvider>
    </StrictMode>
  );
}
