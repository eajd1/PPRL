import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests', workers: 1, fullyParallel: false, timeout: 90000,
  use: { channel: 'chrome', headless: true, screenshot: 'only-on-failure' },
  reporter: 'list',
})
