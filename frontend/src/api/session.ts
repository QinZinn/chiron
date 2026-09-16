/**
 * The learner's Mnemosyne token, held in this browser.
 *
 * It is NOT in frontend/.env: the browser is the thing that needs it, so
 * putting it in the dev server's environment would only add a copy. This is
 * the ordinary shape of a web session — the credential lives in the browser
 * that holds the session — except that the token is minted by the
 * `mint-token` CLI rather than by a login form.
 *
 * Kept in one module so exactly one place reads and writes it; `api/http.ts`
 * attaches it to Mnemosyne requests and nothing else ever sees it.
 */
import { load, save } from '../lib/storage';

const KEY = 'chiron.auth.v1';

interface Stored {
  token: string;
}

let current: string = load<Stored>(KEY, { token: '' }).token;

const listeners = new Set<(token: string) => void>();

export function getToken(): string {
  return current;
}

export function setToken(token: string): void {
  current = token.trim();
  save(KEY, { token: current });
  listeners.forEach((fn) => fn(current));
}

export function onTokenChange(fn: (token: string) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Mnemosyne's minted tokens all carry this prefix (auth.rs TOKEN_PREFIX). */
export const TOKEN_PREFIX = 'mnem_';
