export type AppView = "inbox" | "properties" | "simulator";

export const APP_VIEW_STORAGE_KEY = "prosper_active_view";

export const APP_VIEWS: AppView[] = ["inbox", "properties", "simulator"];

const HASH_ALIASES: Record<string, AppView> = {
  fake_chat: "simulator",
  prompts: "properties",
  playbook: "properties",
};

export function isAppView(value: string | null): value is AppView {
  return APP_VIEWS.includes(value as AppView);
}

export function normalizeHashView(hash: string): AppView | null {
  const value = hash.startsWith("#") ? hash.slice(1) : hash;
  if (HASH_ALIASES[value]) return HASH_ALIASES[value];
  return isAppView(value) ? value : null;
}

export function appViewHash(view: AppView): string {
  return view;
}

export function readStoredAppView(storage: Storage): AppView | null {
  const stored = storage.getItem(APP_VIEW_STORAGE_KEY);
  return isAppView(stored) ? stored : null;
}
