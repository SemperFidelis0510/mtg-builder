import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: 'http://127.0.0.1:8000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: 'python -m src.deck_editor',
    url: 'http://127.0.0.1:8000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      MTG_DISABLE_RAG_STARTUP: '1',
      KMP_DUPLICATE_LIB_OK: 'TRUE',
      MTG_LOG_LEVEL: process.env.MTG_LOG_LEVEL || 'WARNING',
    },
  },
});

