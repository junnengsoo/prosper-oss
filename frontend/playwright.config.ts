import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(frontendDir, "..");
const apiPort = 18080;
const dashboardPort = 15173;
const deepseekPort = 19101;
const databasePath = path.join(rootDir, "runtime", "playwright-acceptance.sqlite3");

export default defineConfig({
  testDir: "./tests/acceptance",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: {
    timeout: 10_000,
  },
  reporter: process.env.CI ? [["dot"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${dashboardPort}`,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `node ${path.join(frontendDir, "tests", "acceptance", "fake-deepseek.mjs")}`,
      url: `http://127.0.0.1:${deepseekPort}/health`,
      timeout: 15_000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        FAKE_DEEPSEEK_PORT: String(deepseekPort),
      },
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `rm -f ${databasePath} ${databasePath}-shm ${databasePath}-wal && .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port ${apiPort} --log-level warning`,
      cwd: rootDir,
      url: `http://127.0.0.1:${apiPort}/health`,
      timeout: 30_000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        AUTH_REQUIRED: "false",
        DATABASE_URL: `sqlite:///${databasePath}`,
        DEEPSEEK_API_KEY: "playwright-test-key",
        DEEPSEEK_BASE_URL: `http://127.0.0.1:${deepseekPort}`,
        LANGFUSE_PUBLIC_KEY: "",
        LANGFUSE_SECRET_KEY: "",
        LANGFUSE_BASE_URL: "",
        LANGFUSE_HOST: "",
        SEED_PROPERTIES: "false",
        BRIDGE_BASE_URL: "http://127.0.0.1:19102",
      },
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run dev -- --port ${dashboardPort}`,
      cwd: frontendDir,
      url: `http://127.0.0.1:${dashboardPort}`,
      timeout: 30_000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        VITE_API_BASE: `http://127.0.0.1:${apiPort}`,
      },
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 980 } },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 5"], viewport: { width: 393, height: 851 } },
    },
  ],
});
