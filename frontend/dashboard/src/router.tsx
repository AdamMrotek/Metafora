import { useCallback, useEffect, useState, type ReactNode } from 'react';

/**
 * Four screens and no router dependency.
 *
 * The whole of it: read `location.pathname`, push to it, and re-render on
 * `popstate`. Deep links work because `vercel.json` sends every unmatched path
 * to `index.html`, which is the one piece of hosting this file depends on.
 */

export type Route =
  | { name: 'dashboard' }
  | { name: 'interview'; id: string }
  | { name: 'patients' }
  | { name: 'deployments' };

function parse(pathname: string): Route {
  const match = /^\/interviews\/(.+)$/.exec(pathname);
  if (match?.[1]) return { name: 'interview', id: decodeURIComponent(match[1]) };
  if (pathname === '/patients') return { name: 'patients' };
  if (pathname === '/deployments') return { name: 'deployments' };
  return { name: 'dashboard' };
}

export function navigate(to: string): void {
  if (to === window.location.pathname) return;
  window.history.pushState({}, '', to);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.pathname));

  useEffect(() => {
    const onPop = () => setRoute(parse(window.location.pathname));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  return route;
}

/** An anchor that navigates without a reload — and is still a real `href`, so
 *  middle-click, copy-link and the status bar all behave. */
export function Link({
  to,
  children,
  ...rest
}: { to: string; children: ReactNode } & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const onClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      navigate(to);
    },
    [to],
  );
  return (
    <a href={to} onClick={onClick} {...rest}>
      {children}
    </a>
  );
}
