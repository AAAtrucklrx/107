import { expect, test } from "vitest";
import { mobileDeepLinkPath, resolveMobileApiBase } from "./mobileConfig";

test.each([
  ["https://xiaowo.example/api/v1", "mobile-release", "0", true],
  ["http://127.0.0.1:8000/api/v1", "mobile-debug", "1", true],
  ["http://127.0.0.1:8000/api/v1", "mobile-release", "1", false],
  ["http://public.example/api/v1", "mobile-debug", "1", false],
  ["https://user:password@xiaowo.example/api/v1", "mobile-release", "0", false],
  ["https://xiaowo.example/api/v1?access_token=test", "mobile-release", "0", false],
] as const)("service URL transport policy: %s", (base, mode, debug, valid) => {
  if (valid) expect(resolveMobileApiBase(base, mode, debug)).toBe(base);
  else expect(() => resolveMobileApiBase(base, mode, debug)).toThrow();
});
test.each([
  ["xiaowo://app/chat", "/"], ["xiaowo://app/academic", "/academic"], ["xiaowo://app/campus", "/campus"],
  ["xiaowo://evil/academic", null], ["xiaowo://app/admin", null], ["xiaowo://app/academic?token=x", null],
  ["https://jw.ustc.edu.cn/", null], ["javascript:alert(1)", null],
])("deep links only navigate fixed application routes: %s", (value, result) => {
  expect(mobileDeepLinkPath(value!)).toBe(result);
});
