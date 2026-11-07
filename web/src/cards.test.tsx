// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import type { PostCard, SaleStatus } from './api'
import { formatBangkokTime } from './labels'

let root: Root
let container: HTMLDivElement
const item = (status: SaleStatus, review = false) => ({ item_id: status, source_item_key: status, model: status, sale_status: status, status_since: null, needs_review: review, ask: null })
const post = (statuses: ReturnType<typeof item>[]): PostCard => ({ post_id: 'p', group_id: 'g', permalink: 'https://example.invalid', posted_at: null, last_success_at: '2026-11-07T00:00:00Z', has_unresolved_items: false, watched: false, thumbnail_evidence_id: null, items: statuses })
async function render(data: PostCard, overdue: string | null = null, route = '/') {
  window.history.replaceState(null, '', route)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.startsWith('/v1/groups') ? { groups: [{ group_id: 'g', poll_minutes: 99999999 }] } : url.startsWith('/v1/watches') ? { watches: [{ post: data, last_success_at: data.last_success_at, overdue_since: overdue }] } : { posts: [data], next_cursor: null }), { status: 200 })))
  await act(async () => { root.render(<App />); await new Promise((resolve) => setTimeout(resolve, 0)) })
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })

test('multi-item summary appears only when every status is known and unconflicted', async () => {
  await render(post([item('sold'), item('open')]))
  expect(container.textContent).toContain('ขายแล้ว 1 จาก 2 รายการ')
})
test.each([['unknown', false], ['open', true]] as const)('multi-item displays individual statuses for %s review=%s', async (status, review) => {
  await render(post([item('sold'), item(status, review)]))
  expect(container.textContent).not.toContain('ขายแล้ว 1 จาก 2 รายการ')
  expect(container.textContent).toContain('ขายแล้ว')
  expect(container.textContent).toContain(review ? 'ข้อมูลขัดกัน · ต้องตรวจสอบ' : 'ยังไม่พบข้อความปิดขาย')
})
test('watch overdue_since forces stale indication even before poll interval', async () => {
  const data = post([item('open')])
  await render(data, '2026-11-07T00:15:00Z', '/watches')
  expect(container.textContent).toContain(`ไม่ได้ตรวจตั้งแต่ ${formatBangkokTime(data.last_success_at)}`)
})
test('watch without overdue_since and within poll interval is not stale', async () => {
  await render(post([item('open')]), null, '/watches')
  expect(container.textContent).not.toContain('ไม่ได้ตรวจตั้งแต่')
})
