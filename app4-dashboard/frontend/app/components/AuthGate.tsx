"use client";

import React, { useState } from "react";
import { Boxes, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { getApiBaseUrl } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function AuthGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const { authStatus, authPrincipal, authError, login } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (authStatus === "authenticated" && authPrincipal) {
    return <>{children}</>;
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (apiKey.length < 32 || submitting) return;
    setSubmitting(true);
    try {
      await login(apiKey);
      setApiKey("");
    } catch {
      // The store exposes the server-safe error message below.
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#eef2f0] p-4">
      <section className="w-full max-w-md rounded-[28px] border border-[#dce5e1] bg-white p-6 shadow-[0_24px_80px_rgba(23,63,59,0.12)] sm:p-8">
        <div className="flex items-center gap-3">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[#173f3b] text-white">
            <Boxes size={22} />
          </span>
          <div>
            <div className="eyebrow">DroneAI Mission Studio</div>
            <h1 className="mt-1 text-xl font-bold text-[#17201e]">
              Operator sign-in
            </h1>
          </div>
        </div>

        {authStatus === "checking" ? (
          <div className="mt-8 flex items-center gap-3 rounded-2xl bg-[#f4f7f6] p-4 text-sm text-[#61706b]">
            <LoaderCircle size={18} className="animate-spin text-[#0f766e]" />
            Checking the secure API session…
          </div>
        ) : (
          <form onSubmit={submit} className="mt-8 space-y-4">
            <p className="text-sm leading-6 text-[#64716d]">
              Enter the API credential issued by your administrator. It is
              exchanged for an HttpOnly browser session and is never stored in
              the frontend bundle or browser storage.
            </p>
            <label className="block">
              <span className="mb-1.5 block text-xs font-bold uppercase tracking-[0.12em] text-[#61706b]">
                API credential
              </span>
              <div className="flex items-center gap-2 rounded-xl border border-[#ccd8d4] bg-[#f9fbfa] px-3 focus-within:border-[#4ba994]">
                <KeyRound size={17} className="shrink-0 text-[#0f766e]" />
                <input
                  type="password"
                  autoComplete="current-password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="Paste a viewer, operator or admin key"
                  className="min-h-12 w-full bg-transparent text-sm outline-none"
                />
              </div>
            </label>
            {authError && (
              <p role="alert" className="text-sm text-red-600">
                {authError}
              </p>
            )}
            <button
              type="submit"
              disabled={apiKey.length < 32 || submitting}
              className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#0f766e] px-4 text-sm font-bold text-white hover:bg-[#115e59] disabled:cursor-not-allowed disabled:bg-[#c7d4d0]"
            >
              <ShieldCheck size={17} />
              {submitting ? "Signing in…" : "Open Mission Studio"}
            </button>
            <p className="truncate text-center font-mono text-[10px] text-[#8a9692]">
              {getApiBaseUrl()}
            </p>
          </form>
        )}
      </section>
    </main>
  );
}
