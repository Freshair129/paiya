# สัญญาเส้นทางอ่านบนมือถือ (ระยะ A)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A5 ตามคำตัดสิน F1–F3 ใน [Issue #37](https://github.com/Freshair129/paiya/issues/37) · **ผู้ดูแล** เจ้าของ PWA และ API

หน้านี้กำหนดวิธีเปิดแอปบนเครื่องด้วยข้อมูลสังเคราะห์ endpoint ที่หน้า **สินค้า · ติดตาม · กลุ่ม** ใช้ ข้อความไทยของสถานะ และวิธีวัด AT-05 ใช้คู่กับ [PRD](../../product/PRD.md) ข้อ 4–6, [SRS](../../requirements/SRS.md) FR-07, FR-09 และ AT-05, [สัญญา Collector](collector-and-query.md), [สัญญาสถานะรายชิ้น](item-status-and-watches.md) และ [สัญญาคิวค้นโพสต์](discovery-queue.md)

## 1. Read context เมื่อรัน server ในระยะ A (F2)

- รัน API ด้วย `uv run python -m paiya.serve` ซึ่งผูกเฉพาะ `127.0.0.1` พอร์ตจาก `PAIYA_PORT` (ค่าเริ่มต้น 8000) และเสิร์ฟไฟล์ที่ build แล้วจาก `web/dist` ที่ `/` (เส้นทางที่ไม่ใช่ `/v1/*` คืน `index.html`) จาก origin เดียวกับ API ตาม SPEC ข้อ 1
- โหมด HTTP fixture เปิดใช้เฉพาะเมื่อกำหนด `PAIYA_DEV_FIXTURE_MODE=1` อย่างชัดเจนและตั้ง `PAIYA_READ_CONTEXT_GROUP_KEY` เป็นกลุ่มที่ขึ้นต้นด้วย `synthetic-`; server ปฏิเสธค่ากลุ่มที่ไม่ขึ้นต้นตามนี้ ไม่รับการเลือกกลุ่มจาก header, query หรือ body
- เมื่อเปิดโหมด fixture server เลือกได้เฉพาะ `groups.source = fixture` และ grant ที่ใช้ได้และมี `read`; กลุ่มไม่มี grant ตอบ 403 `grant_unavailable`
- เมื่อปิดโหมด fixture (ค่าปริยาย) endpoint ที่อ่านข้อมูลใช้ owner session และ readable grants ตาม [สัญญา session](owner-session.md); หากไม่มี session ตอบ 401 `session_required` และต้องปลดล็อกเจ้าของเครื่องก่อน
- ห้ามใช้ fixture mode กับข้อมูลที่ไม่ใช่ fixture การเปิดข้อมูลผู้ซื้อจริงต้องใช้ owner session ตาม [ADR 0004](../decisions/0004-local-access-and-mcp.md) และ [สัญญา session](owner-session.md)
- `uv run python -m paiya.cli demo seed` สร้างกลุ่ม `synthetic-demo` พร้อม grant และนำเข้า fixture สาธิต (ข้อ 6) ลงฐานที่ `PAIYA_DATABASE_URL` ชี้ เพื่อเปิดแอปและถ่ายภาพหลักฐาน

## 2. Endpoint ของหน้าจอ

**`GET /v1/posts`** (F1) พารามิเตอร์ `sale_status` (ค่าใดค่าหนึ่งของ sale_status, ตรงเมื่อมีชิ้นใดมีสถานะนั้น), `min_price`, `max_price` (ทศนิยมบาท เทียบกับ ask ของชิ้นใดชิ้นหนึ่งที่สกุลเงิน THB ปลายช่วงรวม), `limit` (ค่าเริ่มต้น 20 สูงสุด 50 เกินตอบ 422), `cursor` เรียงตาม `last_success_at` ล่าสุดก่อน แล้ว `post_id` ตัวกรองรุ่น หน่วยความจำ และสภาพเพิ่มตาม [สัญญากฎสกัด](extraction-and-tags.md) ข้อ 7 สถานะและราคาใช้ค่าที่อ่านได้ใน context เดียวกับ `GET /v1/posts/{id}`

```jsonc
{
  "posts": [
    {"post_id": "…", "group_id": "…", "permalink": "https://example.invalid/…",
     "posted_at": null, "last_success_at": "2026-11-07T10:00:00Z",
     "has_unresolved_items": false, "watched": true,
     "thumbnail_evidence_id": "…",
     "items": [{"item_id": "…", "source_item_key": "sku3060", "model": "RTX 3060 12GB",
                "sale_status": "open", "status_since": "…", "needs_review": false,
                "ask": {"amount": "5500.00", "currency": "THB", "unit": "each"}}]}
  ],
  "next_cursor": null
}
```

`model` คือข้อความรุ่นตามที่เห็นใน observation ล่าสุด ใช้แสดงผลเท่านั้น `thumbnail_evidence_id` คือหลักฐานแรกที่ `media_state = captured` ของ observation ล่าสุด หรือ null `items` มีเฉพาะชิ้นที่มี key

**`GET /v1/groups`** คืนกลุ่มใน context `{"groups": [{"group_id", "group_key", "enabled", "poll_minutes", "last_success_at", "last_run"}]}` โดย `last_run` คือแถวล่าสุดในรูปแบบเดียวกับ `GET /v1/groups/{id}/runs` หรือ null

**`GET /v1/watches`** เพิ่มฟิลด์ `post` ในแต่ละแถว เป็นการ์ดรูปแบบเดียวกับ `GET /v1/posts` ส่วน `PUT/DELETE /v1/watches/{post_id}` ในสัญญา Collector เลื่อนไปงาน session (E5) หน้า **ติดตาม** ในระยะ A จึงอ่านอย่างเดียว

หน้าผู้ขายแสดง `display_name` ล่าสุดที่เห็นใน observation ของ context โดย `GET /v1/sellers/{id}` เพิ่มฟิลด์ `display_name` (null ได้) พร้อมป้าย “ชื่อที่เห็นล่าสุด” ชื่อไม่ใช้รวมตัวตน

## 3. ข้อความไทยบนหน้าจอ

| ค่า | ข้อความ |
|---|---|
| `sale_status` | `unknown` “ยังไม่พบข้อความปิดขาย” · `open` “เปิดขาย” · `reserved` “ติดจอง” · `sold` “ขายแล้ว” · `withdrawn` “ถอนประกาศ” |
| หลายชิ้น | “ขายแล้ว x จาก n รายการ” เมื่อทราบสถานะครบทุกชิ้น นอกนั้นแสดงรายชิ้น |
| `needs_review` | “ข้อมูลขัดกัน · ต้องตรวจสอบ” ไม่แสดงป้ายยืนยันสถานะ |
| `has_unresolved_items` | “มีรายการที่ยังแยกชิ้นไม่ได้” |
| `access` | `unavailable` “เข้าโพสต์ไม่ได้” · `permission_denied` “ไม่มีสิทธิ์เข้าถึง” · `session_required` “ต้องเข้าสู่ระบบที่ต้นทาง” · `challenge_required` “ต้นทางขอยืนยันตัวตน” |
| `media_state` | `captured` แสดงภาพ · อื่น ๆ “ไม่มีภาพหลักฐานในรอบนี้” ตามด้วยเหตุ: `missing_at_source` “ต้นทางไม่มีภาพ”, `capture_failed` “เก็บภาพไม่สำเร็จ”, `not_requested` “ไม่ได้เก็บภาพ” |
| `coverage` ของรอบ | `complete` “อ่านครบ” · `partial` “อ่านได้บางส่วน” พร้อมเหตุ · `none` “อ่านไม่ได้” |
| เวลา | แสดงตาม Asia/Bangkok แยก “โพสต์เมื่อ …”, “พบครั้งแรก …” และ “ตรวจสำเร็จล่าสุด …” ตาม PRD ข้อ 4 เมื่อ `now − last_success_at` เกิน `poll_minutes` ของกลุ่ม หรือ Watch มี `overdue_since` ให้แสดง “ไม่ได้ตรวจตั้งแต่ …” |
| รุ่น (A6d) | `model_key` แสดงเป็นตัวพิมพ์ใหญ่ของตระกูลและเลข ตามด้วยส่วนต่อท้ายแบบขึ้นต้นตัวใหญ่ เช่น `rtx-4070-ti-super` “RTX 4070 Ti Super”, `rx-7900-xtx` “RX 7900 XTX” · มี `vram_gb` ต่อท้าย “ · 12GB” · `model_key` เป็น null แสดงข้อความ `model` เดิม |
| `condition` | `new` “ของใหม่” · `new_no_warranty` “ของใหม่ ไม่มีประกัน” · `used` “มือสอง” · null ไม่แสดง |
| `warranty` | `none` “ไม่มีประกัน” · `active` ไม่มี `warranty_until` “มีประกัน” · `active` และ `warranty_months_left` > 0 “ประกันเหลือ N เดือน (ถึง <เดือนย่อ ปี พ.ศ.>)” · `active` และ `warranty_months_left` = 0 “ประกันถึง <เดือนย่อ ปี พ.ศ.> · ครบกำหนดแล้ว” · null ไม่แสดง เดือนใช้ `Intl` locale `th-TH` แบบ `month: short, year: numeric` |

ราคาเสนอขายแสดงว่า “ราคาตั้งขาย” ไม่ใช่ราคาที่ขายได้จริง ทุกสีสถานะมีข้อความกำกับ พื้นที่แตะอย่างน้อย 44 × 44 CSS px

**แผ่นตัวกรองหน้าสินค้า (A6d)** เพิ่มจากสถานะและช่วงราคาเดิม ทุกช่องมีค่าเริ่มต้นที่ไม่กรอง

- **รุ่น** ตัวเลือก “ทุกรุ่น” และรายการจาก `GET /v1/models` แสดงชื่อตามตารางข้างบนพร้อมจำนวนชิ้น เช่น “RTX 3060 (2)” โหลดรายการไม่สำเร็จให้แสดง “โหลดรายการรุ่นไม่สำเร็จ” และยังใช้ตัวกรองอื่นได้
- **หน่วยความจำ** “ทุกขนาด” และ 4, 6, 8, 10, 11, 12, 16, 20, 24 GB (ชุดค่าจากตารางรุ่นของ `extract-v1.0`)
- **สภาพ** “ทุกสภาพ” · “ของใหม่” · “ของใหม่ ไม่มีประกัน” · “มือสอง”
- **ประกัน** “ทุกแบบ” · “มีประกัน” · “ไม่มีประกัน”

ตัวกรองที่เลือกส่งเป็นพารามิเตอร์ `model_key`, `vram_gb`, `condition`, `warranty` ของ `GET /v1/posts` ผลต้องตรงบนชิ้นเดียวกันตามสัญญากฎสกัด ข้อความสรุปตัวกรองบนปุ่มใช้ชื่อตามตารางข้างบน เช่น “RTX 3060”, “12GB”, “มือสอง”, “มีประกัน” การ์ดและหน้าประกาศแสดงรุ่น สภาพ และประกันของแต่ละชิ้นตามตารางข้างบน ที่ 360 CSS px ต้องไม่เลื่อนแนวนอนและพื้นที่แตะอย่างน้อย 44 × 44 CSS px เหมือนเดิม

## 4. AT-05 และการวัด

**การนับแตะ** เริ่มเมื่อหน้า **สินค้า** โหลดแล้วและการ์ดเป้าหมายมองเห็นโดยไม่ต้องพิมพ์หรือเลื่อน จบเมื่อหน้าจอแสดงภาพ crop และลิงก์ต้นทาง (permalink) ที่โฟกัสได้ในหน้าเดียวกัน เส้นทางอ้างอิงคือ การ์ด → หน้าประกาศ → แถวหลักฐาน = 2 แตะ เกณฑ์ผ่านคือไม่เกิน 3

**ที่ 360 × 800 CSS px** หน้า สินค้า, ประกาศ, หลักฐาน, ผู้ขาย, ติดตาม และกลุ่ม ต้องไม่มีการเลื่อนแนวนอน (`document.documentElement.scrollWidth ≤ 360`) อ่านราคา สถานะ เวลาตรวจ และปุ่มหลักได้ และใช้แป้นพิมพ์เข้าถึงรายการ หลักฐาน และลิงก์ต้นทางได้

**ภาพหลักฐาน** เก็บที่ `docs/quality/evidence/AT-05/<YYYY-MM-DD>/` พร้อม `README.md` ระบุ commit, ชุดข้อมูล `synthetic-demo` และคำสั่งที่ใช้ ใช้ข้อมูลสังเคราะห์เท่านั้น ส่วน AT-05 เรื่องภาพผ่าน MCP ตรงกับ UI เป็นงาน A6

## 5. ข้อบังคับของแอป

- A5 ยังไม่มี service worker มีเพียง web manifest เพื่อไม่ให้หลักฐานส่วนตัวถูก cache ถาวร (SRS ข้อ 6)
- ตอนพัฒนา Vite ส่งต่อ `/v1` ไปยัง `http://127.0.0.1:8000`
- เทสต์ UI ใช้ Playwright กับ Chromium (F3, [ADR 0003](../decisions/0003-dev-tooling.md)) ปิดคำขอทุกปลายทางที่ไม่ใช่ `127.0.0.1` ในเทสต์

## 6. Fixture สาธิต

`fixtures/demo/` เก็บ envelope สังเคราะห์และภาพ WebP สังเคราะห์ (รูปทรงเรียบ ไม่มีภาพจริง) อย่างน้อยหนึ่งโพสต์ที่หลักฐานเป็น `captured` หนึ่งโพสต์ที่ภาพขาด และโพสต์หลายชิ้น พร้อม Watch หนึ่งรายการ คำสั่ง `demo seed` นำเข้าด้วย ingest เดิมและเก็บภาพใน `PAIYA_FIXTURE_MEDIA_ROOT` ชุดสาธิตใช้เพื่อแสดงผลและถ่ายภาพ ไม่ใช่เฉลยตรวจรับ
