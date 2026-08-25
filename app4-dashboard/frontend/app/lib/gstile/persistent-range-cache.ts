const DATABASE_NAME = "droneai-gstile-cache";
const DATABASE_VERSION = 1;
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

const openDatabase = () =>
  new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
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

  constructor(maximumBytes = DEFAULT_PERSISTENT_GSTILE_CACHE_BYTES) {
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) {
      throw new Error("Persistent GSTile cache size must be a positive integer");
    }
    this.#maximumBytes = maximumBytes;
    this.#database = openDatabase();
  }

  async read(
    key: string,
    expectedByteLength: number,
    signal?: AbortSignal,
  ) {
    signal?.throwIfAborted();
    const database = await this.#database;
    signal?.throwIfAborted();
    const transaction = database.transaction(RANGE_STORE, "readonly");
    const record = await requestResult(
      transaction.objectStore(RANGE_STORE).get(key) as IDBRequest<
        RangeRecord | undefined
      >,
    );
    if (!record || record.byteLength !== expectedByteLength) return null;
    const content = await record.content.arrayBuffer();
    signal?.throwIfAborted();
    if (content.byteLength !== expectedByteLength) {
      void this.#delete(key);
      return null;
    }
    void this.#touch(key, expectedByteLength);
    return content;
  }

  write(key: string, content: ArrayBuffer) {
    if (content.byteLength > this.#maximumBytes) return Promise.resolve();
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

  async #delete(key: string) {
    try {
      const database = await this.#database;
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

export const createGsTilePersistentCache = (): GsTilePersistentCache | null =>
  typeof indexedDB === "undefined"
    ? null
    : new IndexedDbGsTilePersistentCache();
