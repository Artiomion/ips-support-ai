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
  projects: [
    { name: "api", grepInvert: /@browser/ },
    { name: "desktop", grep: /@browser/, use: { browserName: "chromium", viewport: { width: 1280, height: 800 } } },
    { name: "mobile", grep: /@browser/, use: { browserName: "chromium", viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: [{
    command: "uv run --frozen uvicorn ips.main:app --host 127.0.0.1 --port 8000",
    cwd: "./backend",
    url: "http://127.0.0.1:8000/health/ready",
    reuseExistingServer: false,
    timeout: 30_000,
    env: { WEB_ORIGIN: "http://127.0.0.1:5173", WIDGET_ORIGIN: "http://127.0.0.1:5174" },
  }, {
    command: "npm run dev --workspace @ips/web -- --host 127.0.0.1 --port 5173 --strictPort",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: false,
    timeout: 30_000,
    env: { VITE_API_BASE_URL: "http://127.0.0.1:8000" },
  }],
});
