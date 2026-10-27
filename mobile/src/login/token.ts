export interface SecureTokenBridge {
  get(): Promise<{ value: string | null }>;
  set(options: { value: string }): Promise<void>;
  remove(): Promise<void>;
}

/** Native storage is the only persistence destination. No browser storage fallback. */
export class MobileTokenStore {
  value: string | null = null;
  private revision = 0;
  private writes: Promise<unknown> = Promise.resolve();
  constructor(private readonly native: SecureTokenBridge) {}

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.writes.then(operation, operation);
    this.writes = next.catch(() => {});
    return next;
  }

  async restore(): Promise<string | null> {
    const revision = this.revision;
    return this.enqueue(async () => {
      try {
        const { value } = await this.native.get();
        if (revision !== this.revision) return null;
        if (value && !/^[A-Za-z0-9_-]{43}$/.test(value)) {
          await this.native.remove();
          this.value = null;
          return null;
        }
        this.value = value;
        return value;
      } catch {
        this.value = null;
        await this.native.remove();
        throw new Error("安全凭据无法恢复，请重新登录。");
      }
    });
  }

  async authenticate(value: string, current: () => boolean = () => true): Promise<boolean> {
    if (!/^[A-Za-z0-9_-]{43}$/.test(value)) throw new Error("登录凭据格式无效，请重试。");
    const revision = this.revision;
    return this.enqueue(async () => {
      if (revision !== this.revision || !current()) return false;
      try { await this.native.set({ value }); }
      catch {
        this.value = null;
        await this.native.remove();
        throw new Error("安全凭据保存失败，请重试登录。");
      }
      if (revision !== this.revision || !current()) {
        await this.native.remove();
        return false;
      }
      this.value = value;
      return true;
    });
  }

  async anonymous(value: string, current: () => boolean = () => true): Promise<boolean> {
    if (!/^[A-Za-z0-9_-]{43}$/.test(value)) throw new Error("匿名凭据格式无效，请重试。");
    const clearing = this.clear();
    const revision = this.revision;
    await clearing;
    if (revision !== this.revision || !current()) return false;
    this.value = value;
    return true;
  }

  clear(): Promise<void> {
    this.revision += 1;
    this.value = null;
    return this.enqueue(() => this.native.remove());
  }
}
