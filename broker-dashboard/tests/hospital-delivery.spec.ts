import { test, expect } from '@playwright/test'
import { resolve } from 'node:path'

// Use the real hospital HTML/JS; simulate only the external API boundary.
for (const failure of ['rejection', 'offline']) {
  test(`hospital delivery recovers from ${failure} and allows retry`, async ({ page }) => {
    const root = resolve('..')
    let attempts = 0
    await page.route('http://hospital.test/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/health') return route.fulfill({ json: { status: 'ok' } })
      if (path.endsWith('/broker-submission')) {
        attempts++
        if (attempts === 1) {
          if (failure === 'offline') return route.abort('failed')
          return route.fulfill({ status: 409, json: { detail: 'Regenerate this file before sending' } })
        }
        return route.fulfill({ json: { status: 'accepted' } })
      }
      if (path.endsWith('/broker-results')) return route.fulfill({ json: {
        delivery: { status: 'processed' }, items: [{ local_patient_id: '001', global_patient_id: 'synthetic-global', status: 'linked', latest_received_at: '2026-10-26T00:00:00Z' }], total: 1,
      } })
      const files: Record<string, string> = { '/': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css' }
      if (files[path]) return route.fulfill({ path: resolve(root, 'frontend', files[path]) })
      return route.fulfill({ status: 404 })
    })
    await page.goto('http://hospital.test/')
    await page.evaluate(() => {
      const card = document.createElement('div')
      document.querySelector('main')!.append(card)
      ;(window as any).addBrokerControls(card, { file_id: 'file-test' }, 'job-test')
    })
    await page.getByRole('button', { name: 'Send to broker', exact: true }).click()
    await expect(page.locator('.broker-section .error-text')).not.toBeEmpty()
    await expect(page.locator('.broker-section').getByText('Broker: failed', { exact: true })).toBeVisible()
    const retry = page.getByRole('button', { name: 'Retry broker submission' })
    await expect(retry).toBeEnabled()
    await retry.click()
    await expect(page.getByText('Broker: processed', { exact: true })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'synthetic-global' })).toBeVisible()
    await expect(page.locator('.broker-section .error-text')).toBeEmpty()
  })
}
