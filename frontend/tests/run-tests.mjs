import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const testDir = dirname(fileURLToPath(import.meta.url));
const tsxBin = join(testDir, "../../bridge/node_modules/.bin/tsx");
const testFiles = readdirSync(testDir)
  .filter((file) => file.endsWith(".test.ts"))
  .sort();

for (const file of testFiles) {
  const result = spawnSync(tsxBin, [join(testDir, file)], { stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

console.log(`frontend tests passed (${testFiles.length} files)`);
