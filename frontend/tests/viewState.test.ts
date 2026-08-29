import {
  APP_VIEW_STORAGE_KEY,
  APP_VIEWS,
  appViewHash,
  isAppView,
  normalizeHashView,
  readStoredAppView,
} from "../src/viewState";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

function makeStorage(value: string | null): Storage {
  return {
    getItem: (key: string) => (key === APP_VIEW_STORAGE_KEY ? value : null),
    setItem: () => undefined,
    removeItem: () => undefined,
    clear: () => undefined,
    key: () => null,
    length: value == null ? 0 : 1,
  };
}

assert(APP_VIEWS.length === 3, "APP_VIEWS should list all Prosper dashboard views");
assert(APP_VIEW_STORAGE_KEY === "prosper_active_view", "view storage should use Prosper naming");
assert(isAppView("inbox"), "inbox should be a valid app view");
assert(isAppView("properties"), "properties should be a valid app view");
assert(isAppView("simulator"), "simulator should be a valid app view");
assert(!isAppView("fake_chat"), "old fake_chat view should not be a current app view");
assert(!isAppView("prompts"), "old prompts view should not be a current app view");
assert(!isAppView("playbook"), "playbook should be hidden from the primary nav");
assert(!isAppView("settings"), "unknown values should not be valid app views");
assert(!isAppView(null), "null should not be a valid app view");

assert(normalizeHashView("#properties") === "properties", "hash with # should normalize to a valid view");
assert(normalizeHashView("#fake_chat") === "simulator", "old fake chat hash should redirect to simulator");
assert(normalizeHashView("prompts") === "properties", "old prompts hash should redirect to properties");
assert(normalizeHashView("playbook") === "properties", "old playbook hash should redirect to properties");
assert(normalizeHashView("#unknown") === null, "unknown hash should normalize to null");
assert(normalizeHashView("") === null, "empty hash should normalize to null");

assert(appViewHash("inbox") === "inbox", "normal views should publish their own hash");

assert(readStoredAppView(makeStorage("playbook")) === null, "hidden stored playbook view should be ignored");
assert(readStoredAppView(makeStorage("bad-view")) === null, "invalid stored view should return null");
assert(readStoredAppView(makeStorage(null)) === null, "missing stored view should return null");

console.log("viewState tests passed");
