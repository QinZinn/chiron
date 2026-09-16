import { useEffect, useState } from 'react';

export type Route =
  | { view: 'chat'; sessionId?: string; newSetId?: string }
  | { view: 'flashcards' }
  | { view: 'quiz' }
  | { view: 'knowledge'; nodeId?: string }
  | { view: 'weak' }
  | { view: 'schedule' }
  | { view: 'settings' };

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  switch (parts[0]) {
    case 'chat':
      // #/chat/new/<setId> opens the composer with that study set chosen —
      // what "Học bài" on a weak point needs in order to mean something.
      if (parts[1] === 'new') return { view: 'chat', newSetId: parts[2] };
      return { view: 'chat', sessionId: parts[1] };
    case 'flashcards':
    case 'quiz':
    case 'weak':
    case 'schedule':
    case 'settings':
      return { view: parts[0] };
    case 'knowledge':
      return { view: 'knowledge', nodeId: parts[1] };
    default:
      return { view: 'chat' };
  }
}

export function href(route: Route): string {
  switch (route.view) {
    case 'chat':
      if (route.sessionId) return `#/chat/${encodeURIComponent(route.sessionId)}`;
      return route.newSetId ? `#/chat/new/${encodeURIComponent(route.newSetId)}` : '#/';
    case 'knowledge':
      return route.nodeId ? `#/knowledge/${encodeURIComponent(route.nodeId)}` : '#/knowledge';
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
