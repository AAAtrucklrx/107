import "@testing-library/jest-dom/vitest";

Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
  configurable: true,
  value: () => undefined,
});

if (!globalThis.crypto.randomUUID) {
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    configurable: true,
    value: () => "00000000-0000-4000-8000-000000000000",
  });
}

// Radix 的 Menu/Popper（如账号菜单）会用 ResizeObserver 测尺寸，jsdom 不提供 → 补桩，
// 否则"点开账号菜单"这类测试会以 ReferenceError 挂掉（2026-09-17）。
if (!("ResizeObserver" in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: ResizeObserverStub,
  });
}
