"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, setCsrfToken, type User, type MfaChallenge, type AuthResponse } from "./api";
import { authRedirect } from "./route-access";

type Auth = {
  user: User | null;
  loading: boolean;
  demoMode: boolean;
  signIn: (email: string, password: string, register: boolean) => Promise<MfaChallenge | void>;
  completeMfa: (challenge: string, code: string) => Promise<void>;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};
const Context = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [demoMode, setDemoMode] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([api.config().catch(() => ({ demo_mode: false })), api.me().catch(() => null)])
      .then(([config, session]) => {
        if (!active) return;
        setDemoMode(config.demo_mode);
        if (session) { setCsrfToken(session.csrf_token); setUser(session.user); }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const acceptSession = useCallback((result: AuthResponse) => {
    setCsrfToken(result.csrf_token);
    setUser(result.user);
  }, []);
  const signIn = useCallback(async (email: string, password: string, register: boolean): Promise<MfaChallenge | void> => {
    const result = register ? await api.register(email, password) : await api.login(email, password);
    if ("mfa_required" in result) return result;
    acceptSession(result);
  }, [acceptSession]);
  const completeMfa = useCallback(async (challenge: string, code: string) => {
    acceptSession(await api.completeMfa(challenge, code));
  }, [acceptSession]);
  const refresh = useCallback(async () => {
    const result = await api.me();
    acceptSession(result);
  }, [acceptSession]);
  const signOut = useCallback(async () => {
    await api.logout();
    setCsrfToken(null);
    setUser(null);
  }, []);
  return <Context.Provider value={{ user, loading, demoMode, signIn, completeMfa, refresh, signOut }}>{children}</Context.Provider>;
}

export function useAuth() {
  const auth = useContext(Context);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, demoMode } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const redirectTo = authRedirect(path, Boolean(user), demoMode);
  useEffect(() => {
    if (!loading && redirectTo) router.replace(redirectTo);
  }, [loading, redirectTo, router]);
  if (loading) return <div className="mx-auto max-w-lg p-12 text-center text-muted">Loading Ahead…</div>;
  if (redirectTo) return null;
  return <>{children}</>;
}
