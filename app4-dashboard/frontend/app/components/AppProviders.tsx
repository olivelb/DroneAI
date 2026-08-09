"use client";

import React from "react";
import { AuthProvider } from "../lib/auth";
import { I18nProvider } from "../lib/i18n/provider";
import { MissionRuntimeProvider } from "../lib/mission-runtime";
import { StoreProvider } from "../lib/store";
import { WorkspaceDataProvider } from "../lib/workspace-data";
import AuthGate from "./AuthGate";

export default function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <I18nProvider>
      <AuthProvider>
        <WorkspaceDataProvider>
          <MissionRuntimeProvider>
            <StoreProvider>
              <AuthGate>{children}</AuthGate>
            </StoreProvider>
          </MissionRuntimeProvider>
        </WorkspaceDataProvider>
      </AuthProvider>
    </I18nProvider>
  );
}
