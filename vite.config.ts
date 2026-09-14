import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { chironProxy } from './server/chironProxy';

// See .env.example for what every CHIRON_* variable means.
export default defineConfig(({ mode }) => {
  // Read CHIRON_* here, on the server side only. `envPrefix` stays at Vite's
  // default (VITE_), so no CHIRON_* variable is exposed to the browser by
  // prefix; the browser gets exactly the allowlist in `publicConfig` below.
  const env = loadEnv(mode, process.cwd(), 'CHIRON_');
  const trimSlash = (s: string) => s.replace(/\/+$/, '');

  const publicConfig = {
    mnemosyneUrl: trimSlash(env.CHIRON_MNEMOSYNE_URL || 'http://127.0.0.1:8081'),
    defaultUserId: env.CHIRON_MNEMOSYNE_USER_ID || '',
    autoStudyCalendar: env.CHIRON_GCAL_AUTO_STUDY_CALENDAR || 'Auto-Study',
    ignoredCalendars: (env.CHIRON_GCAL_IGNORED_CALENDARS || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
    timezone: env.CHIRON_TIMEZONE || 'Asia/Ho_Chi_Minh',
  };

  return {
    plugins: [
      react(),
      chironProxy({
        ksUrl: trimSlash(env.CHIRON_KS_URL || 'http://127.0.0.1:8080'),
        ksToken: env.CHIRON_KS_TOKEN || '',
        oneApiBase: env.CHIRON_ONE_API_BASE || 'https://api.withone.ai',
        oneSecret: env.CHIRON_ONE_SECRET || '',
        gcalConnectionKey: env.CHIRON_ONE_GCAL_CONNECTION_KEY || '',
      }),
    ],
    define: {
      __CHIRON_PUBLIC_CONFIG__: JSON.stringify(publicConfig),
    },
    // Ports are pinned (strictPort) because Mnemosyne's CORS allowlist names
    // exactly these two origins. A silent fallback to 5174 would look like a
    // Mnemosyne outage in the browser.
    server: { host: 'localhost', port: 5173, strictPort: true },
    preview: { host: 'localhost', port: 4173, strictPort: true },
  };
});
