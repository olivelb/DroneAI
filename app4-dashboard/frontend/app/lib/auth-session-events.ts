type SessionChange = "login" | "logout";
const CHANNEL = "droneai-session-change";
const isSessionChange = (value: unknown): value is SessionChange => value === "login" || value === "logout";

export function broadcastSessionChange(change: SessionChange): void {
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage(change);
    channel.close();
  } else {
    try { localStorage.setItem(CHANNEL, JSON.stringify([change, crypto.randomUUID()])); } catch { /* Storage unavailable. */ }
  }
}

export function subscribeSessionChanges(listener: (change: SessionChange) => void): () => void {
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = event => { if (isSessionChange(event.data)) listener(event.data); };
    return () => channel.close();
  }
  const changed = (event: StorageEvent) => {
    if (event.key !== CHANNEL || !event.newValue) return;
    try { const [change] = JSON.parse(event.newValue); if (isSessionChange(change)) listener(change); } catch { /* Invalid event. */ }
  };
  window.addEventListener("storage", changed);
  return () => window.removeEventListener("storage", changed);
}
