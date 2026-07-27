import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60000, // 60s — covers 30s heartbeat interval + startup
  retries: 1,
  workers: 1,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chromium',
        launchOptions: {
          args: ['--no-sandbox', '--disable-gpu', '--disable-software-rasterizer'],
        },
      },
    },
  ],
});
