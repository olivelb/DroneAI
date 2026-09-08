/** Consume protocol heartbeats before attempting to parse mission events. */
export function replyToStatusPing(
  message: unknown,
  send: (message: string) => void,
): boolean {
  if (typeof message !== "object" || message === null ||
      !("type" in message) || message.type !== "ping") return false;
  send(JSON.stringify({ type: "pong" }));
  return true;
}
