import type { SessionPrincipal } from "./types";
import {
  decoder,
  integerValue,
  nonEmptyString,
  objectWith,
  oneOf,
} from "./contract-decoder";

export const parseSessionPrincipal = decoder<SessionPrincipal>(
  "authentication",
  objectWith({
    subject: nonEmptyString,
    role: oneOf("viewer", "operator", "admin"),
    organization_id: nonEmptyString,
  }, {
    expires_in_seconds: integerValue,
  }),
);
