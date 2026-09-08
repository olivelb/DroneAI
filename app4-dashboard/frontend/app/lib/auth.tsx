"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createSession, deleteSession, fetchSession } from "./api";
import type { SessionPrincipal } from "./api";
import { broadcastSessionChange, subscribeSessionChanges } from "./auth-session-events";
import { gsTileCacheIdentity, purgeGsTilePersistentCaches } from "./gstile/persistent-range-cache";

export type AuthStatus = "checking" | "required" | "authenticated";
type AuthState = {
  authStatus: AuthStatus;
  authPrincipal: SessionPrincipal | null;
  authError: string | null;
  login: (apiKey: string) => Promise<void>;
  logout: () => Promise<void>;
};
const AuthContext = createContext<AuthState | null>(null);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [authPrincipal, setAuthPrincipal] = useState<SessionPrincipal | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const revision = useRef(0);

  const clearSession = useCallback(() => {
    ++revision.current;
    setAuthPrincipal(null);
    setAuthError(null);
    setAuthStatus("required");
    return purgeGsTilePersistentCaches();
  }, []);

  const loadSession = useCallback(async (load: () => Promise<SessionPrincipal>) => {
    const request = ++revision.current;
    setAuthStatus("checking");
    setAuthPrincipal(null);
    setAuthError(null);
    try {
      const principal = await load();
      if (request !== revision.current) return;
      await purgeGsTilePersistentCaches(gsTileCacheIdentity(principal));
      if (request !== revision.current) return;
      setAuthPrincipal(principal);
      setAuthStatus("authenticated");
    } catch (error) {
      if (request !== revision.current) return;
      const clearing = clearSession();
      const clearedRevision = revision.current;
      await clearing;
      if (clearedRevision !== revision.current) return;
      setAuthError(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }, [clearSession]);

  const login = useCallback(async (apiKey: string) => {
    await loadSession(async () => {
      const principal = await createSession(apiKey);
      broadcastSessionChange("login");
      return principal;
    });
  }, [loadSession]);

  const logout = useCallback(async () => {
    ++revision.current;
    setAuthStatus("checking");
    setAuthPrincipal(null);
    try {
      await deleteSession();
    } finally {
      broadcastSessionChange("logout");
      await clearSession();
    }
  }, [clearSession]);

  const invalidateSessionRequest = useCallback(() => { ++revision.current; }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => { if (active) void loadSession(fetchSession).catch(() => undefined); });
    const unsubscribe = subscribeSessionChanges(change => {
      if (change === "logout") void clearSession();
      else void loadSession(fetchSession).catch(() => undefined);
    });
    return () => { active = false; invalidateSessionRequest(); unsubscribe(); };
  }, [loadSession, clearSession, invalidateSessionRequest]);

  useEffect(() => {
    const unauthorized = () => {
      broadcastSessionChange("logout");
      void clearSession();
    };
    window.addEventListener("droneai:unauthorized", unauthorized);
    return () => window.removeEventListener("droneai:unauthorized", unauthorized);
  }, [clearSession]);

  const value: AuthState = { authStatus, authPrincipal, authError, login, logout };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
