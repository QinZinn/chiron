# Chiron frontend

The unified interface for the Chiron ecosystem (Mnemosyne, Knowledge Store).
Vite + React + TypeScript. Design: Claude Design project
`4709e4d2-d436-43d1-acc6-94d9de853713` (`Chiron.dc.html`) — the Nocturne
design system (`src/styles/nocturne.css`, copied verbatim) + the Nord palette
(`src/styles/theme.css`).

## Running

```bash
cp .env.example .env   # fill in CHIRON_KS_TOKEN — see the comments in the file
npm install
npm run dev            # http://localhost:5173  (fixed port — Mnemosyne's CORS needs exactly this one)
npm run build && npm run preview   # production build, http://localhost:4173
```

It must run through `dev` or `preview`: Knowledge and Note scan go through a proxy
in the Vite server (`server/chironProxy.ts`); opening `dist/index.html` directly
has no proxy.

## Connections

| Module | How it is called | Notes |
|---|---|---|
| Mnemosyne `:8081` | Directly from the browser | No secret. CORS only allows `localhost`/`127.0.0.1` on ports 5173 and 4173 |
| Knowledge Store | Proxy `/api/ks/*` | The proxy adds `Bearer CHIRON_KS_TOKEN`. GET `/health`, `/nodes`, `/nodes/{id}`, `/edges` and the Note scan routes (allowlist in `server/chironProxy.ts`) |

The frontend calls no outside service.

Secrets never reach the browser: `vite.config.ts` exposes only an allowlist of
public configuration to it (`src/config.ts`).

## Status of each area

| Area | Source | Status |
|---|---|---|
| Chat · Study | Mnemosyne `/socratic/*` | Working |
| Chat · Ask | Mnemosyne `/chat/*` (`mode: ask`) | Working |
| Chat · Solve | Mnemosyne `/chat/*` (`mode: solve`) | Working |
| Flashcards | Mnemosyne `GET /due`, `POST /review` | Working |
| Quiz | Mnemosyne `/quiz/*` | Working |
| Knowledge | KS `GET /nodes`, `/nodes/{id}`, `/edges` | Working: a list grouped by subject, or a concept map (approved edges only) |
| Weak spots | Mnemosyne `GET /weak_cards` | Working |
| To-do | Mnemosyne `/todos` + Pomodoro (frontend only) | Working: items generated from weak cards, added by hand, ticked off; the Pomodoro is only a timer and keeps no history |
| Edit / delete | Mnemosyne `PATCH`, `DELETE` | Edit and delete cards, rename and delete study sets, delete quiz questions, delete sessions, edit the profile |
| Note scan | KS `/notes`, `/extracted` via the proxy → OCR service | Working: OCR → correct the text → extract concepts → review |
| Teach back (Feynman) | Mnemosyne `/study_sets/{id}/feynman_evaluate` | Working |
| Blurting | Mnemosyne `/study_sets/{id}/blurting` | Working |
| Study stats | Mnemosyne `GET /stats` | Working (shown on Flashcards and Weak spots) |
| Settings | localStorage + `GET /me` | Token sign-in, accent colour, collapsed sidebar, star background, connection status |

Each area handles its own errors: when a module is down only that area reports
"cannot connect"; everything else keeps working.

## Deliberate departures from the design

- The "Chiron 2 · Balanced" model picker → a static "Socratic · Mnemosyne" chip:
  Mnemosyne has no model choice, so a dropdown would be a button that does nothing.
- The design's calendar-sync status tag → the real Mnemosyne connection status.
- No attachment / image / microphone buttons: no backend accepts them yet.
- The Flashcards, Quiz, Knowledge and Settings screens are not in the design;
  they reuse the layout of screen 1c (header, title + description, stats row,
  faint rule, list of `.wk` cards).
- The default mode is Study (mock 1a used "Solve").
- "Recent" comes from `GET /socratic` and `GET /chat`, not from localStorage,
  so switching browsers still shows everything and the "ended" state is always right.
- Mnemosyne needs a token: it lives in the browser's localStorage (pasted on the
  Settings screen), not in `.env`.
