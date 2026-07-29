import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";
import { webcrypto } from "node:crypto";

// This jsdom exposes a localStorage global that is not a functional Storage.
// The store persists a working copy through it, so substitute an in-memory
// implementation; then isolate tests from each other's saved state.
function hasFunctionalLocalStorage(): boolean {
  try {
    return (
      typeof localStorage !== "undefined" &&
      typeof localStorage.setItem === "function" &&
      typeof localStorage.clear === "function"
    );
  } catch {
    return false;
  }
}

if (!hasFunctionalLocalStorage()) {
  const backing = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return backing.size;
    },
    clear: () => backing.clear(),
    getItem: (key) => backing.get(key) ?? null,
    key: (index) => [...backing.keys()][index] ?? null,
    removeItem: (key) => void backing.delete(key),
    setItem: (key, value) => void backing.set(key, String(value)),
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: shim,
    configurable: true,
  });
}

beforeEach(() => {
  localStorage.clear();
});

// jsdom does not provide SubtleCrypto; the packet export hashes its body with
// crypto.subtle.digest. Substitute Node's WebCrypto implementation.
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", {
    value: webcrypto,
    configurable: true,
  });
}
