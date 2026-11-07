import type { SaleStatus } from './api'

export const THAI_LABELS = {
  saleStatus: {
    unknown: 'ยังไม่พบข้อความปิดขาย',
    open: 'เปิดขาย',
    reserved: 'ติดจอง',
    sold: 'ขายแล้ว',
    withdrawn: 'ถอนประกาศ',
  },
  multipleItems: (sold: number, total: number) => `ขายแล้ว ${sold} จาก ${total} รายการ`,
  needsReview: 'ข้อมูลขัดกัน · ต้องตรวจสอบ',
  unresolvedItems: 'มีรายการที่ยังแยกชิ้นไม่ได้',
  access: {
    unavailable: 'เข้าโพสต์ไม่ได้',
    permission_denied: 'ไม่มีสิทธิ์เข้าถึง',
    session_required: 'ต้องเข้าสู่ระบบที่ต้นทาง',
    challenge_required: 'ต้นทางขอยืนยันตัวตน',
    ok: 'อ่านโพสต์ได้',
  },
  mediaState: {
    captured: 'มีภาพหลักฐาน',
    missing_at_source: 'ต้นทางไม่มีภาพ',
    capture_failed: 'เก็บภาพไม่สำเร็จ',
    not_requested: 'ไม่ได้เก็บภาพ',
  },
  coverage: {
    complete: 'อ่านครบ',
    partial: 'อ่านได้บางส่วน',
    none: 'อ่านไม่ได้',
  },
  time: {
    posted: 'โพสต์เมื่อ',
    firstSeen: 'พบครั้งแรก',
    lastSuccess: 'ตรวจสำเร็จล่าสุด',
    stale: 'ไม่ได้ตรวจตั้งแต่',
  },
} as const

const PARTIAL_REASON_LABELS: Record<string, string> = {
  sort_unverified: 'ยืนยันลำดับโพสต์ไม่ได้',
  budget_before_overlap: 'หมดงบก่อนพบโพสต์ซ้ำ',
  feed_stopped_before_overlap: 'ฟีดหยุดก่อนพบโพสต์ซ้ำ',
  parse_failures: 'แยกข้อมูลบางรายการไม่ได้',
}

export function saleStatusLabel(status: SaleStatus): string {
  return THAI_LABELS.saleStatus[status]
}

export function accessLabel(access: string): string {
  return access in THAI_LABELS.access
    ? THAI_LABELS.access[access as keyof typeof THAI_LABELS.access]
    : 'สถานะการเข้าถึงไม่ทราบ'
}

export function coverageLabel(coverage: string | null, reasons: string[] = []): string {
  if (!coverage || !(coverage in THAI_LABELS.coverage)) return 'ยังไม่มีผลความครอบคลุม'
  const label = THAI_LABELS.coverage[coverage as keyof typeof THAI_LABELS.coverage]
  if (coverage !== 'partial' || reasons.length === 0) return label
  const translated = reasons.map((reason) => PARTIAL_REASON_LABELS[reason] ?? reason)
  return `${label} · ${translated.join(' · ')}`
}

export function mediaStateLabel(state: string, reason: string | null): string {
  if (state === 'captured') return THAI_LABELS.mediaState.captured
  const stateLabel =
    state in THAI_LABELS.mediaState
      ? THAI_LABELS.mediaState[state as keyof typeof THAI_LABELS.mediaState]
      : 'ไม่ทราบสถานะภาพ'
  return reason ? `${stateLabel} · ${reason}` : stateLabel
}

export function formatBangkokTime(value: string | null | undefined): string {
  if (!value) return 'ไม่ทราบเวลา'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'ไม่ทราบเวลา'
  return new Intl.DateTimeFormat('th-TH', {
    timeZone: 'Asia/Bangkok',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function isStale(
  lastSuccessAt: string | null | undefined,
  pollMinutes: number | null | undefined,
  now = Date.now(),
): boolean {
  if (!lastSuccessAt || !pollMinutes) return false
  const lastSuccess = new Date(lastSuccessAt).getTime()
  return !Number.isNaN(lastSuccess) && now - lastSuccess > pollMinutes * 60_000
}

export function formatAsk(amount: string, currency: string): string {
  const numeric = Number(amount)
  if (!Number.isFinite(numeric)) return `${amount} ${currency}`
  return `${new Intl.NumberFormat('th-TH', { maximumFractionDigits: 2 }).format(numeric)} ${currency}`
}
