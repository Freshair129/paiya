# สัญญาข้อมูลของ Collector และช่องทางค้นคืน

**สถานะ** observation v1 มี JSON Schema และ fixture สังเคราะห์สำหรับตรวจสัญญา ส่วน API และ MCP เป็นแบบสำหรับพัฒนา · **ผู้ดูแล** ผู้พัฒนา Collector และ API ร่วมกัน

หน้านี้กำหนดรูปแบบข้อมูลกลางที่ Collector ส่งเข้า backend และสิ่งที่แอปกับ Agent เรียกอ่านได้ รูปแบบที่เสนอรองรับประวัติการตรวจ (FR-05) หลักฐาน (FR-07) การค้นผ่าน MCP (FR-11) และการตรวจสิทธิ์ (FR-13) ดูข้อกำหนดทั้งหมดและเกณฑ์รับมอบใน [SRS](../../requirements/SRS.md)

งานนำเข้าโดยผู้ใช้ กฎตั้งรับ การติดตามรายชิ้น และการเทียบดีลมีข้อเสนอ API กับรูปแบบ observation v2 แยกไว้ ข้อมูล v1 ในหน้านี้ยังเป็นฐานสำหรับทดสอบความเข้ากันได้ [สัญญาเส้นทางผู้ซื้อ](buyer-workflows.md) · [แบบจำลองข้อมูล](../data-model.md)

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
| Watch และราคา | PUT หรือ DELETE /v1/watches/{post_id} เป็น idempotent (เลื่อนไปงาน session ตาม E5 ระยะ A ใช้ CLI ดู [สัญญาเส้นทางมือถือ](mobile-read-path.md)), GET /v1/prices/trends คืน sample count, window, ตัวกรอง, percentiles ที่รองรับ และสถานะข้อมูลไม่พอ |
| MCP local stdio | search_posts, get_post, get_seller, get_evidence, get_price_trend ใช้ backend service เดียวกับ API get_evidence ส่ง MCP image content หลังตรวจสิทธิ์ ห้ามรับ arbitrary SQL, URL หรือ path |

ผลการค้นใช้ cursor และ limit แบบมีเพดาน client ต้องโหลดเพิ่มได้ API แยก unauthenticated, invalid input และ not-found ตามมาตรฐาน HTTP ส่วนการพยายามอ่าน ID นอก scope ให้ผลเหมือนหาไม่พบเพื่อไม่เปิดเผยตัวตนของระเบียน โพสต์และ OCR เป็นข้อมูลภายนอกเสมอ ห้ามป้อนคำสั่งจากแหล่งข้อมูลเข้าระบบเครื่องมือของ Agent

สำหรับ AT-03 ตัวตรวจ v1 เช็กว่า quote อ้างคอมเมนต์ที่มีอยู่ แต่ยังไม่ตรวจความสอดคล้องระหว่าง role ของหลักฐานกับ role ของผู้เขียน ก่อนนำคอมเมนต์มายืนยันสถานะต้องเพิ่มการตรวจตัวผู้ขาย ความสัมพันธ์กับโพสต์/ชิ้น และกรณีทดสอบที่ป้ายผู้ขายชี้ข้อความของผู้ซื้อ [ข้อกำหนดก่อนสร้างสถานะ](buyer-workflows.md)

## การบันทึกและอ่านในระยะ A (fixture)

หัวข้อนี้กำหนดรายละเอียดที่งาน A2 ใช้เขียนโค้ด และเป็นฐานของ A3–A6 ระยะ A รับเฉพาะ envelope ที่ `source_kind: fixture` ยังไม่มี HTTP endpoint สำหรับเขียน การนำเข้าเรียกผ่าน service ภายในจาก test หรือเครื่องมือ replay ส่วนคอลัมน์ที่อ้างถึงใช้ชื่อตาม [แบบจำลองข้อมูล](../data-model.md)

### เมื่อใดปฏิเสธทั้ง envelope

ปฏิเสธโดยไม่เขียนแถวใดและไม่สร้างการอ้างถึงไฟล์ เมื่อไม่ผ่าน JSON Schema, ไม่ผ่านเงื่อนไขข้ามฟิลด์ในรายการข้างบน, `source_kind` ไม่ใช่ `fixture`, `group_key` ไม่ตรงกับกลุ่มของ context ที่เรียก, grant ของ context ไม่มีสิทธิ์ `capture` หรือถูกเพิกถอน, หรือไฟล์ใน `assets` ไม่ตรง SHA-256 ขนาด MIME หรือ `staging_key` ไม่เป็น opaque key

หลักฐานที่ `role` ไม่ตรงกับ role ของคอมเมนต์ที่อ้าง หรือ `actor_key` ไม่ตรงผู้ขาย **ไม่ใช่เหตุปฏิเสธ** ให้บันทึก observation และหลักฐานตามที่ได้รับ งานตัดสินสถานะ (A4) เป็นผู้กันหลักฐานนั้นออกจากการยืนยันและบันทึกสาเหตุตาม SRS ข้อ 4 เพราะ FR-05 กำหนดให้เก็บ observation ทุกครั้งที่ตรวจสำเร็จ

### การแปลง v1 ลงตาราง

| ข้อมูลใน envelope | ผลในฐานข้อมูล |
|---|---|
| ทั้ง envelope | `payload_hash` คือ SHA-256 ของ JSON ที่เรียงคีย์ ไม่มีช่องว่าง เข้ารหัส UTF-8 `normalized_json` เก็บ envelope ทั้งชุด `parser_version` เป็นค่าคงที่ของตัวแปลงฝั่ง backend เช่น `ingest-v1.0` และเปลี่ยนเมื่อกฎแปลงเปลี่ยน |
| `capture_id` | ส่งซ้ำด้วย payload_hash เดิมคืน observation เดิมโดยไม่เพิ่มแถว รวมถึงกรณีสองคำขอเข้ามาพร้อมกัน payload_hash ต่างกันเป็น conflict |
| `run_id`, `job_kind`, `page_check` | `capture_runs.id = run_id` ครั้งแรกสร้าง job คู่กัน `session_result`/`sort_result` มาจาก `page_check` run เดิมที่ผูก grant หรือกลุ่มอื่นถูกปฏิเสธ |
| `posted_at` | `posts.posted_at` และ `observations.source_time` ใช้ `value` `source_time_precision` ใช้ `precision` หรือ `unknown` เมื่อไม่มี `posted_at` ข้อความต้นทางคงอยู่ใน `normalized_json` |
| `activity` | สามยอดและ `coverage` ลงคอลัมน์ของ observation เป็น NULL ทั้งหมดเมื่อ payload ไม่มี `activity` |
| `seller` | สร้าง seller เฉพาะเมื่อมี `external_actor_key` ชื่อแสดงผลไม่เก็บในตาราง `sellers` เพราะเป็นข้อมูลที่สังเกตตาม grant ให้อ่านจาก observation |
| `items[]` ที่มี `source_item_key` | หา/สร้าง `listing_items` ด้วย UNIQUE(post_id, source_item_key) `resolution = keyed`, `unit` มาจาก `price.unit` หรือ `unknown` เมื่อไม่มีราคา `model_key`, `vram_gb` และ `condition` มาจากกฎสกัดตาม [สัญญากฎสกัด](extraction-and-tags.md) |
| `items[]` ที่ `source_item_key` เป็น null | ระยะ A2 ไม่สร้างแถว `listing_items` ข้อมูลอยู่ใน `normalized_json` การจับคู่ข้ามรอบเป็น `unresolved` หรือ `bundle` เป็นงาน A4 |
| `comments[]` | คอมเมนต์ที่มี key ใช้แถวเดิมของโพสต์ คอมเมนต์ที่ไม่มี key สร้างแถวใหม่ทุก observation `parent_key` ผูก `parent_id` เมื่อพบ parent ในโพสต์เดียวกัน ทุกคอมเมนต์ได้ `comment_versions` หนึ่งแถวต่อ observation พร้อม `body_hash` (SHA-256 ของข้อความ) และ `source_time` จาก `value` |
| `evidence[]` | `captured_at` ใช้ `observed_at` ของ payload `item_id` ใส่เมื่อมีชิ้นที่มี key อ้างหลักฐานนั้นเพียงชิ้นเดียว span ของ `comment_text` ผูก `comment_version_id` ของ observation เดียวกัน `media_asset_id` มีค่าเมื่อ `media_state = captured` เท่านั้น |
| `assets` | ตรวจไฟล์ทั้งหมดก่อนเขียน เขียนแบบไฟล์ชั่วคราวแล้ว rename ใช้ SHA-256 เป็น storage key ไฟล์เดียวกันใช้ `media_assets` แถวเดียว แต่ละ evidence ตรวจสิทธิ์ของตนเอง |

ทุกแถวของหนึ่ง envelope อยู่ในธุรกรรมเดียว ตรวจ grant ตอนเริ่ม และตรวจซ้ำด้วย `SELECT … FOR SHARE` บนแถว grant ก่อน commit หากการเพิกถอน commit ไปก่อนจุดนั้นให้ rollback ทั้งหมด (AT-09) ไฟล์ที่เขียนไปแล้วเป็น orphan ที่งานกวาดลบได้หลังพ้นช่วงผ่อนผัน

### context สำหรับอ่านในระยะ A

ระยะ A ยังไม่มีสัญญายืนยันตัวตนสำหรับการใช้งานจริง แนวทางที่เจ้าของเลือกอยู่ใน [ADR 0004](../decisions/0004-local-access-and-mcp.md) ซึ่งรับรองแล้ว (งาน session ยังเป็นงานความเสี่ยงสูงที่ต้องอนุมัติแยก) API อ่านข้อมูลภายใต้ fixture read context หนึ่งชุด คือ group และ grant ที่ server กำหนดจากการตั้งค่าฝั่ง server หรือ test harness ห้ามรับกลุ่ม grant หรือ path จาก header, query หรือ body ของคำขอ การตรวจขอบเขตอยู่ใน service layer ที่ API และ MCP ใช้ร่วมกัน ไม่ผูกไว้กับ dependency ของ FastAPI เพียงอย่างเดียว

### รูปแบบ response

ตัวอย่าง response ใช้รั้วโค้ด `jsonc` เพื่อให้ `check_contracts.py` ซึ่งตรวจทุกบล็อก `json` กับ schema ของ payload ข้ามบล็อกเหล่านี้

`GET /v1/posts/{post_id}` คืนโพสต์และ observation ทั้งหมดของ grant ใน context เรียงตาม `observed_at` แล้ว `id` จากเก่าไปใหม่ `items` เป็น snapshot ของ observation นั้น ไม่ใช่สถานะที่ระบบตัดสิน ซึ่ง A4 จะเพิ่มในภายหลัง

```jsonc
{
  "id": "6f1c2a1e-0000-4000-8000-000000000001",
  "group_id": "6f1c2a1e-0000-4000-8000-000000000002",
  "source_post_key": "demo-42",
  "permalink": "https://www.facebook.com/groups/demo-group/posts/42/",
  "seller_id": null,
  "posted_at": null,
  "observations": [
    {
      "id": "6f1c2a1e-0000-4000-8000-000000000003",
      "run_id": "6f1c2a1e-0000-4000-8000-000000000004",
      "capture_id": "demo-capture-1",
      "observed_at": "2026-11-06T03:00:00Z",
      "access": "ok",
      "activity": {"source_reported_comment_count": 5, "distinct_comments_seen": 2,
                   "distinct_known_commenters_seen": 1, "coverage": "partial"},
      "items": [
        {"item_id": "6f1c2a1e-0000-4000-8000-000000000005", "source_item_key": "sku-1",
         "model": "RTX 3060 12GB", "condition_text": null, "source_status_label": null,
         "price": {"kind": "ask", "unit": "each", "amount": "5500.00", "currency": "THB"},
         "evidence_ids": ["6f1c2a1e-0000-4000-8000-000000000006"]}
      ],
      "evidence_ids": ["6f1c2a1e-0000-4000-8000-000000000006"]
    }
  ]
}
```

`item_id` เป็น null สำหรับรายการที่ไม่มี `source_item_key` `items[].evidence_ids` แปลง `evidence_keys` เป็น UUID ของหลักฐานใน observation เดียวกัน ฟิลด์ที่ payload ไม่ส่งมาให้คืนเป็น null ห้ามแทนด้วยศูนย์

`GET /v1/evidence/{evidence_id}` คืน metadata และลิงก์รูปเมื่อมีภาพ

```jsonc
{
  "id": "6f1c2a1e-0000-4000-8000-000000000006",
  "post_id": "6f1c2a1e-0000-4000-8000-000000000001",
  "observation_id": "6f1c2a1e-0000-4000-8000-000000000003",
  "item_id": "6f1c2a1e-0000-4000-8000-000000000005",
  "local_key": "e1",
  "role": "seller",
  "source_span": {"field": "post_text", "quote": "RTX 3060 12GB 5500 บาท"},
  "comment_version_id": null,
  "captured_at": "2026-11-06T03:00:00Z",
  "permalink": "https://www.facebook.com/groups/demo-group/posts/42/",
  "media_state": "captured",
  "media_reason": null,
  "media": {"mime": "image/webp", "width": 640, "height": 480, "bytes": 18234,
            "sha256": "<64 hex>", "href": "/v1/evidence/6f1c2a1e-0000-4000-8000-000000000006/media"}
}
```

`media` เป็น null เมื่อ `media_state` ไม่ใช่ `captured` และ `media_reason` อธิบายเหตุผลตาม FR-07

`GET /v1/evidence/{evidence_id}/media` คืนไบต์ของรูปพร้อม `Content-Type` ตาม `mime`, `Cache-Control: no-store` และ `X-Content-Type-Options: nosniff` ก่อนส่งให้ตรวจ SHA-256 ของไฟล์กับ `media_assets.sha256` และต้องใช้ grant ที่มีสิทธิ์ `media`

### ข้อผิดพลาด

ข้อผิดพลาดทุกแบบคืน `{"detail": {"code": "<code>", "message": "<ข้อความ>"}}` ยกเว้น 422 ที่ใช้รูปแบบมาตรฐานของ FastAPI

| HTTP | `code` | กรณี |
|---|---|---|
| 401 | `read_context_required` | server ไม่ได้กำหนด read context |
| 403 | `grant_unavailable` | grant ของ context ถูกเพิกถอน หมดอายุ หรือไม่มีสิทธิ์ `read`/`media` ที่ต้องใช้ |
| 404 | `not_found` | ไม่มี ID หรือ ID อยู่นอก group/grant ของ context ตอบแบบเดียวกันทั้งสองกรณี |
| 404 | `media_not_available` | หลักฐานอยู่ใน scope แต่ `media_state` ไม่ใช่ `captured` |
| 422 | — | path parameter ไม่ใช่ UUID |
| 500 | `media_integrity_failure` | ไฟล์ของหลักฐานที่ `captured` หายหรือ hash ไม่ตรง ไม่ส่งไบต์ใด ๆ |
| 503 | `media_storage_unavailable` | server ไม่ได้ตั้งที่เก็บรูป |

## เมื่อสัญญาเปลี่ยน

เมื่อเปลี่ยนความหมายของฟิลด์ เวอร์ชันข้อมูล คำสั่ง API หรือขอบเขตสิทธิ์ ให้อัปเดตตัวอย่างและเกณฑ์รับมอบที่เกี่ยวข้องใน PR เดียวกัน เมื่อมี FastAPI ที่รันได้ ให้เชื่อม OpenAPI ที่สร้างจากโค้ดสำหรับ HTTP path, method และ schema แล้วเก็บความหมายของข้อมูล กฎสิทธิ์ และตัวอย่างข้ามระบบไว้ในหน้านี้

การยืนยันสัญญาต้องมีผลทดสอบที่อ้างถึง commit และ fixture ที่ใช้ บันทึกลิงก์ผลตามข้อกำหนดใน [ตารางตรวจรับ](../../quality/TRACEABILITY.md)
