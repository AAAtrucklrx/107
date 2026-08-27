import type { ChatMessage, RetrievalMode } from "../types";

const DATABASE = "xiaowo-browser-history";
const STORE = "conversations";
const VERSION = 1;
const RETENTION_MS = 30 * 24 * 60 * 60 * 1000;

export interface LocalConversation {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  mode: RetrievalMode;
  messages: ChatMessage[];
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) {
        request.result.createObjectStore(STORE, { keyPath: "conversation_id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开匿名历史。"));
  });
}

async function transact<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore, resolve: (value: T) => void, reject: (reason?: unknown) => void) => void,
): Promise<T> {
  const database = await openDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = database.transaction(STORE, mode);
      action(transaction.objectStore(STORE), resolve, reject);
      transaction.onerror = () => reject(transaction.error ?? new Error("匿名历史操作失败。"));
    });
  } finally {
    database.close();
  }
}

export async function listLocalConversations(): Promise<LocalConversation[]> {
  const all = await transact<LocalConversation[]>("readonly", (store, resolve, reject) => {
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result as LocalConversation[]);
    request.onerror = () => reject(request.error);
  });
  const cutoff = Date.now() - RETENTION_MS;
  const current = all
    .filter((item) => new Date(item.updated_at).getTime() >= cutoff)
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  if (current.length !== all.length) {
    await Promise.all(
      all
        .filter((item) => !current.includes(item))
        .map((item) => deleteLocalConversation(item.conversation_id)),
    );
  }
  return current;
}

export async function putLocalConversation(value: LocalConversation): Promise<void> {
  await transact<void>("readwrite", (store, resolve, reject) => {
    const request = store.put(value);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function deleteLocalConversation(conversationId: string): Promise<void> {
  await transact<void>("readwrite", (store, resolve, reject) => {
    const request = store.delete(conversationId);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function clearLocalConversations(): Promise<void> {
  await transact<void>("readwrite", (store, resolve, reject) => {
    const request = store.clear();
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}
