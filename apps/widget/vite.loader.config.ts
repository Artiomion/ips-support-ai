import { defineConfig } from "vite";
import { resolve } from "node:path";
export default defineConfig({ build: { outDir: "dist", emptyOutDir: false, lib: { entry: resolve(import.meta.dirname, "src/loader.ts"), name: "IpsWidget", formats: ["iife"], fileName: () => "loader.js" } } });
