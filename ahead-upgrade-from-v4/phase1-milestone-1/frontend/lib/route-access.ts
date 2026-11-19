/**
 * Client-side route presentation policy (not an API authorization boundary).
 * A single source of truth for both personal navigation and AuthGate keeps
 * newly visible tabs from silently redirecting back to Home.
 * Backend endpoints must still enforce server-side sessions and user ownership.
 */
export const NAV_TABS = [
  { href: "/", label: "Home", personal: true },
  { href: "/accounts", label: "Banks", personal: true },
  { href: "/activity", label: "Activity", personal: true },
  { href: "/alerts", label: "Alerts", personal: false },
  { href: "/plan", label: "Plan", personal: false },
  { href: "/ask", label: "Ask", personal: false },
] as const;

export const PUBLIC_PATHS = ["/login", "/forgot-password", "/reset-password", "/verify-email"] as const;
export const PERSONAL_PATHS = [
  ...NAV_TABS.filter(tab => tab.personal).map(tab => tab.href),
  "/settings/security",
  "/settings/privacy",
] as const;

export function visibleTabs(demoMode: boolean) {
  return NAV_TABS.filter(tab => demoMode || tab.personal);
}

export function authRedirect(path: string, signedIn: boolean, demoMode: boolean): string | null {
  const publicRoute = PUBLIC_PATHS.some(route => route === path);
  if (!signedIn) return publicRoute ? null : "/login";
  if (path === "/login") return "/";
  if (!demoMode && !publicRoute && !PERSONAL_PATHS.some(route => route === path)) return "/";
  return null;
}
