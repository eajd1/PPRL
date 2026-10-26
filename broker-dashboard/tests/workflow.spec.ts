import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve('..')
const brokerUrl = process.env.BROKER_UI_URL || 'http://127.0.0.1:18081'
const hospitalUrl = process.env.HOSPITAL_UI_URL || 'http://127.0.0.1:18080'
const credential = process.env.BROKER_UI_TOKEN || readFileSync(resolve(root, '.env.admin'), 'utf8').split('=')[1].trim()

test('hospital sends three token files and dashboard inspects links and reviews', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('pageerror', error => browserErrors.push(error.message))
  const existing = await page.request.get(`${brokerUrl}/api/v1/admin/submissions?status=processed`, {
    headers: { Authorization: `Bearer ${credential}` },
  })
  expect(existing.ok()).toBeTruthy()
  const processedBefore = (await existing.json()).total
  await page.goto(hospitalUrl)
  await page.locator('#fileInput').setInputFiles([1, 2, 3].map(i => resolve(root, `backend/dataset/hospital${i}.csv`)))
  await page.locator('#processButton').click()
  const cards = page.locator('.result-card')
  await expect(cards).toHaveCount(3)
  await expect(page.locator('.status-badge.success')).toHaveCount(3, { timeout: 30000 })
  for (let i = 0; i < 3; i++) {
    await cards.nth(i).getByRole('button', { name: 'Send to broker', exact: true }).click()
    await expect(cards.nth(i).locator('.broker-section .result-details').first()).toHaveText('Broker: processed', { timeout: 30000 })
    await expect(cards.nth(i).locator('.broker-section table')).toBeVisible()
  }
  await page.goto(brokerUrl)
  await page.getByLabel('Administrator credential').fill(credential)
  await page.getByRole('button', { name: 'Open workspace' }).click()
  await expect(page.getByRole('heading', { name: 'One patient. Connected records.' })).toBeVisible()
  await page.screenshot({ path: resolve(root, '.test-artifacts/dashboard-overview.png'), fullPage: true })
  await page.getByRole('button', { name: 'Patients', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Patient identities' })).toBeVisible()
  await page.getByRole('button', { name: /^Inspect / }).first().click()
  await expect(page.getByRole('heading', { name: 'Patient linkage' })).toBeVisible()
  await page.getByRole('button', { name: 'View version history' }).first().click()
  await expect(page.getByText('Used for matching').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /Review queue/ }).click()
  await page.getByRole('button', { name: /^Inspect / }).first().click()
  await expect(page.getByRole('heading', { name: 'Review evidence' })).toBeVisible()
  await page.screenshot({ path: resolve(root, '.test-artifacts/dashboard-review.png'), fullPage: true })
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Submissions', exact: true }).click()
  await page.getByRole('tab', { name: 'Processed', exact: true }).click()
  await expect(page.getByRole('button', { name: /^Inspect / })).toHaveCount(Math.min(processedBefore + 3, 20))
  await page.reload()
  await expect(page.getByLabel('Administrator credential')).toBeVisible()
  expect(await page.evaluate(() => localStorage.length)).toBe(0)
  expect(browserErrors).toEqual([])
})

test('login and mobile layout handle errors without overflowing', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(brokerUrl)
  await page.getByLabel('Administrator credential').fill('invalid')
  await page.getByRole('button', { name: 'Open workspace' }).click()
  await expect(page.getByText(/Unable to connect/)).toBeVisible()
  await page.getByLabel('Administrator credential').fill(credential)
  await page.getByRole('button', { name: 'Open workspace' }).click()
  await expect(page.getByRole('heading', { name: 'One patient. Connected records.' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Patients', exact: true }).click()
  await page.getByLabel('Search global or local patient ID').fill('does-not-exist')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'No records to show' })).toBeVisible()
  await page.screenshot({ path: resolve(root, '.test-artifacts/dashboard-mobile.png'), fullPage: true })
})
