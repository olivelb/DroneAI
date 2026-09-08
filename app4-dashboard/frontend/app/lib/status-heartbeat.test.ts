import { describe, expect, it, vi } from "vitest";
import { replyToStatusPing } from "./status-heartbeat";

describe("status WebSocket heartbeat", () => {
  it("replies with a JSON pong and consumes the server ping", () => {
    const send = vi.fn();
    expect(replyToStatusPing(JSON.parse('{"type":"ping"}'), send)).toBe(true);
    expect(send).toHaveBeenCalledExactlyOnceWith('{"type":"pong"}');
  });

  it.each([null, undefined, "ping", [], { type: "pong" }, { vol_id: "mission" }])(
    "leaves non-heartbeat messages to the status parser: %j", message => {
      const send = vi.fn();
      expect(replyToStatusPing(message, send)).toBe(false);
      expect(send).not.toHaveBeenCalled();
    },
  );
});
