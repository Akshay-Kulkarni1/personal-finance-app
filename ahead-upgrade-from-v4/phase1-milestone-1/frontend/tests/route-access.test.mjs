/** Run with Node 22 (same major version as CI), no browser dependencies. */
import test from "node:test";
import assert from "node:assert/strict";
import { authRedirect, visibleTabs, PERSONAL_PATHS } from "../lib/route-access.ts";

test("every personal navigation tab is accessible to a signed-in non-demo user", () => {
  const visible = visibleTabs(false);
  assert.deepEqual(visible.map(tab => tab.href), ["/", "/accounts", "/activity"]);
  for (const tab of visible) {
    assert.equal(authRedirect(tab.href, true, false), null, `Broken navigation: ${tab.href}`);
    assert.ok(PERSONAL_PATHS.includes(tab.href), `Personal route not allowlisted: ${tab.href}`);
  }
});

test("Activity remains accessible when entered directly or via Track manually", () => {
  assert.equal(authRedirect("/activity", true, false), null);
  assert.equal(authRedirect("/activity", true, true), null);
  assert.equal(authRedirect("/activity", false, false), "/login");
});

test("private settings remain accessible but demo-only pages stay gated", () => {
  for (const path of ["/settings/security", "/settings/privacy"]) {
    assert.equal(authRedirect(path, true, false), null);
    assert.equal(authRedirect(path, false, false), "/login");
  }
  for (const tab of visibleTabs(true).filter(tab => !tab.personal)) {
    assert.equal(authRedirect(tab.href, true, false), "/");
    assert.equal(authRedirect(tab.href, true, true), null);
  }
});

test("public authentication routes and signed-in login redirect are unchanged", () => {
  for (const path of ["/login", "/forgot-password", "/reset-password", "/verify-email"]) {
    assert.equal(authRedirect(path, false, false), null);
    assert.equal(authRedirect(path, true, false), path === "/login" ? "/" : null);
  }
  assert.equal(authRedirect("/", false, false), "/login");
});
