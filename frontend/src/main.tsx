import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

// Inter, self-hosted. Each weight's CSS carries every subset — Vietnamese
// included — as unicode-range faces, so diacritics come from Inter itself
// rather than falling back to a system font mid-word.
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
// Phosphor icons, as the design system specifies.
import '@phosphor-icons/web/regular/style.css';

import './styles/nocturne.css';
import './styles/theme.css';
import { App } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
