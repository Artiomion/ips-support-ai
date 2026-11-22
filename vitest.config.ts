import { defineConfig } from "vitest/config";

export default defineConfig({ test: { environment: "jsdom", include: ["{apps,packages}/**/*.test.{ts,tsx}"] } });
