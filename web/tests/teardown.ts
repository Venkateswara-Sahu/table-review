import { spawnSync } from "node:child_process";
import path from "node:path";

export default function teardown() {
  const python = path.resolve(
    process.platform === "win32"
      ? "../.venv/Scripts/python.exe"
      : "../.venv/bin/python",
  );
  const result = spawnSync(
    python,
    [path.resolve("../scripts/browser_test_cleanup.py")],
    { timeout: 30_000, windowsHide: true, encoding: "utf-8" },
  );
  if (result.status !== 0)
    throw new Error("Disposable browser test server cleanup failed");
}
