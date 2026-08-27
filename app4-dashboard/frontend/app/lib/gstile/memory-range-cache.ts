/** Byte-bounded SLRU: demand protects up to 75%; speculation uses probation. */
export class GsTileMemoryRangeCache {
  readonly #maximumBytes: number;
  readonly #maximumProtectedBytes: number;
  readonly #onEvict: (key: string) => void;
  readonly #probation = new Map<string, ArrayBuffer>();
  readonly #protected = new Map<string, ArrayBuffer>();
  #bytes = 0;
  #protectedBytes = 0;

  constructor(maximumBytes: number, onEvict: (key: string) => void) {
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 0) {
      throw new Error("GSTile cache size must be a non-negative integer");
    }
    this.#maximumBytes = maximumBytes;
    this.#maximumProtectedBytes = Math.floor(maximumBytes * 0.75);
    this.#onEvict = onEvict;
  }

  get size() { return this.#probation.size + this.#protected.size; }
  get bytes() { return this.#bytes; }
  get protectedBytes() { return this.#protectedBytes; }

  has(key: string) {
    return this.#protected.has(key) || this.#probation.has(key);
  }

  get(key: string, priority: "critical" | "prefetch") {
    const protectedContent = this.#protected.get(key);
    if (protectedContent) {
      // Speculative probes must not extend the lifetime of demanded data.
      if (priority === "critical") {
        this.#protected.delete(key);
        this.#protected.set(key, protectedContent);
      }
      return protectedContent;
    }
    const content = this.#probation.get(key);
    if (!content) return undefined;
    this.#probation.delete(key);
    if (priority === "critical") this.#protect(key, content);
    else this.#probation.set(key, content);
    return content;
  }

  put(key: string, content: ArrayBuffer, priority: "critical" | "prefetch") {
    if (content.byteLength > this.#maximumBytes) return false;
    // A large prediction must not evict demand, nor empty probation then fail.
    if (priority === "prefetch" &&
        content.byteLength > this.#maximumBytes - this.#protectedBytes) return false;
    this.#remove(key);
    if (priority === "critical") this.#protect(key, content);
    else this.#probation.set(key, content);
    this.#bytes += content.byteLength;
    while (this.#bytes > this.#maximumBytes) {
      const oldest = this.#probation.keys().next().value;
      if (oldest === undefined) throw new Error("GSTile cache accounting invariant failed");
      this.#remove(oldest);
    }
    return this.has(key);
  }

  #protect(key: string, content: ArrayBuffer) {
    this.#protected.set(key, content);
    this.#protectedBytes += content.byteLength;
    while (this.#protectedBytes > this.#maximumProtectedBytes) {
      const oldest = this.#protected.entries().next().value!;
      this.#protected.delete(oldest[0]);
      this.#protectedBytes -= oldest[1].byteLength;
      this.#probation.set(oldest[0], oldest[1]);
    }
  }

  #remove(key: string) {
    const protectedContent = this.#protected.get(key);
    const content = protectedContent ?? this.#probation.get(key);
    if (!content) return;
    this.#protected.delete(key);
    this.#probation.delete(key);
    if (protectedContent) this.#protectedBytes -= content.byteLength;
    this.#bytes -= content.byteLength;
    this.#onEvict(key);
  }
}
