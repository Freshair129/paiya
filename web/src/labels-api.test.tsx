import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  accessLabel, coverageLabel, formatBangkokTime, isStale, mediaStateLabel,
  saleStatusLabel, THAI_LABELS,
} from './labels'
import {
  getEvidence, getGroups, getGroupRuns, getPost, getPosts, getSeller, getTimeline, getWatches,
} from './api'

describe('Thai read-path labels', () => {
  it.each([
    ['unknown', 'ยังไม่พบข้อความปิดขาย'], ['open', 'เปิดขาย'],
    ['reserved', 'ติดจอง'], ['sold', 'ขายแล้ว'], ['withdrawn', 'ถอนประกาศ'],
  ] as const)('labels sale status %s', (status, label) => {
    expect(saleStatusLabel(status)).toBe(label)
  })

  it('formats the multi-item and review labels', () => {
    expect(THAI_LABELS.multipleItems(1, 2)).toBe('ขายแล้ว 1 จาก 2 รายการ')
    expect(THAI_LABELS.needsReview).toBe('ข้อมูลขัดกัน · ต้องตรวจสอบ')
    expect(THAI_LABELS.unresolvedItems).toBe('มีรายการที่ยังแยกชิ้นไม่ได้')
  })

  it.each([
    ['unavailable', 'เข้าโพสต์ไม่ได้'], ['permission_denied', 'ไม่มีสิทธิ์เข้าถึง'],
    ['session_required', 'ต้องเข้าสู่ระบบที่ต้นทาง'], ['challenge_required', 'ต้นทางขอยืนยันตัวตน'],
  ])('labels access %s', (value, label) => expect(accessLabel(value)).toBe(label))

  it.each([
    ['captured', 'มีภาพหลักฐาน'], ['missing_at_source', 'ต้นทางไม่มีภาพ'],
    ['capture_failed', 'เก็บภาพไม่สำเร็จ'], ['not_requested', 'ไม่ได้เก็บภาพ'],
  ])('labels media %s', (value, label) => expect(mediaStateLabel(value, null)).toBe(label))

  it('includes a media failure reason', () => {
    expect(mediaStateLabel('capture_failed', 'synthetic failure')).toBe('เก็บภาพไม่สำเร็จ · synthetic failure')
  })

  it.each([['complete', 'อ่านครบ'], ['partial', 'อ่านได้บางส่วน'], ['none', 'อ่านไม่ได้']])(
    'labels coverage %s', (value, label) => expect(coverageLabel(value)).toBe(label),
  )
  it('includes partial coverage reasons', () => {
    expect(coverageLabel('partial', ['sort_unverified'])).toBe('อ่านได้บางส่วน · ยืนยันลำดับโพสต์ไม่ได้')
  })
  it('formats UTC in Bangkok across midnight', () => {
    expect(formatBangkokTime('2026-11-07T16:59:00Z')).toBe('7 พ.ย. 2569 23:59')
    expect(formatBangkokTime('2026-11-07T17:01:00Z')).toBe('8 พ.ย. 2569 00:01')
  })
  it('marks a check stale only after the polling interval', () => {
    const at = '2026-11-07T00:00:00Z'
    const start = Date.parse(at)
    expect(isStale(at, 30, start + 30 * 60_000)).toBe(false)
    expect(isStale(at, 30, start + 30 * 60_000 + 1)).toBe(true)
    expect(isStale(null, 30, start)).toBe(false)
  })
})

describe('API client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses relative v1 URLs for every endpoint and encodes identifiers', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await Promise.all([
      getPosts({ sale_status: 'sold', min_price: '1', max_price: '2' }, 'cursor'),
      getGroups(), getPost('a/b'), getTimeline('a/b', 'cursor'), getEvidence('a/b'),
      getSeller('a/b'), getWatches(), getGroupRuns('a/b', 'cursor'),
    ])
    expect(fetchMock).toHaveBeenCalledTimes(8)
    for (const [url] of fetchMock.mock.calls) expect(url).toMatch(/^\/v1\//)
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain('/v1/posts/a%2Fb')
    expect(fetchMock.mock.calls[0][0]).toContain('sale_status=sold')
  })

  it.each([
    [401, 'ต้องปลดล็อกเจ้าของเครื่องเพื่ออ่านข้อมูล'],
    [403, 'ไม่มีสิทธิ์ดำเนินการนี้ในขณะนี้'],
    [404, 'ไม่พบรายการนี้ หรือรายการอยู่นอกกลุ่มที่เปิดอ่านได้'],
  ])('maps HTTP %i to a Thai error', async (status, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status })))
    await expect(getGroups()).rejects.toMatchObject({ status, message })
  })
})
