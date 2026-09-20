import { mkdtempSync } from "node:fs";
import path from "node:path";
import { tmpdir } from "node:os";
import { defineConfig } from "@playwright/test";

const e2eDatabaseDir = mkdtempSync(path.join(tmpdir(), "intelliq-e2e-"));
const e2eDatabaseUrl = `sqlite:///${path.join(e2eDatabaseDir, "fixture.sqlite")}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    browserName: "chromium",
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  webServer: [
    {
      command: "pnpm dev --port 5174 --strictPort",
      url: "http://127.0.0.1:5174",
      env: { VITE_API_PROXY_TARGET: "http://127.0.0.1:8002" },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `DATA_MODE=fixture DATABASE_URL=${e2eDatabaseUrl} JOBS_ENABLED=false ALLOW_EXTERNAL_TEXT_PROCESSING=false SESSION_ENCRYPTION_KEY='' WEATHERAPI_API_KEY='' OPENROUTESERVICE_API_KEY='' ALLOWED_ORIGINS=http://127.0.0.1:5174 PYTHONPATH=. uv run python -m uvicorn src.app.main:app --host 127.0.0.1 --port 8002`,
      cwd: path.resolve(process.cwd(), "..", "api"),
      url: "http://127.0.0.1:8002/api/runtime",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
