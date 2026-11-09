export type SaleStatus = 'unknown' | 'open' | 'reserved' | 'sold' | 'withdrawn'
export type ItemCondition = 'new' | 'new_no_warranty' | 'used'
export type WarrantyStatus = 'active' | 'none'

export interface ModelOption {
  model_key: string
  item_count: number
}

export interface ModelsResponse {
  models: ModelOption[]
}

export interface AskPrice {
  amount: string
  currency: string
  unit: string
  evidence_id?: string
}

export interface PostCardItem {
  item_id: string | null
  source_item_key: string | null
  model: string | null
  model_key?: string | null
  vram_gb?: number | null
  condition?: ItemCondition | null
  warranty?: WarrantyStatus | null
  warranty_until?: string | null
  warranty_months_left?: number | null
  sale_status: SaleStatus
  status_since: string | null
  needs_review: boolean
  ask: AskPrice | null
}

export interface PostCard {
  post_id: string
  group_id: string
  permalink: string
  posted_at: string | null
  last_success_at: string | null
  has_unresolved_items: boolean
  watched: boolean
  thumbnail_evidence_id: string | null
  items: PostCardItem[]
}

export interface PostsResponse {
  posts: PostCard[]
  next_cursor: string | null
}

export interface GroupRun {
  run_id: string
  job_kind: string
  scheduled_for: string
  started_at: string
  ended_at: string | null
  coverage: 'complete' | 'partial' | 'none' | null
  partial_reasons: string[]
  stop_reason: string | null
  cards_seen: number | null
  cards_parsed: number | null
  session_result: string | null
  sort_result: string | null
  error_code: string | null
}

export interface CaptureGap {
  gap_from: string
  gap_to: string
  reason: string
}

export interface GroupRunsResponse {
  group_id: string
  runs: GroupRun[]
  gaps: CaptureGap[]
  next_cursor: string | null
}

export interface Group {
  group_id: string
  group_key: string
  enabled: boolean
  poll_minutes: number
  last_success_at: string | null
  last_run: GroupRun | null
}

export interface GroupsResponse {
  groups: Group[]
}

export interface PostObservationItem {
  item_id: string | null
  source_item_key: string | null
  model: string | null
  model_key?: string | null
  vram_gb?: number | null
  condition?: ItemCondition | null
  warranty?: WarrantyStatus | null
  warranty_until?: string | null
  warranty_months_left?: number | null
  condition_text: string | null
  source_status_label: string | null
  price: AskPrice | null
  evidence_ids: string[]
}

export interface PostObservation {
  id: string
  run_id: string | null
  capture_id: string | null
  observed_at: string
  access: string
  activity: {
    source_reported_comment_count: number | null
    distinct_comments_seen: number | null
    distinct_known_commenters_seen: number | null
    coverage: string | null
  } | null
  items: PostObservationItem[]
  evidence_ids: string[]
}

export interface PostCurrentItem {
  item_id: string
  source_item_key: string
  model_key?: string | null
  vram_gb?: number | null
  condition?: ItemCondition | null
  warranty?: WarrantyStatus | null
  warranty_until?: string | null
  warranty_months_left?: number | null
  sale_status: SaleStatus
  status_since: string | null
  status_evidence_id: string | null
  last_success_at: string | null
  needs_review: boolean
  ask: AskPrice | null
}

export interface PostDetail {
  id: string
  group_id: string
  source_post_key: string
  permalink: string
  seller_id: string | null
  posted_at: string | null
  has_unresolved_items: boolean
  current_items: PostCurrentItem[]
  observations: PostObservation[]
}

export interface TimelineEvent {
  id: string
  kind: string
  at: string
  observation_id: string
  item_id?: string | null
  from?: string | number | null
  to?: string | number | null
  evidence_id?: string | null
  role?: string | null
}

export interface TimelineResponse {
  post_id: string
  events: TimelineEvent[]
  next_cursor: string | null
}

export interface EvidenceMedia {
  mime: string
  width: number
  height: number
  bytes: number
  sha256: string
  href: string
}

export interface EvidenceDetail {
  id: string
  post_id: string
  observation_id: string
  item_id: string | null
  local_key: string
  role: string
  source_span: { field?: string; quote?: string; [key: string]: unknown } | null
  comment_version_id: string | null
  captured_at: string | null
  permalink: string
  media_state: string
  media_reason: string | null
  media: EvidenceMedia | null
}

export interface SellerPost {
  post_id: string
  permalink: string
  first_seen_at: string
  has_sold_item: boolean
}

export interface SellerDetail {
  id: string
  identity_scope: string
  display_name: string | null
  first_seen_at: string
  group_ids: string[]
  post_count: number
  sold_post_count: number
  posts: SellerPost[]
}

export interface Watch {
  post_id: string
  saved_at: string
  hot_until: string | null
  next_due_at: string | null
  last_success_at: string | null
  overdue_since: string | null
  post: PostCard
}

export interface WatchesResponse {
  watches: Watch[]
}

export type HealthJobKind = 'discover' | 'revisit' | 'manual_import' | 'enrich' | 'purge'
export type HealthBackupState = 'ok' | 'stale' | 'never'

export interface HealthJobStatus {
  overdue_count: number
  oldest_overdue_age_minutes: number | null
}

export interface HealthDiskStatus {
  free_bytes: number
  total_bytes: number
  min_free_bytes: number
  state: 'ok' | 'low'
}

export interface HealthCaptureGap {
  gap_from: string
  gap_to: string
  reason: 'missed_slots' | 'run_failed'
  detected_at: string
}

export interface HealthCoverage {
  group_id: string
  last_successful_at: string | null
  latest_coverage: 'complete' | 'partial' | 'none' | null
  capture_gap_count: number
  latest_gap_7d: HealthCaptureGap | null
}

export interface SystemHealth {
  checked_at: string
  queue: Record<HealthJobKind, HealthJobStatus>
  coverage: HealthCoverage[]
  media: {
    captured: number
    capture_failed: number
    success_rate: number | null
    disk_low: number
  }
  disk: {
    media: HealthDiskStatus
    backup?: HealthDiskStatus
  }
  backup: {
    last_success_at: string | null
    age_hours: number | null
    state: HealthBackupState
    last_failure_at: string | null
    last_failure_reason: string | null
  }
  ai_backlog: {
    states: Record<'queued' | 'leased' | 'succeeded' | 'failed' | 'cancelled', number>
    oldest_queued_at: string | null
    oldest_queued_age_seconds: number | null
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(status: number, code: string | null, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

let csrfToken: string | null = null

function notifyIfSessionRequired(status: number, code: string | null): void {
  if (status !== 401 || code !== 'session_required') return
  csrfToken = null
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('paiya:session-required'))
  }
}

async function responseError(response: Response, operation: string): Promise<ApiError> {
  let code: string | null = null
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = body.detail
      if (detail && typeof detail === 'object' && 'code' in detail) {
        code = typeof detail.code === 'string' ? detail.code : null
      }
    }
  } catch {
    // The HTTP status remains enough to choose a Thai message.
  }
  notifyIfSessionRequired(response.status, code)

  const message =
    response.status === 401
      ? 'ต้องปลดล็อกเจ้าของเครื่องเพื่ออ่านข้อมูล'
      : response.status === 403 && code === 'csrf_invalid'
        ? 'คำขอหมดอายุ กรุณาลองอีกครั้ง'
        : response.status === 403
          ? 'ไม่มีสิทธิ์ดำเนินการนี้ในขณะนี้'
          : response.status === 404
            ? 'ไม่พบรายการนี้ หรือรายการอยู่นอกกลุ่มที่เปิดอ่านได้'
            : response.status === 429
              ? 'ลองปลดล็อกอีกครั้งหลังจากรอสักครู่'
              : `${operation}ไม่สำเร็จ (HTTP ${response.status})`
  return new ApiError(response.status, code, message)
}

async function responseJson<T>(response: Response, operation: string): Promise<T> {
  if (!response.ok) throw await responseError(response, operation)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
    signal,
  })
  return responseJson<T>(response, 'โหลดข้อมูล')
}

export async function getCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken
  const body = await getJson<{ csrf_token: string }>('/v1/auth/csrf')
  csrfToken = body.csrf_token
  return csrfToken
}

export interface UnlockResponse {
  session_id: string
  csrf_token: string
}

export async function unlockOwner(passphrase: string): Promise<UnlockResponse> {
  const challengeResponse = await fetch('/v1/auth/challenge', {
    method: 'GET',
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
  })
  const challenge = await responseJson<{ nonce: string }>(challengeResponse, 'פתหน้าปลดล็อก')
  const response = await fetch('/v1/auth/unlock', {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRF-Token': challenge.nonce,
    },
    credentials: 'same-origin',
    body: JSON.stringify({ passphrase }),
  })
  const result = await responseJson<UnlockResponse>(response, 'ปลดล็อก')
  csrfToken = result.csrf_token
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('paiya:session-unlocked'))
  }
  return result
}

export type WriteMethod = 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export async function writeJson<T>(
  path: string,
  method: WriteMethod,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const token = await getCsrfToken()
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-CSRF-Token': token,
  }
  const init: RequestInit = {
    method,
    headers,
    credentials: 'same-origin',
    signal,
  }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  return responseJson<T>(await fetch(path, init), 'คำสั่ง')
}

export interface PostFilters {
  sale_status: SaleStatus | null
  min_price: string
  max_price: string
  model_key?: string | null
  vram_gb?: number | null
  condition?: ItemCondition | null
  warranty?: WarrantyStatus | null
}

export function getPosts(
  filters: PostFilters,
  cursor: string | null = null,
  signal?: AbortSignal,
): Promise<PostsResponse> {
  const query = new URLSearchParams({ limit: '20' })
  if (filters.sale_status) query.set('sale_status', filters.sale_status)
  if (filters.min_price) query.set('min_price', filters.min_price)
  if (filters.max_price) query.set('max_price', filters.max_price)
  if (filters.model_key) query.set('model_key', filters.model_key)
  if (filters.vram_gb !== undefined && filters.vram_gb !== null) {
    query.set('vram_gb', String(filters.vram_gb))
  }
  if (filters.condition) query.set('condition', filters.condition)
  if (filters.warranty) query.set('warranty', filters.warranty)
  if (cursor) query.set('cursor', cursor)
  return getJson(`/v1/posts?${query.toString()}`, signal)
}

export const getModels = (signal?: AbortSignal): Promise<ModelsResponse> =>
  getJson('/v1/models', signal)

export const getGroups = (signal?: AbortSignal): Promise<GroupsResponse> =>
  getJson('/v1/groups', signal)

export const getPost = (id: string, signal?: AbortSignal): Promise<PostDetail> =>
  getJson(`/v1/posts/${encodeURIComponent(id)}`, signal)

export function getTimeline(
  id: string,
  cursor: string | null = null,
  signal?: AbortSignal,
): Promise<TimelineResponse> {
  const query = new URLSearchParams({ limit: '50' })
  if (cursor) query.set('cursor', cursor)
  return getJson(`/v1/posts/${encodeURIComponent(id)}/timeline?${query.toString()}`, signal)
}

export const getEvidence = (id: string, signal?: AbortSignal): Promise<EvidenceDetail> =>
  getJson(`/v1/evidence/${encodeURIComponent(id)}`, signal)

export const getSeller = (id: string, signal?: AbortSignal): Promise<SellerDetail> =>
  getJson(`/v1/sellers/${encodeURIComponent(id)}`, signal)

export const getWatches = (signal?: AbortSignal): Promise<WatchesResponse> =>
  getJson('/v1/watches', signal)

export const getHealth = (signal?: AbortSignal): Promise<SystemHealth> =>
  getJson('/v1/admin/health', signal)

export function getGroupRuns(
  id: string,
  cursor: string | null = null,
  signal?: AbortSignal,
): Promise<GroupRunsResponse> {
  const query = new URLSearchParams({ limit: '20' })
  if (cursor) query.set('cursor', cursor)
  return getJson(`/v1/groups/${encodeURIComponent(id)}/runs?${query.toString()}`, signal)
}
