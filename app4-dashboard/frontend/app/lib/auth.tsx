"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  createSession,
  deleteSession,
  fetchSession,
} from "./api";
import type { SessionPrincipal } from "./api";

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
  const [authPrincipal, setAuthPrincipal] =
    useState<SessionPrincipal | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  const login = useCallback(async (apiKey: string) => {
    setAuthError(null);
    setAuthStatus("checking");
    try {
      const principal = await createSession(apiKey);
      setAuthPrincipal(principal);
      setAuthStatus("authenticated");
    } catch (error) {
      setAuthPrincipal(null);
      setAuthError(error instanceof Error ? error.message : String(error));
      setAuthStatus("required");
      throw error;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await deleteSession();
    } finally {
      setAuthPrincipal(null);
      setAuthError(null);
      setAuthStatus("required");
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchSession()
      .then((principal) => {
        if (!active) return;
        setAuthPrincipal(principal);
        setAuthStatus("authenticated");
      })
      .catch(() => {
        if (!active) return;
        setAuthPrincipal(null);
        setAuthStatus("required");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const unauthorized = () => {
      setAuthPrincipal(null);
      setAuthStatus("required");
    };
    window.addEventListener("droneai:unauthorized", unauthorized);
    return () =>
      window.removeEventListener("droneai:unauthorized", unauthorized);
  }, []);

  const value: AuthState = {
    authStatus,
    authPrincipal,
    authError,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
