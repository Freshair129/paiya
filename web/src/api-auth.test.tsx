// @vitest-environment jsdom

import { afterEach, expect, test, vi } from 'vitest'
import { getGroups, writeJson } from './api'

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

test('writes fetch a synchronizer token and send it in X-CSRF-Token', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(jsonResponse({ csrf_token: 'generated-csrf-token' }))
    .mockResolvedValueOnce(jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)

  await writeJson('/v1/sample', 'POST', { synthetic: true })

  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[0][0]).toBe('/v1/auth/csrf')
  expect(fetchMock.mock.calls[1][0]).toBe('/v1/sample')
  expect(fetchMock.mock.calls[1][1]).toMatchObject({
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'generated-csrf-token',
    },
  })
})

test('a session-required 401 notifies the app to show the unlock screen', async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(
    jsonResponse({ detail: { code: 'session_required', message: 'A valid session is required' } }, 401),
  )
  vi.stubGlobal('fetch', fetchMock)
  const required = vi.fn()
  window.addEventListener('paiya:session-required', required)

  await expect(getGroups()).rejects.toMatchObject({ status: 401, code: 'session_required' })

  expect(required).toHaveBeenCalledTimes(1)
  window.removeEventListener('paiya:session-required', required)
})
