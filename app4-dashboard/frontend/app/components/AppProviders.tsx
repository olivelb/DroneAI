"use client";

import React from "react";
import { AuthProvider, useAuth } from "../lib/auth";
import { I18nProvider } from "../lib/i18n/provider";
import { MissionRuntimeProvider } from "../lib/mission-runtime";
import { StoreProvider } from "../lib/store";
import { WorkspaceDataProvider } from "../lib/workspace-data";
import AuthGate from "./AuthGate";

export default function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <I18nProvider>
      <AuthProvider>
        <IdentityWorkspace>{children}</IdentityWorkspace>
      </AuthProvider>
    </I18nProvider>
  );
}

function IdentityWorkspace({ children }: { children: React.ReactNode }) {
  const { authStatus, authPrincipal } = useAuth();
  return (
        <WorkspaceDataProvider key={JSON.stringify([authStatus, authPrincipal])}>
          <MissionRuntimeProvider>
            <StoreProvider>
              <AuthGate>{children}</AuthGate>
            </StoreProvider>
          </MissionRuntimeProvider>
        </WorkspaceDataProvider>
  );
}
