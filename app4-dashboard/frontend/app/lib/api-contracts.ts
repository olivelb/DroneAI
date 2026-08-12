import type { SessionPrincipal } from "./types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isRole = (
  value: unknown,
): value is SessionPrincipal["role"] =>
  value === "viewer" || value === "operator" || value === "admin";

export const parseSessionPrincipal = (value: unknown): SessionPrincipal => {
  if (
    !isRecord(value)
    || typeof value.subject !== "string"
    || !isRole(value.role)
    || typeof value.organization_id !== "string"
    || !value.organization_id
    || (
      value.expires_in_seconds !== undefined
      && typeof value.expires_in_seconds !== "number"
    )
  ) {
    throw new Error("Invalid authentication response contract");
  }
  return {
    subject: value.subject,
    role: value.role,
    organization_id: value.organization_id,
    ...(value.expires_in_seconds === undefined
      ? {}
      : { expires_in_seconds: value.expires_in_seconds }),
  };
};
