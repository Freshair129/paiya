# สัญญาข้อมูลของ Collector และช่องทางค้นคืน

**สถานะ** แบบที่ใช้อ้างอิงสำหรับ MVP · รอยืนยันด้วย fixture · **ผู้ดูแล** ผู้พัฒนา Collector และ API ร่วมกัน

หน้านี้กำหนดรูปแบบข้อมูลกลางที่ Collector ส่งเข้า backend และสิ่งที่แอปกับ Agent เรียกอ่านได้ รูปแบบที่เสนอรองรับประวัติการตรวจ (FR-05) หลักฐาน (FR-07) การค้นผ่าน MCP (FR-11) และการตรวจสิทธิ์ (FR-13) ดูข้อกำหนดทั้งหมดและเกณฑ์รับมอบใน [SRS](../../requirements/SRS.md)

## ข้อมูลจาก Collector

โครงสร้างที่ใช้ตรวจจริงอยู่ใน [observation.v1.schema.json](observation.v1.schema.json) หน้านี้อธิบายความหมาย กฎค่า null และเงื่อนไขข้ามฟิลด์ที่ JSON Schema แสดงไม่ได้ ตัวอย่างที่ผ่านและไม่ผ่านอยู่ใน [fixtures/contract/observation](../../../fixtures/contract/observation/) รัน `python scripts/check_contracts.py` เพื่อตรวจ fixture และตัวอย่าง JSON ในหน้านี้กับ schema

ตัวอย่างย่อของ payload จาก fixture กำหนด source_post_key และ source_item_key คงที่ข้ามรอบ ราคาเป็นเลขฐานสิบในรูปข้อความที่ระบุสกุลเงินชัดเจน

```json
{
  "schema_version": 1,
  "group_key": "demo-group",
  "source_post_key": "demo-42",
  "permalink": "https://www.facebook.com/groups/demo-group/posts/42/",
  "observed_at": "2026-11-06T03:00:00Z",
  "access": "ok",
  "posted_at": {"source_text": "5 ชม.", "value": null, "precision": "hour"},
  "seller": {"identity_scope": "demo-group", "external_actor_key": "demo-s7"},
  "items": [{"source_item_key": "sku-1", "model": "RTX 3060 12GB",
             "price": {"kind": "ask", "unit": "each", "amount": "5500.00", "currency": "THB"},
             "evidence_keys": ["e1"]}],
  "activity": {"source_reported_comment_count": 5, "distinct_comments_seen": 2,
               "distinct_known_commenters_seen": 1, "coverage": "partial"},
  "evidence": [{"local_key": "e1", "role": "seller",
                "source_span": {"field": "post_text", "quote": "RTX 3060 12GB 5500 บาท"},
                "media_state": "captured"}]
}
```

Worker ห่อ payload ด้วย envelope ที่มี run_id, capture_id, grant_id, source_kind (`fixture` หรือ `facebook_live`), job_kind (`discover` หรือ `revisit`), ผลตรวจหน้า (`page_check.session`, `page_check.sort`) และ `assets` ที่จับคู่ local_key กับไฟล์ใน staging ด้วย staging_key, SHA-256, MIME และขนาด ห้ามใส่ path บนเครื่องใน envelope Backend ตรวจ schema และสิทธิ์ซ้ำก่อนสร้าง UUID และ FK จริง เมื่อเขียนไฟล์ล้มเหลวให้เก็บเหตุผลและ retry เฉพาะ media งานเก็บไฟล์ต้องเขียนไฟล์ชั่วคราวแล้ว rename แบบ atomic พร้อมกวาด orphan หลัง recovery

### ความหมายของฟิลด์และค่าที่อนุญาต

| ฟิลด์ | ความหมายและกฎ |
|---|---|
| `observed_at`, `*.value` | เวลา UTC ลงท้าย `Z` เท่านั้น การแสดงเป็น Asia/Bangkok ทำที่ API/UI |
| `posted_at`, `comments[].source_time` | `source_text` เก็บสิ่งที่ต้นทางแสดง เช่น “5 ชม.” ส่วน `value` เป็น null เมื่อแปลงเป็นเวลาที่แน่นอนไม่ได้ `precision` คือ `exact`, `minute`, `hour`, `day` หรือ `unknown` |
| `access` | `ok`, `unavailable`, `permission_denied`, `session_required`, `challenge_required` ค่าอื่นนอกจาก `ok` ห้ามส่ง `items` หรือ `comments` ที่มีข้อมูล และไม่เปลี่ยนสถานะขายเดิม (SRS ข้อ 4) |
| `page_check.sort` | `verified_latest` เมื่อยืนยันการเรียงโพสต์ใหม่ได้ `unverified` เมื่อยืนยันไม่ได้ งาน `revisit` ที่เปิด permalink ตรงใช้ `not_applicable` ซึ่งงาน `discover` ใช้ไม่ได้ |
| `grant_id` | เป็น null ได้เฉพาะ `source_kind: fixture` |
| `seller` | null เมื่อไม่เห็นผู้โพสต์ `external_actor_key` เป็น null เมื่อเห็นชื่อแต่ไม่มีตัวระบุที่คงที่ ชื่อแสดงผลไม่ใช้ผูกตัวตน |
| `items[].source_item_key` | null เมื่อแยกรายการไม่ได้ Backend เก็บเป็น unresolved หรือ bundle ตาม SRS ข้อ 3 |
| `items[].price` | `kind` คือ `ask`, `deposit`, `shipping`, `installment`, `offer`, `unknown` และ `unit` คือ `each`, `bundle`, `unknown` ราคาเป็นข้อความทศนิยมไม่เกินสองตำแหน่ง ถ้าต้นทางให้เป็นช่วงให้ใส่ `amount_max` เพิ่ม Adapter รายงานตามที่เห็น ส่วนการคัดเข้าชุดเปรียบเทียบราคาเป็นหน้าที่ของ backend ตาม SRS ข้อ 8 |
| `items[].source_status_label` | ข้อความป้ายจากต้นทางตามที่เห็น เช่น ป้ายขายแล้ว ไม่ใช่ sale_status ที่ระบบตัดสิน |
| `activity` | `source_reported_comment_count` เป็น null เมื่อต้นทางไม่แสดงยอด ห้ามใส่ 0 แทน `coverage` คือ `complete`, `partial`, `none` |
| `comments[].role`, `evidence[].role` | `seller` เมื่อยืนยันได้ว่าเป็นเจ้าของประกาศในโพสต์เดียวกัน `other` สำหรับผู้แสดงความเห็นอื่น และ `unknown` เมื่อยืนยันไม่ได้ evidence ใช้ `source_label` สำหรับป้ายของต้นทาง |
| `evidence[].source_span` | `field` ระบุที่มา (`post_text`, `comment_text`, `source_label`) `quote` คือข้อความที่อ้าง `start`/`end` เป็น offset แบบ Unicode code point ภายในข้อความนั้นถ้ามี |
| `evidence[].media_state` | `captured`, `missing_at_source`, `capture_failed`, `not_requested` ใช้ `media_reason` อธิบายกรณีที่ไม่ได้ภาพ |

เงื่อนไขข้ามฟิลด์ที่ `scripts/check_contracts.py` ตรวจเพิ่มจาก schema

- `distinct_known_commenters_seen` ไม่เกิน `distinct_comments_seen` และเมื่อส่ง `comments` มา จำนวนรายการต้องเท่ากับ `distinct_comments_seen`
- `items[].evidence_keys` และคีย์ใน `assets` ต้องมี evidence ที่ local_key ตรงกัน evidence ที่ `captured` ต้องมี asset
- span ของ `comment_text` ต้องชี้ comment ที่มีอยู่ และ quote ต้องตรงกับ offset หรือพบในข้อความนั้น
- `source_comment_key` ไม่ซ้ำภายใน payload และ `amount_max` ไม่ต่ำกว่า `amount`

`capture_id` คงเดิมทุกครั้งที่ retry งานเดียวกันของโพสต์เดียวกัน การเปลี่ยนชื่อหรือความหมายของฟิลด์ หรือการลบค่า enum ต้องออก `schema_version` ใหม่ การเพิ่มค่า enum หรือฟิลด์ที่ไม่บังคับทำในรุ่นเดิมได้เมื่อ backend รองรับก่อน adapter ส่งค่า

## HTTP API และ MCP

| ช่องทาง | คำสั่งและผลลัพธ์ขั้นต่ำ |
|---|---|
| จัดการกลุ่ม | GET /v1/groups แสดงรายการ · POST /v1/groups เพิ่มแหล่งที่มี grant, PATCH /v1/groups/{id} เปลี่ยนรอบ/พัก, GET /v1/groups/{id}/runs ดูผลและ gap · DELETE /v1/groups/{id} ยกเลิกติดตาม · POST /v1/purge-jobs ขอให้ลบตามขอบเขตและ GET /v1/purge-jobs/{id} ตรวจผล |
| ค้น/ดูประกาศ | GET /v1/posts รองรับ model, group, price, sale_status, tag และ cursor · GET /v1/posts/{id} คืนรายการสินค้า ราคา permalink สถานะ/เวลา/coverage และ evidence_id · GET /v1/posts/{id}/timeline เรียง cursor ตามเวลา |
| ส่งออก | GET /v1/posts/{id}/export?format=json หรือ format=md คืนข้อมูลตามสิทธิ์พร้อม schema_version, permalink, เวลาสังเกตและ evidence_id Markdown สร้างจากผล JSON ครั้งเดียวกัน |
| ผู้ขายและหลักฐาน | GET /v1/sellers/{id} พร้อม coverage และรายการต้นทาง · GET /v1/evidence/{id} คืน metadata และ GET /v1/evidence/{id}/media คืนรูปเมื่อ grant ที่ผูกยังใช้ได้ |
| Watch และราคา | PUT หรือ DELETE /v1/watches/{post_id} เป็น idempotent, GET /v1/prices/trends คืน sample count, window, ตัวกรอง, percentiles ที่รองรับ และสถานะข้อมูลไม่พอ |
| MCP local stdio | search_posts, get_post, get_seller, get_evidence, get_price_trend ใช้ backend service เดียวกับ API get_evidence ส่ง MCP image content หลังตรวจสิทธิ์ ห้ามรับ arbitrary SQL, URL หรือ path |

ผลการค้นใช้ cursor และ limit แบบมีเพดาน client ต้องโหลดเพิ่มได้ API แยก unauthenticated, invalid input และ not-found ตามมาตรฐาน HTTP ส่วนการพยายามอ่าน ID นอก scope ให้ผลเหมือนหาไม่พบเพื่อไม่เปิดเผยตัวตนของระเบียน โพสต์และ OCR เป็นข้อมูลภายนอกเสมอ ห้ามป้อนคำสั่งจากแหล่งข้อมูลเข้าระบบเครื่องมือของ Agent

## เมื่อสัญญาเปลี่ยน

เมื่อเปลี่ยนความหมายของฟิลด์ เวอร์ชันข้อมูล คำสั่ง API หรือขอบเขตสิทธิ์ ให้อัปเดตตัวอย่างและเกณฑ์รับมอบที่เกี่ยวข้องใน PR เดียวกัน เมื่อมี FastAPI ที่รันได้ ให้เชื่อม OpenAPI ที่สร้างจากโค้ดสำหรับ HTTP path, method และ schema แล้วเก็บความหมายของข้อมูล กฎสิทธิ์ และตัวอย่างข้ามระบบไว้ในหน้านี้

การยืนยันสัญญาต้องมีผลทดสอบที่อ้างถึง commit และ fixture ที่ใช้ บันทึกลิงก์ผลตามข้อกำหนดใน [ตารางตรวจรับ](../../quality/TRACEABILITY.md)
