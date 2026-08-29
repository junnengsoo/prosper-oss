export type AppView = "inbox" | "properties" | "simulator";

export const APP_VIEWS: AppView[] = ["inbox", "properties", "simulator"];

export function isAppView(value: string | null): value is AppView {
  return APP_VIEWS.includes(value as AppView);
}

export function normalizeHashView(hash: string): AppView | null {
  const value = hash.startsWith("#") ? hash.slice(1) : hash;
  return isAppView(value) ? value : null;
}

export function appViewHash(view: AppView): string {
  return view;
}
