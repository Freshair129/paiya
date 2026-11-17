// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'

afterEach(() => vi.unstubAllGlobals())

test.each([
  ['owner', 'บัญชีเจ้าของ'],
  ['fixture', 'ข้อมูลสังเคราะห์'],
  [undefined, 'โหมดข้อมูลยังไม่ระบุ'],
] as const)('shows the verified %s read context and the returned post', async (mode, expected) => {
  window.history.replaceState(null, '', '/')
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const item = { item_id: 'i', source_item_key: 'i', model: 'Synthetic RTX 3060', sale_status: 'open', status_since: null, needs_review: false, ask: null }
  const post = { post_id: 'p', group_id: 'g', permalink: 'https://example.invalid', posted_at: null, last_success_at: null, has_unresolved_items: false, watched: false, thumbnail_evidence_id: null, items: [item] }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.startsWith('/v1/groups') ? { groups: [{ group_id: 'g', poll_minutes: 60 }], read_context: mode }
      : url.startsWith('/v1/models') ? { models: [] } : { posts: [post], next_cursor: null },
  ), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  await act(async () => { root.render(<App />); await new Promise((resolve) => setTimeout(resolve, 0)) })
  expect(container.textContent).toContain(expected)
  expect(container.textContent).toContain('Synthetic RTX 3060')
  if (mode === 'owner') expect(container.textContent).not.toContain('ข้อมูลสังเคราะห์')
  await act(async () => root.unmount())
  container.remove()
})
