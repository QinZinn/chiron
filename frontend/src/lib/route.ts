import { useEffect, useState } from 'react';

export type Route =
  | { view: 'chat'; kind?: 'socratic' | 'chat'; sessionId?: string; newSetId?: string }
  | { view: 'flashcards' }
  | { view: 'quiz' }
  | { view: 'knowledge'; nodeId?: string }
  | { view: 'notes'; noteId?: string }
  | { view: 'weak' }
  | { view: 'feynman' }
  | { view: 'settings' };

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  switch (parts[0]) {
    case 'chat':
      // #/chat/new/<setId> opens the composer with that study set chosen —
      // what "Học bài" on a weak point needs in order to mean something.
      if (parts[1] === 'new') return { view: 'chat', newSetId: parts[2] };
      // Socratic sessions and ask/solve conversations live in different
      // tables and have different endpoints, so the URL says which it is
      // rather than making the view guess from the id.
      if (parts[1] === 's') return { view: 'chat', kind: 'socratic', sessionId: parts[2] };
      if (parts[1] === 'c') return { view: 'chat', kind: 'chat', sessionId: parts[2] };
      return { view: 'chat', kind: 'socratic', sessionId: parts[1] };
    case 'flashcards':
    case 'quiz':
    case 'feynman':
    case 'weak':
    case 'settings':
      return { view: parts[0] };
    case 'knowledge':
      return { view: 'knowledge', nodeId: parts[1] };
    case 'notes':
      return { view: 'notes', noteId: parts[1] };
    default:
      return { view: 'chat' };
  }
}

export function href(route: Route): string {
  switch (route.view) {
    case 'chat':
      if (route.sessionId) {
        return `#/chat/${route.kind === 'chat' ? 'c' : 's'}/${encodeURIComponent(route.sessionId)}`;
      }
      return route.newSetId ? `#/chat/new/${encodeURIComponent(route.newSetId)}` : '#/';
    case 'knowledge':
      return route.nodeId ? `#/knowledge/${encodeURIComponent(route.nodeId)}` : '#/knowledge';
    case 'notes':
      return route.noteId ? `#/notes/${encodeURIComponent(route.noteId)}` : '#/notes';
    default:
      return `#/${route.view}`;
  }
}

export function navigate(route: Route): void {
  window.location.hash = href(route);
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}
