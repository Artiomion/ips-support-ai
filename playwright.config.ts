import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "tenants.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:8000" },
  webServer: {
    command: "uv run --frozen uvicorn ips.main:app --host 127.0.0.1 --port 8000",
    cwd: "./backend",
    url: "http://127.0.0.1:8000/health/ready",
    reuseExistingServer: false,
    timeout: 30_000,
    env: { WEB_ORIGIN: "http://localhost:5173", WIDGET_ORIGIN: "http://localhost:5174" },
  },
});
