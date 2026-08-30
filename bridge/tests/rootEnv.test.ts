import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const configUrl = pathToFileURL(path.resolve(__dirname, "../src/config.ts")).href;
const tsxBin = path.resolve(__dirname, "../node_modules/.bin/tsx");
const tempRoot = mkdtempSync(path.join(tmpdir(), "prosper-root-env-"));

try {
  mkdirSync(path.join(tempRoot, "bridge"));
  writeFileSync(
    path.join(tempRoot, ".env"),
    [
      "PROSPER_TRIAGE_BURST_WAIT_MS=4567",
      "PROSPER_ACTIVE_BURST_WAIT_MS=5678",
      "PROSPER_BRIDGE_TOKEN=root-env-token",
      "",
    ].join("\n"),
  );

  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("PROSPER_") || key.startsWith("WHATSAPP_PA_")) {
      delete env[key];
    }
  }

  const script = `
    import assert from "node:assert/strict";
    import(${JSON.stringify(configUrl)}).then((config) => {
      assert.equal(config.WHATSAPP_TRIAGE_BURST_WAIT_MS, 4567);
      assert.equal(config.WHATSAPP_ACTIVE_BURST_WAIT_MS, 5678);
      assert.equal(config.WHATSAPP_BRIDGE_TOKEN, "root-env-token");
    });
  `;

  const result = spawnSync(tsxBin, ["--eval", script], {
    cwd: path.join(tempRoot, "bridge"),
    env,
    encoding: "utf8",
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
} finally {
  rmSync(tempRoot, { recursive: true, force: true });
}

console.log("bridge root env tests passed");
