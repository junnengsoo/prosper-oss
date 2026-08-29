import { api } from "../src/api";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

const calls: Array<{ url: string; init?: RequestInit }> = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  calls.push({ url: String(input), init });
  return {
    ok: true,
    json: async () => ({ authenticated: true, email: null }),
    text: async () => "",
  } as Response;
}) as typeof fetch;

await api.authLogin("demo-password");
assert(calls[0]?.url.endsWith("/api/auth/login"), "login should use the backend auth endpoint");
assert(calls[0]?.init?.credentials === "include", "login should include session credentials");
assert(calls[0]?.init?.body === JSON.stringify({ password: "demo-password" }), "login should send only the password");

await api.authSession();
assert(calls[1]?.url.endsWith("/api/auth/session"), "session checks should use the backend auth endpoint");

await api.authLogout();
assert(calls[2]?.url.endsWith("/api/auth/logout"), "logout should use the backend auth endpoint");

console.log("auth tests passed");
