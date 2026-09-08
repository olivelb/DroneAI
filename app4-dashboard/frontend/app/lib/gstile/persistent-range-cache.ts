const DATABASE_NAME = "droneai-gstile-cache";
const DATABASE_VERSION = 1;
const openCaches = new Set<IndexedDbGsTilePersistentCache>();
const knownDatabases = new Set<string>([DATABASE_NAME]);
let persistenceEnabled = true;
let purgeTail = Promise.resolve();
const scopedDatabaseName = (identity: string) => `${DATABASE_NAME}:${encodeURIComponent(identity)}`;

export const gsTileCacheIdentity = (
  principal: { organization_id: string; subject: string; role: string } | null,
): string | null => principal
  ? JSON.stringify([principal.organization_id, principal.subject, principal.role]) : null;

/** Close writers first, then delete legacy and non-current identity databases. */
export function purgeGsTilePersistentCaches(keepIdentity: string | null = null): Promise<void> {
  const purge = purgeTail.then(() => purgeCaches(keepIdentity));
  purgeTail = purge.catch(() => undefined);
  return purge;
}

async function purgeCaches(keepIdentity: string | null): Promise<void> {
  const keep = keepIdentity === null ? null : scopedDatabaseName(keepIdentity);
  await Promise.all([...openCaches].filter(cache => cache.databaseName !== keep).map(cache => cache.close()));
  if (typeof indexedDB === "undefined") return;
  try {
    if (!indexedDB.databases) persistenceEnabled = false;
    const databases = indexedDB.databases ? await indexedDB.databases() : [];
    const names = new Set([...knownDatabases, ...databases.flatMap(database => database.name ? [database.name] : [])]);
    await Promise.all([...names].filter(name => name !== keep &&
      (name === DATABASE_NAME || name.startsWith(`${DATABASE_NAME}:`))).map(name => new Promise<void>((resolve, reject) => {
        const request = indexedDB.deleteDatabase(name);
        request.onsuccess = () => { knownDatabases.delete(name); resolve(); };
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error("Another tab blocked GSTile cache deletion"));
      })));
  } catch (error) {
    persistenceEnabled = false;
    console.warn("GSTile persistence disabled because cache cleanup failed", error);
  }
}
const RANGE_STORE = "ranges";
const ACCESS_STORE = "access";
const META_STORE = "metadata";
const TOTAL_BYTES_KEY = "totalBytes";

export const DEFAULT_PERSISTENT_GSTILE_CACHE_BYTES = 2 * 1024 * 1024 * 1024;

type RangeRecord = {
  key: string;
  content: Blob;
  byteLength: number;
};

type AccessRecord = {
  key: string;
  byteLength: number;
  lastAccessed: number;
};

type MetaRecord = {
  key: typeof TOTAL_BYTES_KEY;
  value: number;
};

export interface GsTilePersistentCache {
  read(
    key: string,
    expectedByteLength: number,
    signal?: AbortSignal,
  ): Promise<ArrayBuffer | null>;
  write(key: string, content: ArrayBuffer): Promise<void>;
  delete(key: string): Promise<void>;
  close?(): Promise<void>;
  hasMany?(
    entries: readonly { key: string; expectedByteLength: number }[],
    signal?: AbortSignal,
  ): Promise<Set<string>>;
}

const requestResult = <T>(request: IDBRequest<T>) =>
  new Promise<T>((resolve, reject) => {
    request.addEventListener("success", () => resolve(request.result), {
      once: true,
    });
    request.addEventListener(
      "error",
      () => reject(request.error ?? new Error("IndexedDB request failed")),
      { once: true },
    );
  });

const transactionDone = (transaction: IDBTransaction) =>
  new Promise<void>((resolve, reject) => {
    transaction.addEventListener("complete", () => resolve(), { once: true });
    transaction.addEventListener(
      "abort",
      () => reject(transaction.error ?? new Error("IndexedDB transaction aborted")),
      { once: true },
    );
    transaction.addEventListener(
      "error",
      () => reject(transaction.error ?? new Error("IndexedDB transaction failed")),
      { once: true },
    );
  });

const openDatabase = (name: string) =>
  new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(name, DATABASE_VERSION);
    request.addEventListener(
      "upgradeneeded",
      () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(RANGE_STORE)) {
          database.createObjectStore(RANGE_STORE, { keyPath: "key" });
        }
        if (!database.objectStoreNames.contains(ACCESS_STORE)) {
          const access = database.createObjectStore(ACCESS_STORE, {
            keyPath: "key",
          });
          access.createIndex("lastAccessed", "lastAccessed");
        }
        if (!database.objectStoreNames.contains(META_STORE)) {
          database.createObjectStore(META_STORE, { keyPath: "key" });
        }
      },
      { once: true },
    );
    request.addEventListener(
      "success",
      () => {
        const database = request.result;
        database.addEventListener("versionchange", () => database.close());
        resolve(database);
      },
      { once: true },
    );
    request.addEventListener(
      "error",
      () => reject(request.error ?? new Error("Unable to open GSTile cache")),
      { once: true },
    );
  });

export class IndexedDbGsTilePersistentCache implements GsTilePersistentCache {
  readonly #maximumBytes: number;
  readonly #database: Promise<IDBDatabase>;
  #writeTail = Promise.resolve();
  #closed = false;
  readonly databaseName: string;

  constructor(identity: string, maximumBytes = DEFAULT_PERSISTENT_GSTILE_CACHE_BYTES) {
    if (!identity) throw new Error("Persistent GSTile cache requires an identity");
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) {
      throw new Error("Persistent GSTile cache size must be a positive integer");
    }
    this.#maximumBytes = maximumBytes;
    this.databaseName = scopedDatabaseName(identity);
    knownDatabases.add(this.databaseName);
    this.#database = openDatabase(this.databaseName);
    openCaches.add(this);
  }

  async close(): Promise<void> {
    this.#closed = true;
    await this.#writeTail;
    try { (await this.#database).close(); } catch { /* Unavailable storage has no open connection. */ }
    openCaches.delete(this);
  }

  async read(
    key: string,
    expectedByteLength: number,
    signal?: AbortSignal,
  ) {
    signal?.throwIfAborted();
    if (this.#closed) return null;
    const database = await this.#database;
    signal?.throwIfAborted();
    if (this.#closed) return null;
    const transaction = database.transaction(RANGE_STORE, "readonly");
    const record = await requestResult(
      transaction.objectStore(RANGE_STORE).get(key) as IDBRequest<
        RangeRecord | undefined
      >,
    );
    if (!record || record.byteLength !== expectedByteLength) return null;
    const content = await record.content.arrayBuffer();
    signal?.throwIfAborted();
    if (this.#closed) return null;
    if (content.byteLength !== expectedByteLength) {
      void this.delete(key);
      return null;
    }
    void this.#touch(key, expectedByteLength);
    return content;
  }

  async hasMany(
    entries: readonly { key: string; expectedByteLength: number }[],
    signal?: AbortSignal,
  ) {
    signal?.throwIfAborted();
    if (entries.length === 0) return new Set<string>();
    if (this.#closed) return new Set<string>();
    const requested = [...new Map(entries.map(entry => [entry.key, entry.expectedByteLength]))];
    const database = await this.#database;
    const present = new Set<string>();
    for (let offset = 0; offset < requested.length; offset += 256) {
      signal?.throwIfAborted();
      if (this.#closed) return new Set<string>();
      const store = database.transaction(ACCESS_STORE, "readonly").objectStore(ACCESS_STORE);
      const matches = await Promise.all(requested.slice(offset, offset + 256).map(async ([key, expected]) => {
        const record = await requestResult(store.get(key) as IDBRequest<AccessRecord | undefined>);
        return record?.byteLength === expected ? key : null;
      }));
      for (const key of matches) if (key !== null) present.add(key);
    }
    signal?.throwIfAborted();
    return this.#closed ? new Set<string>() : present;
  }

  write(key: string, content: ArrayBuffer) {
    if (this.#closed || content.byteLength > this.#maximumBytes) return Promise.resolve();
    const record: RangeRecord = {
      key,
      content: new Blob([content]),
      byteLength: content.byteLength,
    };
    const write = this.#writeTail.then(async () => {
      const totalBytes = await this.#put(record);
      if (totalBytes > this.#maximumBytes) await this.#trim();
    });
    this.#writeTail = write.catch(() => undefined);
    return write;
  }

  async #put(record: RangeRecord) {
    const database = await this.#database;
    if (this.#closed) return 0;
    const transaction = database.transaction(
      [RANGE_STORE, ACCESS_STORE, META_STORE],
      "readwrite",
    );
    const done = transactionDone(transaction);
    const ranges = transaction.objectStore(RANGE_STORE);
    const metadata = transaction.objectStore(META_STORE);
    const existing = await requestResult(
      ranges.get(record.key) as IDBRequest<RangeRecord | undefined>,
    );
    const totalRecord = await requestResult(
      metadata.get(TOTAL_BYTES_KEY) as IDBRequest<MetaRecord | undefined>,
    );
    const totalBytes =
      (totalRecord?.value ?? 0) - (existing?.byteLength ?? 0) + record.byteLength;
    ranges.put(record);
    transaction.objectStore(ACCESS_STORE).put({
      key: record.key,
      byteLength: record.byteLength,
      lastAccessed: Date.now(),
    } satisfies AccessRecord);
    metadata.put({
      key: TOTAL_BYTES_KEY,
      value: totalBytes,
    } satisfies MetaRecord);
    await done;
    return totalBytes;
  }

  async #trim() {
    const database = await this.#database;
    const transaction = database.transaction(
      [RANGE_STORE, ACCESS_STORE, META_STORE],
      "readwrite",
    );
    const done = transactionDone(transaction);
    const ranges = transaction.objectStore(RANGE_STORE);
    const access = transaction.objectStore(ACCESS_STORE);
    const metadata = transaction.objectStore(META_STORE);
    const totalRecord = await requestResult(
      metadata.get(TOTAL_BYTES_KEY) as IDBRequest<MetaRecord | undefined>,
    );
    let totalBytes = totalRecord?.value ?? 0;
    const targetBytes = Math.floor(this.#maximumBytes * 0.9);
    await new Promise<void>((resolve, reject) => {
      const request = access.index("lastAccessed").openCursor();
      request.addEventListener("error", () => reject(request.error), {
        once: true,
      });
      request.addEventListener("success", () => {
        const cursor = request.result;
        if (!cursor || totalBytes <= targetBytes) {
          metadata.put({
            key: TOTAL_BYTES_KEY,
            value: Math.max(totalBytes, 0),
          } satisfies MetaRecord);
          resolve();
          return;
        }
        const record = cursor.value as AccessRecord;
        totalBytes -= record.byteLength;
        ranges.delete(record.key);
        cursor.delete();
        cursor.continue();
      });
    });
    await done;
  }

  async #touch(key: string, byteLength: number) {
    try {
      const database = await this.#database;
      if (this.#closed) return;
      const transaction = database.transaction(ACCESS_STORE, "readwrite");
      transaction.objectStore(ACCESS_STORE).put({
        key,
        byteLength,
        lastAccessed: Date.now(),
      } satisfies AccessRecord);
      await transactionDone(transaction);
    } catch {
      // Cache recency is advisory; a failed touch must never fail rendering.
    }
  }

  async delete(key: string) {
    try {
      const database = await this.#database;
      if (this.#closed) return;
      const transaction = database.transaction(
        [RANGE_STORE, ACCESS_STORE, META_STORE],
        "readwrite",
      );
      const done = transactionDone(transaction);
      const ranges = transaction.objectStore(RANGE_STORE);
      const metadata = transaction.objectStore(META_STORE);
      const existing = await requestResult(
        ranges.get(key) as IDBRequest<RangeRecord | undefined>,
      );
      const totalRecord = await requestResult(
        metadata.get(TOTAL_BYTES_KEY) as IDBRequest<MetaRecord | undefined>,
      );
      ranges.delete(key);
      transaction.objectStore(ACCESS_STORE).delete(key);
      metadata.put({
        key: TOTAL_BYTES_KEY,
        value: Math.max(
          (totalRecord?.value ?? 0) - (existing?.byteLength ?? 0),
          0,
        ),
      } satisfies MetaRecord);
      await done;
    } catch {
      // Corrupt cache eviction is best-effort; SHA verification still fails closed.
    }
  }
}

export const createGsTilePersistentCache = (identity: string | null): GsTilePersistentCache | null =>
  typeof indexedDB === "undefined" || !persistenceEnabled || identity === null
    ? null
    : new IndexedDbGsTilePersistentCache(identity);
