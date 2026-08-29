import {
  APP_VIEWS,
  appViewHash,
  isAppView,
  normalizeHashView,
} from "../src/viewState";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

assert(APP_VIEWS.length === 3, "APP_VIEWS should list all Prosper dashboard views");
assert(isAppView("inbox"), "inbox should be a valid app view");
assert(isAppView("properties"), "properties should be a valid app view");
assert(isAppView("simulator"), "simulator should be a valid app view");
assert(!isAppView("fake_chat"), "old fake_chat view should not be a current app view");
assert(!isAppView("prompts"), "old prompts view should not be a current app view");
assert(!isAppView("playbook"), "playbook should be hidden from the primary nav");
assert(!isAppView("settings"), "unknown values should not be valid app views");
assert(!isAppView(null), "null should not be a valid app view");

assert(normalizeHashView("#properties") === "properties", "hash with # should normalize to a valid view");
assert(normalizeHashView("#fake_chat") === null, "old fake chat hash should not resolve to a current view");
assert(normalizeHashView("prompts") === null, "old prompts hash should not resolve to a current view");
assert(normalizeHashView("playbook") === null, "old playbook hash should not resolve to a current view");
assert(normalizeHashView("#unknown") === null, "unknown hash should normalize to null");
assert(normalizeHashView("") === null, "empty hash should normalize to null");

assert(appViewHash("inbox") === "inbox", "normal views should publish their own hash");

console.log("viewState tests passed");
