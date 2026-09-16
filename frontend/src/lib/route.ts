import { useEffect, useState } from 'react';

export type Route =
  | { view: 'chat'; sessionId?: string }
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
      return route.sessionId ? `#/chat/${encodeURIComponent(route.sessionId)}` : '#/';
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
