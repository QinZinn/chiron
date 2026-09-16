/**
 * Browser-side configuration. Values come from CHIRON_* in frontend/.env, via
 * the explicit allowlist in vite.config.ts — nothing secret is in here.
 */
export interface PublicConfig {
  mnemosyneUrl: string;
  autoStudyCalendar: string;
  ignoredCalendars: string[];
  timezone: string;
}

declare const __CHIRON_PUBLIC_CONFIG__: PublicConfig;

export const config: PublicConfig = __CHIRON_PUBLIC_CONFIG__;
