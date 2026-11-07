import { test, expect } from './fixtures'

test('model filter and new controls fit the mobile viewport', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('RTX 3060 · 12GB').first()).toBeVisible()
  await page.getByRole('button', { name: /ตัวกรอง/ }).first().click()
  const sheet = page.getByRole('dialog')
  await expect(sheet.getByRole('option', { name: 'RTX 3060 (2)' })).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360)
  for (const label of ['รุ่น', 'หน่วยความจำ', 'สภาพ', 'ประกัน']) {
    const control = sheet.getByLabel(label, { exact: true })
    expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  }
  await sheet.getByLabel('รุ่น', { exact: true }).selectOption('rtx-3060')
  await sheet.getByRole('button', { name: 'แสดงผล' }).click()
  await expect(page.locator('.post-card-link')).toHaveCount(2)
  await expect(page.locator('.active-filters').getByText('RTX 3060', { exact: true })).toBeVisible()
  for (const card of await page.locator('.post-card-link').all()) {
    await expect(card.getByText(/RTX 3060/).first()).toBeVisible()
  }
})
