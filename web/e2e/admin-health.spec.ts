import { test, expect } from './fixtures'

test('admin health shows its Thai sections and backup warning on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await page.goto('/admin/health')
  await expect(page.getByRole('heading', { level: 1, name: 'สุขภาพระบบ' })).toBeVisible()
  for (const name of ['คิวงาน', 'รอบตรวจและช่วงขาด', 'ภาพใน 24 ชั่วโมง', 'พื้นที่จัดเก็บ', 'ข้อมูลสำรอง', 'คิวเสริมข้อมูลด้วย AI']) {
    await expect(page.getByRole('heading', { level: 2, name })).toBeVisible()
  }
  await expect(page.getByRole('alert').filter({ hasText: 'ยังไม่เคยสำรองข้อมูล' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360)
})
