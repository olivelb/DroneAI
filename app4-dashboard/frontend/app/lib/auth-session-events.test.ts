import { afterEach, describe, expect, it, vi } from "vitest";
import { broadcastSessionChange, subscribeSessionChanges } from "./auth-session-events";

afterEach(() => vi.unstubAllGlobals());
describe("session notifications", () => {
  it("broadcasts only the event and closes its transport", () => {
    const channel = { postMessage: vi.fn(), close: vi.fn() };
    vi.stubGlobal("BroadcastChannel", class { constructor() { return channel; } });
    broadcastSessionChange("logout");
    expect(channel.postMessage).toHaveBeenCalledWith({ change: "logout", source: expect.any(String) });
    expect(channel.close).toHaveBeenCalledOnce();
  });
  it("validates incoming events and closes the subscription", () => {
    const channel = { onmessage: (event: { data: unknown }) => { void event; }, close: vi.fn() };
    vi.stubGlobal("BroadcastChannel", class { constructor() { return channel; } });
    const listener = vi.fn();
    const unsubscribe = subscribeSessionChanges(listener);
    for (const data of [null, {}, "invalid", {change: "logout", source: "other"}, {change: "login", source: "other"}]) channel.onmessage({ data });
    expect(listener.mock.calls).toEqual([["logout"], ["login"]]);
    unsubscribe();
    expect(channel.close).toHaveBeenCalledOnce();
  });
  it("ignores notifications from its own page", () => {
    const channel = { onmessage: (event: { data: unknown }) => { void event; }, postMessage: vi.fn(), close: vi.fn() };
    vi.stubGlobal("BroadcastChannel", class { constructor() { return channel; } });
    const listener = vi.fn();
    subscribeSessionChanges(listener);
    broadcastSessionChange("login");
    channel.onmessage({ data: channel.postMessage.mock.calls[0][0] });
    expect(listener).not.toHaveBeenCalled();
  });
  it("validates the storage fallback and removes its listener", () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const addEventListener = vi.fn();
    const removeEventListener = vi.fn();
    vi.stubGlobal("window", { addEventListener, removeEventListener });
    const listener = vi.fn();
    const unsubscribe = subscribeSessionChanges(listener);
    const changed = addEventListener.mock.calls[0][1];
    for (const newValue of ["bad", "null", "{}", '["invalid"]', '["logout","nonce"]']) {
      changed({ key: "droneai-session-change", newValue });
    }
    changed({ key: "another-app", newValue: '["login"]' });
    expect(listener.mock.calls).toEqual([["logout"]]);
    unsubscribe();
    expect(removeEventListener).toHaveBeenCalledWith("storage", changed);
  });
});
