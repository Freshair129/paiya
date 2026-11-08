// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import type { SystemHealth } from './api'
import { formatBangkokTime } from './labels'

let root: Root
let container: HTMLDivElement
let report: SystemHealth
let fetchMock: ReturnType<typeof vi.fn>

const emptyReport = (): SystemHealth => ({
  checked_at: '2026-11-09T12:00:00Z',
  queue: {
    discover: { overdue_count: 0, oldest_overdue_age_minutes: null },
    revisit: { overdue_count: 0, oldest_overdue_age_minutes: null },
    manual_import: { overdue_count: 0, oldest_overdue_age_minutes: null },
    enrich: { overdue_count: 0, oldest_overdue_age_minutes: null },
    purge: { overdue_count: 0, oldest_overdue_age_minutes: null },
  },
  coverage: [],
  media: { captured: 0, capture_failed: 0, success_rate: null, disk_low: 0 },
  disk: {
    media: { free_bytes: 8_000_000_000, total_bytes: 20_000_000_000, min_free_bytes: 5_368_709_120, state: 'ok' },
  },
  backup: {
    last_success_at: null,
    age_hours: null,
    state: 'never',
    last_failure_at: null,
    last_failure_reason: null,
  },
  ai_backlog: {
    states: { queued: 0, leased: 0, succeeded: 0, failed: 0, cancelled: 0 },
    oldest_queued_at: null,
    oldest_queued_age_seconds: null,
  },
})

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  window.history.replaceState(null, '', '/admin/health')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  report = emptyReport()
  fetchMock = vi.fn(async () => new Response(JSON.stringify(report), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

async function render() {
  await act(async () => {
    root.render(<App />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

test('admin health shows Thai warnings for low disk, overdue work, and stale backup time', async () => {
  report.queue.discover = { overdue_count: 2, oldest_overdue_age_minutes: 42 }
  report.disk.media.state = 'low'
  report.backup = {
    last_success_at: '2026-11-08T07:15:00Z',
    age_hours: 28.75,
    state: 'stale',
    last_failure_at: null,
    last_failure_reason: null,
  }
  await render()

  expect(fetchMock).toHaveBeenCalledWith('/v1/admin/health', expect.objectContaining({ method: 'GET' }))
  expect(container.querySelector('h1')?.textContent).toBe('สุขภาพระบบ')
  expect(container.textContent).toContain('พื้นที่ว่างต่ำกว่าเกณฑ์ที่ตั้งไว้')
  expect(container.textContent).toContain('งานที่ค้างนานที่สุด 42 นาที')
  expect(container.textContent).toContain('ข้อมูลสำรองเก่ากว่า 26 ชั่วโมง')
  expect(container.textContent).toContain(formatBangkokTime(report.backup.last_success_at))
  expect(container.querySelectorAll('[role="alert"]').length).toBeGreaterThanOrEqual(3)
})

test('admin health warns when backup has never succeeded and handles no media sample', async () => {
  await render()

  expect(container.textContent).toContain('ยังไม่เคยสำรองข้อมูล')
  expect(container.textContent).toContain('ไม่มีข้อมูลภาพ')
  expect(container.textContent).not.toContain('0.0%')
})
