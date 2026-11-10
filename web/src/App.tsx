import {
  createContext,
  type FormEvent,
  type MouseEvent,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ApiError,
  getEvidence,
  getHealth,
  getGroupRuns,
  getGroups,
  getModels,
  getPost,
  getPosts,
  getSeller,
  getTimeline,
  getWatches,
  commitManualImport,
  previewManualImport,
  unlockOwner,
  type CaptureGap,
  type EvidenceDetail,
  type Group,
  type GroupRun,
  type GroupRunsResponse,
  type HealthCoverage,
  type HealthDiskStatus,
  type HealthJobKind,
  type SystemHealth,
  type ItemCondition,
  type ManualImportEvidence,
  type ManualImportInput,
  type ManualImportPreview,
  type ManualImportResult,
  type ManualPriceFact,
  type ModelOption,
  type PostCard,
  type PostCurrentItem,
  type PostDetail,
  type PostFilters,
  type SaleStatus,
  type SellerDetail,
  type TimelineEvent,
  type TimelineResponse,
  type WarrantyStatus,
  type Watch,
} from './api'
import {
  accessLabel,
  coverageLabel,
  formatAsk,
  formatBangkokTime,
  isStale,
  mediaStateLabel,
  conditionLabel,
  modelLabel,
  saleStatusLabel,
  THAI_LABELS,
  warrantyLabel,
} from './labels'

type Route =
  | { name: 'feed' }
  | { name: 'admin-health' }
  | { name: 'post'; id: string }
  | { name: 'evidence'; id: string }
  | { name: 'seller'; id: string }
  | { name: 'watches' }
  | { name: 'groups' }
  | { name: 'imports' }
  | { name: 'not-found' }

interface RouterValue {
  navigate: (path: string) => void
}

const RouterContext = createContext<RouterValue | null>(null)

function parseRoute(path: string): Route {
  if (path === '/') return { name: 'feed' }
  if (path === '/admin/health') return { name: 'admin-health' }
  if (path === '/watches') return { name: 'watches' }
  if (path === '/groups') return { name: 'groups' }
  if (path === '/imports') return { name: 'imports' }
  const match = path.match(/^\/(posts|evidence|sellers)\/([^/]+)$/)
  if (!match) return { name: 'not-found' }
  let id: string
  try {
    id = decodeURIComponent(match[2])
  } catch {
    return { name: 'not-found' }
  }
  if (match[1] === 'posts') return { name: 'post', id }
  if (match[1] === 'evidence') return { name: 'evidence', id }
  return { name: 'seller', id }
}

function RouteLink({
  to,
  children,
  className,
  ariaLabel,
  current,
}: {
  to: string
  children: ReactNode
  className?: string
  ariaLabel?: string
  current?: boolean
}) {
  const router = useContext(RouterContext)
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return
    }
    event.preventDefault()
    router?.navigate(to)
  }
  return (
    <a
      href={to}
      onClick={handleClick}
      className={className}
      aria-label={ariaLabel}
      aria-current={current ? 'page' : undefined}
    >
      {children}
    </a>
  )
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error && error.name === 'AbortError') return ''
  return 'เชื่อมต่อข้อมูลไม่ได้ กรุณาลองอีกครั้ง'
}

function LoadingState({ label = 'กำลังโหลดข้อมูล…' }: { label?: string }) {
  return (
    <p className="state-message" role="status" aria-live="polite">
      {label}
    </p>
  )
}

function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="state-message state-error" role="alert">
      <p>{message}</p>
      {retry ? (
        <button className="button button-secondary" type="button" onClick={retry}>
          ลองอีกครั้ง
        </button>
      ) : null}
    </div>
  )
}

function UnlockScreen() {
  const [passphrase, setPassphrase] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await unlockOwner(passphrase)
      setPassphrase('')
    } catch (requestError) {
      setError(
        requestError instanceof ApiError && requestError.status === 401
          ? 'รหัสผ่านไม่ถูกต้อง'
          : errorMessage(requestError),
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="app-shell unlock-shell">
      <header className="app-header">
        <span className="brand-link">
          <span className="brand-mark" aria-hidden="true">ป</span>
          <span><strong>ป้ายยา</strong><small>ข้อมูลที่มีหลักฐาน</small></span>
        </span>
      </header>
      <main className="unlock-main" id="main-content">
        <section className="unlock-card" aria-labelledby="unlock-title">
          <p className="eyebrow">API ในเครื่อง</p>
          <h1 id="unlock-title">ปลดล็อกข้อมูลของคุณ</h1>
          <p>กรอกรหัสผ่านเจ้าของเครื่องเพื่ออ่านข้อมูลที่จัดเก็บไว้</p>
          <form className="unlock-form" onSubmit={submit}>
            <label htmlFor="owner-passphrase">รหัสผ่านเจ้าของเครื่อง</label>
            <input
              id="owner-passphrase"
              name="passphrase"
              type="password"
              autoComplete="current-password"
              minLength={12}
              required
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
              disabled={submitting}
            />
            {error ? <p className="unlock-error" role="alert">{error}</p> : null}
            <button className="button button-primary" type="submit" disabled={submitting || passphrase.length < 12}>
              {submitting ? 'กำลังปลดล็อก…' : 'ปลดล็อก'}
            </button>
          </form>
        </section>
      </main>
    </div>
  )
}

function formatPrice(amount: string, currency: string): string {
  return formatAsk(amount, currency)
}

function StatusLine({
  item,
  lastSuccessAt,
}: {
  item: Pick<PostCurrentItem, 'sale_status' | 'status_since' | 'needs_review'>
  lastSuccessAt: string | null
}) {
  return (
    <div className="status-and-time">
      {item.needs_review ? (
        <span className="status-pill status-review">{THAI_LABELS.needsReview}</span>
      ) : (
        <span className={`status-pill status-${item.sale_status}`}>
          {saleStatusLabel(item.sale_status)}
        </span>
      )}
      <span className="status-time">
        {THAI_LABELS.time.lastSuccess} {formatBangkokTime(lastSuccessAt)}
      </span>
    </div>
  )
}

function Thumbnail({ evidenceId }: { evidenceId: string | null }) {
  const [imageHref, setImageHref] = useState<string | null>(null)
  useEffect(() => {
    if (!evidenceId) {
      setImageHref(null)
      return
    }
    const controller = new AbortController()
    getEvidence(evidenceId, controller.signal)
      .then((evidence) => setImageHref(evidence.media?.href ?? null))
      .catch(() => setImageHref(null))
    return () => controller.abort()
  }, [evidenceId])

  return (
    <span className="thumbnail-frame" aria-hidden={imageHref ? undefined : true}>
      {imageHref ? (
        <img src={imageHref} alt="ภาพตัวอย่างสินค้า" loading="lazy" />
      ) : (
        <span className="thumbnail-placeholder">รูปสินค้า</span>
      )}
    </span>
  )
}

function PostCardView({
  post,
  pollMinutes,
  staleAt,
}: {
  post: PostCard
  pollMinutes: number | null
  staleAt?: string | null
}) {
  const itemSummaries = post.items.map((item) => ({
    item_id: item.item_id,
    source_item_key: item.source_item_key,
    model: item.model,
    modelLabel: modelLabel(item.model_key, item.vram_gb, item.model),
    conditionLabel: conditionLabel(item.condition),
    warrantyLabel: warrantyLabel(
      item.warranty,
      item.warranty_until,
      item.warranty_months_left,
    ),
    sale_status: item.sale_status,
    status_since: item.status_since,
    needs_review: item.needs_review,
    ask: item.ask,
  }))
  const allStatusesKnown =
    itemSummaries.length > 1 &&
    itemSummaries.every((item) => item.sale_status !== 'unknown' && !item.needs_review)
  const soldCount = itemSummaries.filter((item) => item.sale_status === 'sold').length
  const isOverdue = staleAt
    ? true
    : isStale(post.last_success_at, pollMinutes)
  const staleTime = staleAt ?? post.last_success_at
  const title = itemSummaries.find((item) => item.modelLabel)?.modelLabel ?? 'สินค้าในประกาศ'

  return (
    <RouteLink
      to={`/posts/${encodeURIComponent(post.post_id)}`}
      className="post-card-link"
      ariaLabel={`เปิดประกาศ${title ? ` ${title}` : ''}`}
    >
      <article className="post-card">
        <Thumbnail evidenceId={post.thumbnail_evidence_id} />
        <div className="post-card-content">
          <h2>{title}</h2>
          {post.posted_at ? (
            <p className="post-time">{THAI_LABELS.time.posted} {formatBangkokTime(post.posted_at)}</p>
          ) : null}
          <div className="post-card-items">
            {itemSummaries.map((item, index) => (
              <div className="post-card-item" key={item.item_id ?? item.source_item_key ?? index}>
                {itemSummaries.length > 1 && item.modelLabel ? (
                  <p className="model-subtitle">{item.modelLabel}</p>
                ) : null}
                {item.conditionLabel || item.warrantyLabel ? (
                  <div className="item-attributes">
                    {item.conditionLabel ? <span>{item.conditionLabel}</span> : null}
                    {item.warrantyLabel ? <span>{item.warrantyLabel}</span> : null}
                  </div>
                ) : null}
                {allStatusesKnown ? (
                  <div className="status-and-time">
                    <span className="status-pill status-sold">
                      {THAI_LABELS.multipleItems(soldCount, itemSummaries.length)}
                    </span>
                    <span className="status-time">
                      {THAI_LABELS.time.lastSuccess} {formatBangkokTime(post.last_success_at)}
                    </span>
                  </div>
                ) : (
                  <StatusLine item={item} lastSuccessAt={post.last_success_at} />
                )}
                {item.ask ? (
                  <p className="ask-price">
                    <span>{'ราคาตั้งขาย '}</span>
                    <strong>{formatPrice(item.ask.amount, item.ask.currency)}</strong>
                  </p>
                ) : (
                  <p className="ask-price ask-missing">ยังไม่มีราคาตั้งขาย</p>
                )}
              </div>
            ))}
          </div>
          {post.has_unresolved_items ? (
            <p className="inline-note">{THAI_LABELS.unresolvedItems}</p>
          ) : null}
          {isOverdue ? (
            <p className="stale-note">
              {THAI_LABELS.time.stale} {formatBangkokTime(staleTime)}
            </p>
          ) : null}
        </div>
        <span className="card-chevron" aria-hidden="true">›</span>
      </article>
    </RouteLink>
  )
}

const DEFAULT_FILTERS: PostFilters = {
  sale_status: null,
  min_price: '',
  max_price: '',
  model_key: null,
  vram_gb: null,
  condition: null,
  warranty: null,
}
const STATUS_OPTIONS: Array<{ value: SaleStatus | ''; label: string }> = [
  { value: '', label: 'ทุกสถานะ' },
  { value: 'unknown', label: saleStatusLabel('unknown') },
  { value: 'open', label: saleStatusLabel('open') },
  { value: 'reserved', label: saleStatusLabel('reserved') },
  { value: 'sold', label: saleStatusLabel('sold') },
  { value: 'withdrawn', label: saleStatusLabel('withdrawn') },
]
const VRAM_OPTIONS = [4, 6, 8, 10, 11, 12, 16, 20, 24]
const CONDITION_OPTIONS: Array<{ value: ItemCondition | ''; label: string }> = [
  { value: '', label: 'ทุกสภาพ' },
  { value: 'new', label: 'ของใหม่' },
  { value: 'new_no_warranty', label: 'ของใหม่ ไม่มีประกัน' },
  { value: 'used', label: 'มือสอง' },
]
const WARRANTY_OPTIONS: Array<{ value: WarrantyStatus | ''; label: string }> = [
  { value: '', label: 'ทุกแบบ' },
  { value: 'active', label: 'มีประกัน' },
  { value: 'none', label: 'ไม่มีประกัน' },
]

function filterSummary(filters: PostFilters): string[] {
  const summary: string[] = []
  if (filters.sale_status) summary.push(saleStatusLabel(filters.sale_status))
  if (filters.min_price) summary.push(`ตั้งแต่ ${filters.min_price} บาท`)
  if (filters.max_price) summary.push(`ไม่เกิน ${filters.max_price} บาท`)
  if (filters.model_key) {
    summary.push(modelLabel(filters.model_key, null, null) ?? filters.model_key)
  }
  if (filters.vram_gb !== undefined && filters.vram_gb !== null) {
    summary.push(`${filters.vram_gb}GB`)
  }
  const condition = conditionLabel(filters.condition)
  if (condition) summary.push(condition)
  const warranty = warrantyLabel(filters.warranty, null, null)
  if (warranty) summary.push(warranty)
  return summary
}

function FeedPage() {
  const [filters, setFilters] = useState<PostFilters>(DEFAULT_FILTERS)
  const [draft, setDraft] = useState<PostFilters>(DEFAULT_FILTERS)
  const [filterOpen, setFilterOpen] = useState(false)
  const [posts, setPosts] = useState<PostCard[]>([])
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([])
  const [modelLoadError, setModelLoadError] = useState('')
  const [groups, setGroups] = useState<Group[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [moreError, setMoreError] = useState('')
  const [filterError, setFilterError] = useState('')
  const filterTriggerRef = useRef<HTMLButtonElement | null>(null)
  const filterDialogRef = useRef<HTMLElement | null>(null)
  const filterSelectRef = useRef<HTMLSelectElement | null>(null)
  const restoreFilterFocus = useRef(false)
  const activeFilters = filterSummary(filters)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setMoreError('')
    setPosts([])
    setNextCursor(null)
    Promise.all([getPosts(filters, null, controller.signal), getGroups(controller.signal)])
      .then(([postsResponse, groupsResponse]) => {
        setPosts(postsResponse.posts)
        setNextCursor(postsResponse.next_cursor)
        setGroups(groupsResponse.groups)
      })
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [filters])

  useEffect(() => {
    const controller = new AbortController()
    getModels(controller.signal)
      .then((response) => {
        if (!Array.isArray(response.models)) {
          setModelOptions([])
          setModelLoadError('โหลดรายการรุ่นไม่สำเร็จ')
          return
        }
        setModelOptions(response.models)
        setModelLoadError('')
      })
      .catch((cause: unknown) => {
        if (errorMessage(cause)) {
          setModelOptions([])
          setModelLoadError('โหลดรายการรุ่นไม่สำเร็จ')
        }
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!filterOpen) {
      if (restoreFilterFocus.current) {
        filterTriggerRef.current?.focus()
        restoreFilterFocus.current = false
      }
      return
    }

    restoreFilterFocus.current = true
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.requestAnimationFrame(() => filterSelectRef.current?.focus())
    function keepFocusInSheet(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setFilterOpen(false)
        return
      }
      if (event.key !== 'Tab' || !filterDialogRef.current) return
      const focusable = Array.from(
        filterDialogRef.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), select:not(:disabled), input:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (!focusable.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', keepFocusInSheet)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', keepFocusInSheet)
    }
  }, [filterOpen])

  const pollMinutesByGroup = useMemo(
    () => new Map(groups.map((group) => [group.group_id, group.poll_minutes])),
    [groups],
  )

  function openFilters() {
    setDraft(filters)
    setFilterError('')
    setFilterOpen(true)
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const min = draft.min_price ? Number(draft.min_price) : null
    const max = draft.max_price ? Number(draft.max_price) : null
    if ((min !== null && (!Number.isFinite(min) || min < 0)) || (max !== null && max < 0)) {
      setFilterError('กรุณาใส่ราคาเป็นจำนวนศูนย์หรือมากกว่า')
      return
    }
    if (min !== null && max !== null && min > max) {
      setFilterError('ราคาเริ่มต้นต้องไม่มากกว่าราคาสูงสุด')
      return
    }
    setFilters(draft)
    setFilterOpen(false)
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    setMoreError('')
    try {
      const response = await getPosts(filters, nextCursor)
      setPosts((current) => [...current, ...response.posts])
      setNextCursor(response.next_cursor)
    } catch (cause) {
      setMoreError(errorMessage(cause))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <section className="page-section" aria-labelledby="feed-title">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">PAIYA · ข้อมูลสังเคราะห์</p>
          <h1 id="feed-title">สินค้า</h1>
        </div>
        <button ref={filterTriggerRef} className="button button-secondary filter-button" onClick={openFilters} type="button">
          <span aria-hidden="true">☷</span> ตัวกรอง
          {activeFilters.length ? <span className="filter-count">{activeFilters.length}</span> : null}
        </button>
      </div>

      {activeFilters.length ? (
        <div className="active-filters" aria-label="ตัวกรองที่กำลังใช้">
          {activeFilters.map((filter) => <span className="filter-chip" key={filter}>{filter}</span>)}
          <button className="text-button" type="button" onClick={() => setFilters(DEFAULT_FILTERS)}>
            ล้างตัวกรอง
          </button>
        </div>
      ) : null}

      {loading ? <LoadingState label="กำลังโหลดสินค้า…" /> : null}
      {!loading && error ? <ErrorState message={error} retry={() => setFilters({ ...filters })} /> : null}
      {!loading && !error && posts.length === 0 ? (
        <div className="empty-state">
          <span className="empty-icon" aria-hidden="true">⌕</span>
          <h2>ยังไม่พบสินค้า</h2>
          <p>{activeFilters.length ? 'ลองเปลี่ยนตัวกรองเพื่อดูรายการอื่น' : 'กลุ่มสังเคราะห์ยังไม่มีรายการที่อ่านได้'}</p>
          {activeFilters.length ? (
            <div className="empty-filter-summary">
              <p>ตัวกรองที่ใช้: {activeFilters.join(' · ')}</p>
              <button className="button button-primary" type="button" onClick={() => setFilters(DEFAULT_FILTERS)}>
                ล้างตัวกรอง
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {!loading && !error && posts.length ? (
        <div className="post-list">
          {posts.map((post) => (
            <PostCardView key={post.post_id} post={post} pollMinutes={pollMinutesByGroup.get(post.group_id) ?? null} />
          ))}
          {moreError ? <ErrorState message={moreError} retry={() => void loadMore()} /> : null}
          {nextCursor ? (
            <button className="button button-secondary load-more-button" type="button" onClick={() => void loadMore()} disabled={loadingMore}>
              {loadingMore ? 'กำลังโหลด…' : 'โหลดสินค้าเพิ่มเติม'}
            </button>
          ) : null}
        </div>
      ) : null}

      {filterOpen ? (
        <div className="sheet-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setFilterOpen(false) }}>
          <section ref={filterDialogRef} className="filter-sheet" role="dialog" aria-modal="true" aria-labelledby="filter-title">
            <div className="sheet-handle" aria-hidden="true" />
            <div className="sheet-heading">
              <h2 id="filter-title">กรองสินค้า</h2>
              <button className="icon-button" type="button" aria-label="ปิดตัวกรอง" onClick={() => setFilterOpen(false)}>×</button>
            </div>
            <form onSubmit={applyFilters}>
              <label className="field-label" htmlFor="sale-status">สถานะการขาย</label>
              <select
                ref={filterSelectRef}
                id="sale-status"
                value={draft.sale_status ?? ''}
                onChange={(event) => setDraft((value) => ({ ...value, sale_status: (event.target.value || null) as SaleStatus | null }))}
              >
                {STATUS_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
              </select>
              <div className="price-filter-row">
                <label>
                  <span className="field-label">ราคาตั้งแต่</span>
                  <input type="number" min="0" step="any" inputMode="decimal" placeholder="ไม่จำกัด" value={draft.min_price} onChange={(event) => setDraft((value) => ({ ...value, min_price: event.target.value }))} />
                </label>
                <label>
                  <span className="field-label">ถึงราคา</span>
                  <input type="number" min="0" step="any" inputMode="decimal" placeholder="ไม่จำกัด" value={draft.max_price} onChange={(event) => setDraft((value) => ({ ...value, max_price: event.target.value }))} />
                </label>
              </div>
              <label className="field-label" htmlFor="model-key">รุ่น</label>
              <select
                id="model-key"
                value={draft.model_key ?? ''}
                onChange={(event) => setDraft((value) => ({ ...value, model_key: event.target.value || null }))}
              >
                <option value="">ทุกรุ่น</option>
                {draft.model_key && !modelOptions.some((option) => option.model_key === draft.model_key) ? (
                  <option value={draft.model_key}>{modelLabel(draft.model_key, null, null) ?? draft.model_key}</option>
                ) : null}
                {modelOptions.map((option) => (
                  <option value={option.model_key} key={option.model_key}>
                    {modelLabel(option.model_key, null, null) ?? option.model_key} ({option.item_count})
                  </option>
                ))}
              </select>
              {modelLoadError ? <p className="field-error" role="status">{modelLoadError}</p> : null}
              <label className="field-label" htmlFor="vram-gb">หน่วยความจำ</label>
              <select
                id="vram-gb"
                value={draft.vram_gb ?? ''}
                onChange={(event) => setDraft((value) => ({
                  ...value,
                  vram_gb: event.target.value ? Number(event.target.value) : null,
                }))}
              >
                <option value="">ทุกขนาด</option>
                {VRAM_OPTIONS.map((value) => <option value={value} key={value}>{value} GB</option>)}
              </select>
              <label className="field-label" htmlFor="item-condition">สภาพ</label>
              <select
                id="item-condition"
                value={draft.condition ?? ''}
                onChange={(event) => setDraft((value) => ({
                  ...value,
                  condition: (event.target.value || null) as ItemCondition | null,
                }))}
              >
                {CONDITION_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
              </select>
              <label className="field-label" htmlFor="item-warranty">ประกัน</label>
              <select
                id="item-warranty"
                value={draft.warranty ?? ''}
                onChange={(event) => setDraft((value) => ({
                  ...value,
                  warranty: (event.target.value || null) as WarrantyStatus | null,
                }))}
              >
                {WARRANTY_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
              </select>
              {filterError ? <p className="field-error" role="alert">{filterError}</p> : null}
              <div className="sheet-actions">
                <button className="button button-secondary" type="button" onClick={() => { setDraft(DEFAULT_FILTERS); setFilters(DEFAULT_FILTERS); setFilterOpen(false) }}>
                  ล้างทั้งหมด
                </button>
                <button className="button button-primary" type="submit">แสดงผล</button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </section>
  )
}

function ItemDetail({ item, model }: { item: PostCurrentItem; model: string | null }) {
  const itemModel = modelLabel(item.model_key, item.vram_gb, model)
  const condition = conditionLabel(item.condition)
  const warranty = warrantyLabel(
    item.warranty,
    item.warranty_until,
    item.warranty_months_left,
  )
  return (
    <article className="item-detail">
      <div className="item-detail-heading">
        <div>
          {itemModel ? <h3>{itemModel}</h3> : null}
          {itemModel ? <p className="list-meta">{item.source_item_key}</p> : null}
        </div>
        {item.needs_review ? (
          <span className="status-pill status-review">{THAI_LABELS.needsReview}</span>
        ) : (
          <span className={`status-pill status-${item.sale_status}`}>{saleStatusLabel(item.sale_status)}</span>
        )}
      </div>
      {condition || warranty ? (
        <div className="item-attributes item-detail-attributes">
          {condition ? <span>{condition}</span> : null}
          {warranty ? <span>{warranty}</span> : null}
        </div>
      ) : null}
      <p className="detail-time">
        สถานะเมื่อ {formatBangkokTime(item.status_since ?? item.last_success_at)}
      </p>
      {item.ask ? (
        <p className="ask-price"><span>ราคาตั้งขาย </span><strong>{formatPrice(item.ask.amount, item.ask.currency)}</strong></p>
      ) : <p className="ask-price ask-missing">ยังไม่มีราคาตั้งขาย</p>}
    </article>
  )
}

function timelineDescription(event: TimelineEvent): string {
  switch (event.kind) {
    case 'status_changed':
      return `เปลี่ยนสถานะเป็น ${saleStatusLabel((event.to as SaleStatus) ?? 'unknown')}`
    case 'price_changed':
      return `เปลี่ยนราคาตั้งขายเป็น ${event.to ?? 'ไม่ระบุ'} บาท`
    case 'comment_added':
      return `พบความคิดเห็นใหม่${event.role ? ` (${event.role})` : ''}`
    case 'comment_edited':
      return 'พบการแก้ไขความคิดเห็น'
    case 'comment_count_changed':
      return `จำนวนความคิดเห็นเปลี่ยนจาก ${event.from ?? 'ไม่ทราบ'} เป็น ${event.to ?? 'ไม่ทราบ'}`
    case 'access_changed':
      return `การเข้าถึงเปลี่ยนเป็น ${accessLabel(String(event.to ?? ''))}`
    default:
      return 'พบเหตุการณ์ในประวัติ'
  }
}

function PostPage({ id }: { id: string }) {
  const [post, setPost] = useState<PostDetail | null>(null)
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    Promise.all([getPost(id, controller.signal), getTimeline(id, null, controller.signal)])
      .then(([postResponse, timelineResponse]) => {
        setPost(postResponse)
        setTimeline(timelineResponse)
      })
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [id])

  if (loading) return <LoadingState label="กำลังโหลดรายละเอียดประกาศ…" />
  if (error) return <ErrorState message={error} />
  if (!post || !timeline) return <ErrorState message="ไม่พบรายละเอียดประกาศ" />

  const evidenceRows = post.observations.flatMap((observation) =>
    observation.evidence_ids.map((evidenceId) => ({ evidenceId, observedAt: observation.observed_at })),
  )
  const latestObservationItems = post.observations.at(-1)?.items ?? []
  const modelsByItemKey = new Map(
    latestObservationItems
      .filter((item) => item.source_item_key !== null)
      .map((item) => [item.source_item_key as string, item.model]),
  )

  return (
    <section className="page-section" aria-labelledby="post-title">
      <RouteLink to="/" className="back-link">‹ กลับไปสินค้า</RouteLink>
      <p className="eyebrow">ประกาศ · {post.source_post_key}</p>
      <h1 id="post-title">รายละเอียดประกาศ</h1>
      {post.posted_at ? <p className="detail-time">{THAI_LABELS.time.posted} {formatBangkokTime(post.posted_at)}</p> : null}

      <section className="detail-section" aria-labelledby="items-title">
        <h2 id="items-title">สินค้าในประกาศ</h2>
        {post.current_items.length ? (
          <div className="item-list">
            {post.current_items.map((item) => (
              <ItemDetail item={item} model={modelsByItemKey.get(item.source_item_key) ?? null} key={item.item_id} />
            ))}
          </div>
        ) : <p className="muted-text">ยังไม่มีรายการสินค้าที่แยกชิ้นได้</p>}
        {post.has_unresolved_items ? <p className="inline-note">{THAI_LABELS.unresolvedItems}</p> : null}
      </section>

      <section className="detail-section" aria-labelledby="seller-title">
        <h2 id="seller-title">ผู้ขาย</h2>
        {post.seller_id ? (
          <RouteLink className="seller-link" to={`/sellers/${encodeURIComponent(post.seller_id)}`}>
            ดูประวัติผู้ขาย <span aria-hidden="true">›</span>
          </RouteLink>
        ) : <p className="muted-text">ยังไม่พบข้อมูลผู้ขายที่ยืนยันได้</p>}
      </section>

      <section className="detail-section" aria-labelledby="history-title">
        <h2 id="history-title">ประวัติสถานะ</h2>
        {timeline.events.length ? (
          <ol className="timeline-list">
            {timeline.events.map((event) => (
              <li className="timeline-entry" key={event.id}>
                <span className="timeline-dot" aria-hidden="true" />
                <div>
                  <p>{timelineDescription(event)}</p>
                  <time dateTime={event.at}>{formatBangkokTime(event.at)}</time>
                </div>
              </li>
            ))}
          </ol>
        ) : <p className="muted-text">ยังไม่มีเหตุการณ์ในประวัติ</p>}
      </section>

      <section className="detail-section" aria-labelledby="evidence-title">
        <h2 id="evidence-title">หลักฐาน</h2>
        {evidenceRows.length ? (
          <ul className="link-list">
            {evidenceRows.map((row, index) => (
              <li key={`${row.evidenceId}-${index}`}>
                <RouteLink to={`/evidence/${encodeURIComponent(row.evidenceId)}`}>
                  <span>เปิดหลักฐาน {index + 1}</span>
                  <span className="list-meta">พบเมื่อ {formatBangkokTime(row.observedAt)}</span>
                  <span className="card-chevron" aria-hidden="true">›</span>
                </RouteLink>
              </li>
            ))}
          </ul>
        ) : <p className="muted-text">ยังไม่มีหลักฐานในประกาศนี้</p>}
      </section>
    </section>
  )
}

function EvidencePage({ id }: { id: string }) {
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getEvidence(id, controller.signal)
      .then(setEvidence)
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [id])

  if (loading) return <LoadingState label="กำลังโหลดหลักฐาน…" />
  if (error) return <ErrorState message={error} />
  if (!evidence) return <ErrorState message="ไม่พบหลักฐานนี้" />

  return (
    <section className="page-section" aria-labelledby="evidence-page-title">
      <RouteLink to={`/posts/${encodeURIComponent(evidence.post_id)}`} className="back-link">‹ กลับไปประกาศ</RouteLink>
      <p className="eyebrow">หลักฐาน · {evidence.role}</p>
      <h1 id="evidence-page-title">ภาพและข้อความอ้างอิง</h1>
      <div className="evidence-image-frame">
        {evidence.media?.href ? (
          <img src={evidence.media.href} alt="ภาพ crop จากหลักฐานของประกาศ" />
        ) : (
          <div className="evidence-missing">
            <span className="missing-image-icon" aria-hidden="true">▧</span>
            <strong>ไม่มีภาพหลักฐานในรอบนี้</strong>
            <span>{mediaStateLabel(evidence.media_state, evidence.media_reason)}</span>
          </div>
        )}
      </div>
      <section className="detail-section evidence-quote" aria-labelledby="quote-title">
        <h2 id="quote-title">ข้อความอ้างอิง</h2>
        {evidence.source_span?.quote ? (
          <blockquote>{evidence.source_span.quote}</blockquote>
        ) : <p className="muted-text">รอบนี้ไม่มีข้อความอ้างอิง</p>}
        {evidence.source_span?.field ? <p className="list-meta">ช่องข้อมูล: {evidence.source_span.field}</p> : null}
      </section>
      <section className="detail-section" aria-labelledby="captured-title">
        <h2 id="captured-title">เวลาที่พบหลักฐาน</h2>
        <time dateTime={evidence.captured_at ?? undefined}>{formatBangkokTime(evidence.captured_at)}</time>
      </section>
      <a className="button button-primary source-link" href={evidence.permalink} target="_blank" rel="noopener noreferrer">
        เปิดโพสต์ต้นทาง <span aria-hidden="true">↗</span>
      </a>
    </section>
  )
}

function SellerPage({ id }: { id: string }) {
  const [seller, setSeller] = useState<SellerDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getSeller(id, controller.signal)
      .then(setSeller)
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [id])

  if (loading) return <LoadingState label="กำลังโหลดประวัติผู้ขาย…" />
  if (error) return <ErrorState message={error} />
  if (!seller) return <ErrorState message="ไม่พบประวัติผู้ขาย" />

  return (
    <section className="page-section" aria-labelledby="seller-page-title">
      <RouteLink to="/" className="back-link">‹ กลับไปสินค้า</RouteLink>
      <p className="eyebrow">ประวัติผู้ขาย</p>
      <h1 id="seller-page-title">{seller.display_name ?? 'ไม่ทราบชื่อผู้ขาย'}</h1>
      <p className="inline-note">ชื่อที่เห็นล่าสุด</p>
      <div className="metric-grid">
        <div><strong>{seller.post_count}</strong><span>ประกาศที่พบ</span></div>
        <div><strong>{seller.sold_post_count}</strong><span>ประกาศที่มีชิ้นขายแล้ว</span></div>
      </div>
      <p className="detail-time">{THAI_LABELS.time.firstSeen} {formatBangkokTime(seller.first_seen_at)}</p>
      <section className="detail-section" aria-labelledby="seller-posts-title">
        <h2 id="seller-posts-title">ประกาศที่พบ</h2>
        <ul className="link-list">
          {seller.posts.map((post) => (
            <li key={post.post_id}>
              <RouteLink to={`/posts/${encodeURIComponent(post.post_id)}`}>
                <span>{post.has_sold_item ? 'มีรายการขายแล้ว' : 'ดูประกาศนี้'}</span>
                <span className="list-meta">{THAI_LABELS.time.firstSeen} {formatBangkokTime(post.first_seen_at)}</span>
                <span className="card-chevron" aria-hidden="true">›</span>
              </RouteLink>
            </li>
          ))}
        </ul>
      </section>
    </section>
  )
}

function WatchCard({ watch, pollMinutes }: { watch: Watch; pollMinutes: number | null }) {
  const overdue = watch.overdue_since !== null
  const staleAt = overdue ? watch.last_success_at ?? watch.overdue_since : null
  return (
    <div className="watch-card">
      <PostCardView post={watch.post} pollMinutes={pollMinutes} staleAt={staleAt} />
    </div>
  )
}

function WatchesPage() {
  const [watches, setWatches] = useState<Watch[]>([])
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([getWatches(controller.signal), getGroups(controller.signal)])
      .then(([watchResponse, groupResponse]) => {
        setWatches(watchResponse.watches)
        setGroups(groupResponse.groups)
      })
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

  if (loading) return <LoadingState label="กำลังโหลดรายการติดตาม…" />
  if (error) return <ErrorState message={error} />
  if (!watches.length) {
    return (
      <section className="page-section" aria-labelledby="watches-title">
        <p className="eyebrow">อ่านอย่างเดียว</p>
        <h1 id="watches-title">ติดตาม</h1>
        <div className="empty-state"><h2>ยังไม่มีรายการติดตาม</h2><p>รายการติดตามจากกลุ่มสังเคราะห์จะแสดงที่นี่</p></div>
      </section>
    )
  }

  const pollMinutesByGroup = new Map(groups.map((group) => [group.group_id, group.poll_minutes]))
  return (
    <section className="page-section" aria-labelledby="watches-title">
      <div className="page-heading-row">
        <div><p className="eyebrow">อ่านอย่างเดียว</p><h1 id="watches-title">ติดตาม</h1></div>
        <span className="count-badge">{watches.length} รายการ</span>
      </div>
      <div className="post-list">
        {watches.map((watch) => (
          <WatchCard key={watch.post_id} watch={watch} pollMinutes={pollMinutesByGroup.get(watch.post.group_id) ?? null} />
        ))}
      </div>
    </section>
  )
}

function RunSummary({ run }: { run: GroupRun | null }) {
  if (!run) return <p className="muted-text">กลุ่มนี้ยังไม่มีผลรอบตรวจ</p>
  return (
    <div className="run-summary">
      <div className="run-summary-head">
        <span className={`coverage-pill coverage-${run.coverage ?? 'none'}`}>
          {coverageLabel(run.coverage, run.partial_reasons)}
        </span>
        <time dateTime={run.started_at}>{formatBangkokTime(run.started_at)}</time>
      </div>
      <p>พบการ์ด {run.cards_seen ?? '—'} · แยกข้อมูลได้ {run.cards_parsed ?? '—'}</p>
    </div>
  )
}

function GapList({ gaps }: { gaps: CaptureGap[] }) {
  return gaps.length ? (
    <ul className="gap-list">
      {gaps.map((gap, index) => (
        <li key={`${gap.gap_from}-${index}`}>
          <strong>ช่วงที่ขาดการตรวจ</strong>
          <span>{formatBangkokTime(gap.gap_from)} – {formatBangkokTime(gap.gap_to)}</span>
          <small>{gap.reason === 'missed_slots' ? 'พลาดรอบตรวจ' : 'รอบตรวจล้มเหลว'}</small>
        </li>
      ))}
    </ul>
  ) : <p className="muted-text">ไม่มีช่วงที่ขาดในผลรอบล่าสุด</p>
}

function GroupRuns({
  initial,
  onLoadMore,
  loadingMore,
  moreError,
}: {
  initial: GroupRunsResponse
  onLoadMore: () => void
  loadingMore: boolean
  moreError: string
}) {
  return (
    <div className="group-runs">
      <h3>ประวัติรอบตรวจ</h3>
      <GapList gaps={initial.gaps} />
      {initial.runs.length ? (
        <ol className="run-list">
          {initial.runs.map((run) => (
            <li key={run.run_id}>
              <RunSummary run={run} />
              <span className="list-meta">{run.job_kind === 'discover' ? 'ค้นพบโพสต์' : 'ตรวจติดตาม'}</span>
            </li>
          ))}
        </ol>
      ) : <p className="muted-text">ยังไม่มีประวัติรอบตรวจ</p>}
      {moreError ? <p className="field-error" role="alert">{moreError}</p> : null}
      {initial.next_cursor ? (
        <button className="button button-secondary" type="button" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? 'กำลังโหลด…' : 'ดูรอบเก่ากว่า'}
        </button>
      ) : null}
    </div>
  )
}

const IMPORT_IDEMPOTENCY_STORAGE_KEY = 'paiya.import.idempotency-key'

function storedImportKey(): string | null {
  try {
    return typeof window === 'undefined'
      ? null
      : window.sessionStorage.getItem(IMPORT_IDEMPOTENCY_STORAGE_KEY)
  } catch {
    return null
  }
}

function persistImportKey(value: string | null): void {
  try {
    if (typeof window === 'undefined') return
    if (value) window.sessionStorage.setItem(IMPORT_IDEMPOTENCY_STORAGE_KEY, value)
    else window.sessionStorage.removeItem(IMPORT_IDEMPOTENCY_STORAGE_KEY)
  } catch {
    // The in-memory key still protects retries for this mounted form.
  }
}

function newImportKey(): string {
  if (typeof window !== 'undefined' && window.crypto?.randomUUID) {
    return window.crypto.randomUUID()
  }
  return `import-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function importPriceLabel(kind: string): string {
  const labels: Record<string, string> = {
    ask: 'ราคาตั้งขาย',
    shipping: 'ค่าส่ง',
    deposit: 'มัดจำ',
    installment: 'ผ่อน',
    offer: 'ราคารับซื้อ',
  }
  return labels[kind] ?? 'ราคา'
}

function importAmount(value: string): string {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return value
  return new Intl.NumberFormat('th-TH', { maximumFractionDigits: 2 }).format(amount)
}

function importFactAmount(fact: ManualPriceFact): string {
  const low = importAmount(fact.amount)
  const high = fact.amount_max ? `–${importAmount(fact.amount_max)}` : ''
  const currency = fact.currency === 'THB' ? 'บาท' : fact.currency
  return `${low}${high} ${currency}`
}

function importRequestError(error: unknown): { message: string; field: string | null } {
  if (!(error instanceof ApiError)) {
    return { message: 'เชื่อมต่อข้อมูลไม่ได้ กรุณาลองอีกครั้ง', field: null }
  }
  if (error.status === 401) {
    return { message: 'ต้องปลดล็อกเจ้าของเครื่องก่อนนำเข้าข้อมูล', field: null }
  }
  if (error.status === 403) {
    return { message: 'ไม่มีสิทธิ์ดำเนินการนี้ในขณะนี้', field: null }
  }
  if (error.status === 404) {
    return { message: 'ไม่พบกลุ่มหรือสิทธิ์นำเข้าที่ใช้งานได้', field: null }
  }
  if (error.status === 409 && error.code === 'idempotency_conflict') {
    return {
      message: 'หากข้อมูลเปลี่ยน ให้ตรวจตัวอย่างใหม่และใช้คีย์การยืนยันใหม่',
      field: 'Idempotency-Key',
    }
  }
  if (error.status === 409) {
    return {
      message: 'หากข้อมูลเปลี่ยน ให้ตรวจตัวอย่างใหม่และใช้คีย์การยืนยันใหม่',
      field: 'preview_digest',
    }
  }
  if (error.status === 413) {
    return { message: 'ข้อความเกินขนาดที่กำหนด กรุณาลดขนาดแล้วลองใหม่', field: 'text' }
  }
  if (error.status === 415) {
    return { message: 'ส่งข้อมูลนำเข้าในรูปแบบที่รองรับไม่ได้', field: 'body' }
  }
  if (error.status === 422) {
    if (error.field === 'permalink' || error.code === 'invalid_permalink') {
      return { message: 'ลิงก์ระบุตัวโพสต์ไม่ได้ กรุณาใช้ permalink ตรงของโพสต์ในกลุ่ม', field: 'permalink' }
    }
    if (error.field === 'text' || error.code === 'empty_text' || error.code === 'invalid_text') {
      return { message: 'กรุณาตรวจข้อความประกาศ แล้วคงข้อความร่างไว้เพื่อแก้ไข', field: 'text' }
    }
    if (error.field === 'Idempotency-Key') {
      return { message: 'คีย์การยืนยันไม่ถูกต้อง กรุณาเริ่มรายการใหม่', field: 'Idempotency-Key' }
    }
    return { message: 'ตรวจข้อมูลนำเข้าอีกครั้ง โดยระบบคงข้อความร่างไว้', field: error.field }
  }
  return { message: 'นำเข้าข้อมูลไม่สำเร็จ กรุณาลองอีกครั้ง', field: error.field }
}

function ImportPriceFact({
  fact,
  evidence,
}: {
  fact: ManualPriceFact
  evidence: ManualImportEvidence[]
}) {
  const quote = evidence.find((entry) => entry.local_key === fact.evidence_key)?.source_span.quote
  return (
    <div className="import-fact">
      <p><strong>{importPriceLabel(fact.kind)}:</strong> {importFactAmount(fact)}</p>
      {quote ? <blockquote>{quote}</blockquote> : <p className="muted-text">ไม่มีข้อความอ้างอิง</p>}
    </div>
  )
}

function ImportPreviewCard({
  preview,
  result,
  confirming,
  onConfirm,
}: {
  preview: ManualImportPreview
  result: ManualImportResult | null
  confirming: boolean
  onConfirm: () => void
}) {
  const unresolved = new Set(preview.unresolved_item_keys)
  return (
    <section className="import-preview" aria-labelledby="import-preview-title">
      <div className="import-section-heading">
        <div>
          <p className="eyebrow">ดูตัวอย่างก่อนบันทึก</p>
          <h2 id="import-preview-title">รายการและหลักฐานที่แยกได้</h2>
        </div>
        <span className="import-source-badge">ข้อความที่ผู้ซื้อส่ง</span>
      </div>
      {preview.items.length ? (
        <div className="import-item-list">
          {preview.items.map((item) => (
            <article className="import-item-card" key={item.source_item_key}>
              <div className="import-item-heading">
                <h3>{item.model}</h3>
                {unresolved.has(item.source_item_key)
                  ? <span className="import-unresolved-badge">ยังจับคู่รายการไม่ชัดเจน</span>
                  : null}
              </div>
              {item.price_facts.length ? item.price_facts.map((fact) => (
                <ImportPriceFact
                  key={`${item.source_item_key}-${fact.evidence_key}`}
                  fact={fact}
                  evidence={preview.evidence}
                />
              )) : <p className="muted-text">ยังระบุราคาตั้งขายของรายการนี้ไม่ได้</p>}
            </article>
          ))}
        </div>
      ) : (
        <p className="muted-text">ยังแยกรุ่นสินค้าไม่ได้จากข้อความนี้</p>
      )}
      {preview.unassigned_price_facts.length ? (
        <section className="import-unresolved" aria-labelledby="import-unassigned-title">
          <h3 id="import-unassigned-title">ราคาที่ยังจับคู่กับชิ้นสินค้าไม่ได้</h3>
          {preview.unassigned_price_facts.map((fact) => (
            <ImportPriceFact
              key={`unassigned-${fact.evidence_key}`}
              fact={fact}
              evidence={preview.evidence}
            />
          ))}
        </section>
      ) : null}
      {preview.unresolved_fields.length ? (
        <section className="import-unresolved" aria-labelledby="import-fields-title">
          <h3 id="import-fields-title">ช่องที่ยังระบุไม่ได้</h3>
          <ul>
            {preview.unresolved_fields.map((field) => (
              <li key={`${field.field}-${field.source_item_key}`}>
                {field.field === 'item_identity' ? 'การแยกรายการ' : 'ราคาตั้งขาย'} · {field.source_item_key}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <p className="import-author-note" role="note">
        การที่ผู้ซื้อเห็นตัวเลข ไม่ได้พิสูจน์ว่าผู้แสดงความคิดเห็นเป็นผู้ขาย
      </p>
      {result ? (
        <div className="import-success" role="status" aria-live="polite">
          <strong>บันทึกการนำเข้าแล้ว</strong>
          <span>รหัส observation: {result.observation_id}</span>
        </div>
      ) : (
        <button className="button button-primary import-confirm" type="button" onClick={onConfirm} disabled={confirming}>
          {confirming ? 'กำลังบันทึก…' : 'ยืนยันและบันทึก'}
        </button>
      )}
    </section>
  )
}

function ImportPage() {
  const [groups, setGroups] = useState<Group[]>([])
  const [groupId, setGroupId] = useState('')
  const [permalink, setPermalink] = useState('')
  const [text, setText] = useState('')
  const [preview, setPreview] = useState<ManualImportPreview | null>(null)
  const [result, setResult] = useState<ManualImportResult | null>(null)
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(storedImportKey)
  const [groupLoading, setGroupLoading] = useState(true)
  const [groupError, setGroupError] = useState('')
  const [error, setError] = useState<{ message: string; field: string | null } | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [renewKeyOnNextPreview, setRenewKeyOnNextPreview] = useState(false)
  const [loadAttempt, setLoadAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setGroupLoading(true)
    setGroupError('')
    getGroups(controller.signal)
      .then((response) => {
        setGroups(response.groups)
        setGroupId((current) => current || response.groups[0]?.group_id || '')
      })
      .catch((cause: unknown) => {
        if (cause instanceof Error && cause.name === 'AbortError') return
        setGroupError(errorMessage(cause))
      })
      .finally(() => setGroupLoading(false))
    return () => controller.abort()
  }, [loadAttempt])

  function clearKey() {
    setIdempotencyKey(null)
    persistImportKey(null)
  }

  function updateDraft(update: () => void) {
    update()
    setPreview(null)
    setResult(null)
    setError(null)
    setRenewKeyOnNextPreview(false)
    if (idempotencyKey) clearKey()
  }

  function startNewSubmission() {
    clearKey()
    setPermalink('')
    setText('')
    setPreview(null)
    setResult(null)
    setError(null)
    setRenewKeyOnNextPreview(false)
  }

  async function requestPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setResult(null)
    if (!groupId) {
      setError({ message: 'เลือกกลุ่มที่เปิดอ่านได้ก่อนดูตัวอย่าง', field: 'group_id' })
      return
    }
    if (!permalink.trim()) {
      setPreview(null)
      setError({ message: 'กรุณาวาง permalink ตรงของโพสต์ในกลุ่ม', field: 'permalink' })
      return
    }
    if (!text.trim()) {
      setPreview(null)
      setError({ message: 'กรุณาวางข้อความประกาศ', field: 'text' })
      return
    }
    setPreviewing(true)
    setPreview(null)
    try {
      const input: ManualImportInput = { permalink, text }
      const nextPreview = await previewManualImport(groupId, input)
      if (renewKeyOnNextPreview) {
        clearKey()
        setRenewKeyOnNextPreview(false)
      }
      setPreview(nextPreview)
    } catch (cause) {
      setError(importRequestError(cause))
    } finally {
      setPreviewing(false)
    }
  }

  async function confirmImport() {
    if (!preview || !groupId || confirming) return
    setConfirming(true)
    setError(null)
    const key = idempotencyKey ?? newImportKey()
    setIdempotencyKey(key)
    persistImportKey(key)
    try {
      const input: ManualImportInput = { permalink, text }
      const saved = await commitManualImport(groupId, input, preview.preview_digest, key)
      clearKey()
      setResult(saved)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) setRenewKeyOnNextPreview(true)
      setError(importRequestError(cause))
    } finally {
      setConfirming(false)
    }
  }

  if (groupLoading) return <LoadingState label="กำลังโหลดกลุ่มสำหรับนำเข้า…" />
  if (groupError) {
    return <ErrorState message={groupError} retry={() => setLoadAttempt((attempt) => attempt + 1)} />
  }

  return (
    <section className="page-section import-page" aria-labelledby="import-title">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">นำเข้าด้วยข้อความที่ผู้ซื้อส่ง</p>
          <h1 id="import-title">นำเข้าประกาศ</h1>
        </div>
        {idempotencyKey || permalink || text || result ? (
          <button className="button button-secondary import-reset" type="button" onClick={startNewSubmission}>
            เริ่มรายการใหม่
          </button>
        ) : null}
      </div>
      {!groups.length ? (
        <div className="empty-state">
          <h2>ยังไม่มีกลุ่มที่เปิดอ่านได้</h2>
          <p>กลุ่มที่มีสิทธิ์อ่านจะแสดงที่นี่</p>
        </div>
      ) : (
        <form className="import-form" onSubmit={(event) => void requestPreview(event)}>
          <label className="import-field" htmlFor="import-group">
            <span>กลุ่ม</span>
            <select
              id="import-group"
              value={groupId}
              onChange={(event) => updateDraft(() => setGroupId(event.target.value))}
              disabled={previewing || confirming}
              aria-invalid={error?.field === 'group_id'}
            >
              {groups.map((group) => (
                <option key={group.group_id} value={group.group_id}>{group.group_key}</option>
              ))}
            </select>
          </label>
          <label className="import-field" htmlFor="import-permalink">
            <span>Permalink ตรงของโพสต์</span>
            <input
              id="import-permalink"
              type="url"
              inputMode="url"
              autoComplete="url"
              placeholder="https://www.facebook.com/groups/.../posts/.../"
              value={permalink}
              onChange={(event) => updateDraft(() => setPermalink(event.target.value))}
              disabled={previewing || confirming}
              aria-invalid={error?.field === 'permalink'}
              aria-describedby={error?.field === 'permalink' ? 'import-error' : undefined}
              required
            />
          </label>
          <label className="import-field" htmlFor="import-text">
            <span>ข้อความประกาศที่คัดลอกมา</span>
            <textarea
              id="import-text"
              rows={7}
              value={text}
              onChange={(event) => updateDraft(() => setText(event.target.value))}
              disabled={previewing || confirming}
              aria-invalid={error?.field === 'text'}
              aria-describedby={error?.field === 'text' ? 'import-error' : undefined}
              required
            />
          </label>
          <p className="import-local-note">ระบบแยกลิงก์บนเครื่องและไม่เปิดหน้าโพสต์ต้นทาง</p>
          {error ? <p id="import-error" className="field-error" role="alert">{error.message}</p> : null}
          <div className="import-actions">
            <button className="button button-primary" type="submit" disabled={previewing || confirming}>
              {previewing ? 'กำลังตรวจตัวอย่าง…' : 'ดูตัวอย่าง'}
            </button>
          </div>
        </form>
      )}
      {preview ? (
        <ImportPreviewCard
          preview={preview}
          result={result}
          confirming={confirming}
          onConfirm={() => void confirmImport()}
        />
      ) : null}
    </section>
  )
}

function GroupsPage() {
  const [groups, setGroups] = useState<Group[]>([])
  const [runs, setRuns] = useState<Record<string, GroupRunsResponse>>({})
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [moreError, setMoreError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    getGroups(controller.signal)
      .then(async (groupResponse) => {
        const runResponses = await Promise.all(
          groupResponse.groups.map((group) => getGroupRuns(group.group_id, null, controller.signal)),
        )
        setGroups(groupResponse.groups)
        setRuns(Object.fromEntries(runResponses.map((response) => [response.group_id, response])))
      })
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

  async function loadMore(groupId: string) {
    const current = runs[groupId]
    if (!current?.next_cursor || loadingMore) return
    setLoadingMore(true)
    setMoreError('')
    try {
      const next = await getGroupRuns(groupId, current.next_cursor)
      setRuns((value) => ({
        ...value,
        [groupId]: {
          ...next,
          runs: [...current.runs, ...next.runs],
          gaps: [...current.gaps, ...next.gaps],
        },
      }))
    } catch (cause) {
      setMoreError(errorMessage(cause))
    } finally {
      setLoadingMore(false)
    }
  }

  if (loading) return <LoadingState label="กำลังโหลดข้อมูลกลุ่ม…" />
  if (error) return <ErrorState message={error} />
  return (
    <section className="page-section" aria-labelledby="groups-title">
      <p className="eyebrow">ภาพรวมการอ่านข้อมูล</p>
      <h1 id="groups-title">กลุ่ม</h1>
      {!groups.length ? (
        <div className="empty-state"><h2>ยังไม่มีกลุ่ม</h2><p>กลุ่มที่เปิดอ่านได้จะแสดงที่นี่</p></div>
      ) : (
        <div className="group-list">
          {groups.map((group) => {
            const groupRuns = runs[group.group_id]
            const isExpanded = expandedGroup === group.group_id
            return (
              <article className="group-card" key={group.group_id}>
                <div className="group-card-heading">
                  <div>
                    <p className="eyebrow">{group.enabled ? 'เปิดใช้งาน' : 'พักการตรวจ'}</p>
                    <h2>{group.group_key}</h2>
                  </div>
                  <span className="poll-badge">ทุก {group.poll_minutes} นาที</span>
                </div>
                <p className="detail-time">
                  {group.last_success_at
                    ? `${THAI_LABELS.time.lastSuccess} ${formatBangkokTime(group.last_success_at)}`
                    : 'ยังไม่เคยตรวจสำเร็จ'}
                </p>
                <RunSummary run={group.last_run} />
                {groupRuns ? <GapList gaps={groupRuns.gaps.slice(0, 3)} /> : null}
                <button
                  className="text-button group-runs-toggle"
                  type="button"
                  aria-expanded={isExpanded}
                  onClick={() => setExpandedGroup(isExpanded ? null : group.group_id)}
                >
                  {isExpanded ? 'ซ่อนประวัติรอบตรวจ' : 'ดูรอบตรวจทั้งหมด'}
                </button>
                {isExpanded && groupRuns ? (
                  <GroupRuns
                    initial={groupRuns}
                    onLoadMore={() => void loadMore(group.group_id)}
                    loadingMore={loadingMore}
                    moreError={moreError}
                  />
                ) : null}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

const HEALTH_JOB_LABELS: Record<HealthJobKind, string> = {
  discover: 'ค้นหารอบใหม่',
  revisit: 'กลับไปตรวจประกาศ',
  manual_import: 'นำเข้าด้วยมือ',
  enrich: 'เสริมข้อมูลด้วย AI',
  purge: 'ลบข้อมูลตามคำขอ',
}
const HEALTH_COVERAGE_LABELS = {
  complete: 'อ่านครบ',
  partial: 'อ่านได้บางส่วน',
  none: 'อ่านไม่ได้',
} as const
const HEALTH_ENRICH_LABELS: Record<keyof SystemHealth['ai_backlog']['states'], string> = {
  queued: 'รอทำงาน',
  leased: 'กำลังทำงาน',
  succeeded: 'สำเร็จ',
  failed: 'ล้มเหลว',
  cancelled: 'ยกเลิก',
}

function formatHealthBytes(value: number): string {
  const gib = value / 1024 ** 3
  return `${new Intl.NumberFormat('th-TH', { maximumFractionDigits: 2 }).format(gib)} GiB`
}

function HealthWarning({ children }: { children: ReactNode }) {
  return <p className="health-warning" role="alert">{children}</p>
}

function HealthDiskCard({ title, disk }: { title: string; disk: HealthDiskStatus }) {
  return (
    <article className="health-card">
      <h3>{title}</h3>
      {disk.state === 'low' ? (
        <HealthWarning>พื้นที่ว่างต่ำกว่าเกณฑ์ที่ตั้งไว้</HealthWarning>
      ) : <p className="health-ok">พื้นที่ว่างอยู่ในเกณฑ์</p>}
      <dl className="health-values">
        <div><dt>พื้นที่ว่าง</dt><dd>{formatHealthBytes(disk.free_bytes)}</dd></div>
        <div><dt>พื้นที่ทั้งหมด</dt><dd>{formatHealthBytes(disk.total_bytes)}</dd></div>
        <div><dt>เกณฑ์ขั้นต่ำ</dt><dd>{formatHealthBytes(disk.min_free_bytes)}</dd></div>
      </dl>
    </article>
  )
}

function HealthCoverageCard({ group }: { group: HealthCoverage }) {
  return (
    <article className="health-card">
      <h3>กลุ่ม {group.group_id.slice(0, 8)}</h3>
      <dl className="health-values">
        <div><dt>ตรวจสำเร็จล่าสุด</dt><dd>{formatBangkokTime(group.last_successful_at)}</dd></div>
        <div>
          <dt>ผลรอบล่าสุด</dt>
          <dd>{group.latest_coverage ? HEALTH_COVERAGE_LABELS[group.latest_coverage] : 'ยังไม่มีผลรอบตรวจ'}</dd>
        </div>
        <div><dt>ช่วงขาดทั้งหมด</dt><dd>{group.capture_gap_count} ช่วง</dd></div>
      </dl>
      {group.latest_gap_7d ? (
        <p className="health-detail">
          ช่วงขาดใน 7 วัน: {formatBangkokTime(group.latest_gap_7d.gap_from)} –{' '}
          {formatBangkokTime(group.latest_gap_7d.gap_to)}
        </p>
      ) : <p className="muted-text">ไม่มีช่วงขาดใน 7 วันล่าสุด</p>}
    </article>
  )
}

function AdminHealthPage() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    getHealth(controller.signal)
      .then(setHealth)
      .catch((cause: unknown) => {
        const message = errorMessage(cause)
        if (message) setError(message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [refreshKey])

  if (loading) return <LoadingState label="กำลังตรวจสุขภาพระบบ…" />
  if (error || !health) {
    return (
      <ErrorState
        message={error || 'โหลดข้อมูลสุขภาพระบบไม่ได้'}
        retry={() => setRefreshKey((value) => value + 1)}
      />
    )
  }

  const queueEntries = Object.entries(health.queue) as [HealthJobKind, SystemHealth['queue'][HealthJobKind]][]
  const diskEntries: { title: string; disk: HealthDiskStatus }[] = [
    { title: 'คลังภาพ', disk: health.disk.media },
  ]
  if (health.disk.backup) diskEntries.push({ title: 'พื้นที่สำรอง', disk: health.disk.backup })

  return (
    <section className="page-section admin-health" aria-labelledby="health-title">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">สำหรับผู้ดูแล</p>
          <h1 id="health-title">สุขภาพระบบ</h1>
        </div>
        <button className="button button-secondary health-refresh" type="button" onClick={() => setRefreshKey((value) => value + 1)}>
          ตรวจอีกครั้ง
        </button>
      </div>
      <p className="health-checked-at">ตรวจข้อมูลเมื่อ {formatBangkokTime(health.checked_at)}</p>

      <section className="health-section" aria-labelledby="health-queue-title">
        <h2 id="health-queue-title">คิวงาน</h2>
        <div className="health-grid">
          {queueEntries.map(([kind, status]) => (
            <article className="health-card" key={kind}>
              <h3>{HEALTH_JOB_LABELS[kind]}</h3>
              <strong className="health-count">{status.overdue_count} งาน</strong>
              {status.overdue_count > 0 ? (
                <HealthWarning>
                  มีงานเกินกำหนด · งานที่ค้างนานที่สุด {status.oldest_overdue_age_minutes ?? 0} นาที
                </HealthWarning>
              ) : <p className="health-ok">ไม่มีงานเกินกำหนด</p>}
            </article>
          ))}
        </div>
      </section>

      <section className="health-section" aria-labelledby="health-coverage-title">
        <h2 id="health-coverage-title">รอบตรวจและช่วงขาด</h2>
        {health.coverage.length ? (
          <div className="health-grid">
            {health.coverage.map((group) => <HealthCoverageCard group={group} key={group.group_id} />)}
          </div>
        ) : <p className="muted-text">ยังไม่มีกลุ่มที่ตรวจได้</p>}
      </section>

      <section className="health-section" aria-labelledby="health-media-title">
        <h2 id="health-media-title">ภาพใน 24 ชั่วโมง</h2>
        <article className="health-card">
          <dl className="health-values">
            <div><dt>เก็บภาพสำเร็จ</dt><dd>{health.media.captured}</dd></div>
            <div><dt>เก็บภาพไม่สำเร็จ</dt><dd>{health.media.capture_failed}</dd></div>
            <div>
              <dt>อัตราสำเร็จ</dt>
              <dd>{health.media.success_rate === null ? 'ไม่มีข้อมูลภาพ' : `${(health.media.success_rate * 100).toFixed(1)}%`}</dd>
            </div>
            <div><dt>เหตุพื้นที่ต่ำ</dt><dd>{health.media.disk_low}</dd></div>
          </dl>
        </article>
      </section>

      <section className="health-section" aria-labelledby="health-disk-title">
        <h2 id="health-disk-title">พื้นที่จัดเก็บ</h2>
        <div className="health-grid">
          {diskEntries.map(({ title, disk }) => <HealthDiskCard key={title} title={title} disk={disk} />)}
        </div>
      </section>

      <section className="health-section" aria-labelledby="health-backup-title">
        <h2 id="health-backup-title">ข้อมูลสำรอง</h2>
        <article className="health-card">
          {health.backup.state === 'never' ? (
            <HealthWarning>ยังไม่เคยสำรองข้อมูล</HealthWarning>
          ) : health.backup.state === 'stale' ? (
            <HealthWarning>ข้อมูลสำรองเก่ากว่า 26 ชั่วโมง</HealthWarning>
          ) : <p className="health-ok">ข้อมูลสำรองยังใหม่</p>}
          <dl className="health-values">
            <div><dt>สำเร็จล่าสุดจริง</dt><dd>{formatBangkokTime(health.backup.last_success_at)}</dd></div>
            <div><dt>อายุ</dt><dd>{health.backup.age_hours === null ? '—' : `${health.backup.age_hours} ชั่วโมง`}</dd></div>
            <div><dt>ล้มเหลวล่าสุด</dt><dd>{formatBangkokTime(health.backup.last_failure_at)}</dd></div>
          </dl>
        </article>
      </section>

      <section className="health-section" aria-labelledby="health-ai-title">
        <h2 id="health-ai-title">คิวเสริมข้อมูลด้วย AI</h2>
        <article className="health-card">
          <dl className="health-values">
            {Object.entries(health.ai_backlog.states).map(([state, count]) => (
              <div key={state}><dt>{HEALTH_ENRICH_LABELS[state as keyof SystemHealth['ai_backlog']['states']]}</dt><dd>{count}</dd></div>
            ))}
            <div>
              <dt>งานรอนานที่สุด</dt>
              <dd>
                {health.ai_backlog.oldest_queued_age_seconds === null
                  ? 'ไม่มีงานรอ'
                  : `${Math.floor(health.ai_backlog.oldest_queued_age_seconds / 60)} นาที`}
              </dd>
            </div>
          </dl>
        </article>
      </section>
    </section>
  )
}

function NotFoundPage() {
  return (
    <section className="page-section empty-state" aria-labelledby="not-found-title">
      <p className="eyebrow">404</p>
      <h1 id="not-found-title">ไม่พบหน้าที่ต้องการ</h1>
      <RouteLink to="/" className="button button-primary">กลับไปหน้าสินค้า</RouteLink>
    </section>
  )
}

function MainNavigation({ route }: { route: Route }) {
  const links = [
    { to: '/', label: 'สินค้า', icon: '◈', active: route.name === 'feed' || route.name === 'post' || route.name === 'evidence' || route.name === 'seller' },
    { to: '/watches', label: 'ติดตาม', icon: '☆', active: route.name === 'watches' },
    { to: '/groups', label: 'กลุ่ม', icon: '▦', active: route.name === 'groups' },
    { to: '/imports', label: 'นำเข้า', icon: '＋', active: route.name === 'imports' },
  ]
  return (
    <nav className="bottom-navigation" aria-label="เมนูหลัก">
      {links.map((link) => (
        <RouteLink className={`nav-tab${link.active ? ' nav-tab-active' : ''}`} to={link.to} key={link.to} current={link.active}>
          <span className="nav-icon" aria-hidden="true">{link.icon}</span>
          <span>{link.label}</span>
        </RouteLink>
      ))}
    </nav>
  )
}

export default function App() {
  const [path, setPath] = useState(() => (typeof window === 'undefined' ? '/' : window.location.pathname))
  const [needsUnlock, setNeedsUnlock] = useState(false)
  const route = useMemo(() => parseRoute(path), [path])

  useEffect(() => {
    function onPopState() {
      setPath(window.location.pathname)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    function requireUnlock() {
      setNeedsUnlock(true)
    }
    function sessionUnlocked() {
      setNeedsUnlock(false)
    }
    window.addEventListener('paiya:session-required', requireUnlock)
    window.addEventListener('paiya:session-unlocked', sessionUnlocked)
    return () => {
      window.removeEventListener('paiya:session-required', requireUnlock)
      window.removeEventListener('paiya:session-unlocked', sessionUnlocked)
    }
  }, [])

  function navigate(to: string) {
    if (typeof window === 'undefined') return
    window.history.pushState(null, '', to)
    setPath(window.location.pathname)
    window.scrollTo(0, 0)
  }

  let page: ReactNode
  switch (route.name) {
    case 'feed': page = <FeedPage />; break
    case 'admin-health': page = <AdminHealthPage />; break
    case 'post': page = <PostPage id={route.id} />; break
    case 'evidence': page = <EvidencePage id={route.id} />; break
    case 'seller': page = <SellerPage id={route.id} />; break
    case 'watches': page = <WatchesPage />; break
    case 'groups': page = <GroupsPage />; break
    case 'imports': page = <ImportPage />; break
    default: page = <NotFoundPage />
  }

  if (needsUnlock) return <UnlockScreen />

  return (
    <RouterContext.Provider value={{ navigate }}>
      <a className="skip-link" href="#main-content">ข้ามไปยังเนื้อหา</a>
      <div className="app-shell">
        <header className="app-header">
          <RouteLink to="/" className="brand-link" ariaLabel="ป้ายยา หน้าสินค้า">
            <span className="brand-mark" aria-hidden="true">ป</span>
            <span><strong>ป้ายยา</strong><small>ข้อมูลที่มีหลักฐาน</small></span>
          </RouteLink>
          <span className="demo-indicator"><span aria-hidden="true" />ข้อมูลสังเคราะห์</span>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>{page}</main>
        <MainNavigation route={route} />
      </div>
    </RouterContext.Provider>
  )
}
