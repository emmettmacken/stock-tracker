// Client-side auth helpers. The backend issues HttpOnly access/refresh cookies,
// so every API call must send credentials. fetchWithAuth transparently refreshes
// an expired access token once, and bounces to /login when the session is truly
// gone.

// Empty base = same-origin requests. Next.js rewrites (see next.config.mjs) proxy
// /api/* and /auth/* to the backend, so cookies stay first-party to this origin.
export const BASE = "";

const PUBLIC_PATHS = new Set(["/login", "/register", "/verify-email"]);

// De-dupe concurrent refreshes: if many requests 401 at once, they share one
// /auth/refresh round-trip instead of stampeding the endpoint.
let refreshing: Promise<boolean> | null = null;

function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  if (!PUBLIC_PATHS.has(window.location.pathname)) {
    window.location.href = "/login";
  }
}

/**
 * fetch() wrapper that always sends cookies. On a 401 it attempts a single
 * token refresh and retries the original request once; if the refresh fails the
 * user is redirected to /login.
 */
export async function fetchWithAuth(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const opts: RequestInit = { ...options, credentials: "include" };

  const res = await fetch(url, opts);
  if (res.status !== 401) return res;

  const refreshed = await tryRefresh();
  if (!refreshed) {
    redirectToLogin();
    return res;
  }

  const retry = await fetch(url, opts);
  if (retry.status === 401) redirectToLogin();
  return retry;
}

/** POST /auth/logout, clearing cookies server-side, then go to /login. */
export async function logout(): Promise<void> {
  try {
    await fetch(`${BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
  } finally {
    if (typeof window !== "undefined") window.location.href = "/login";
  }
}
