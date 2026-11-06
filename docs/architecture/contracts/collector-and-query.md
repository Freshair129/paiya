# สัญญาข้อมูลของ Collector และช่องทางค้นคืน

**สถานะ** แบบที่ใช้อ้างอิงสำหรับ MVP · รอยืนยันด้วย fixture · **ผู้ดูแล** ผู้พัฒนา Collector และ API ร่วมกัน

หน้านี้กำหนดรูปแบบข้อมูลกลางที่ Collector ส่งเข้า backend และสิ่งที่แอปกับ Agent เรียกอ่านได้ รูปแบบที่เสนอรองรับประวัติการตรวจ (FR-05) หลักฐาน (FR-07) การค้นผ่าน MCP (FR-11) และการตรวจสิทธิ์ (FR-13) ดูข้อกำหนดทั้งหมดและเกณฑ์รับมอบใน [SRS](../../requirements/SRS.md)

## ข้อมูลจาก Collector

ตัวอย่างย่อของ normalized JSON จาก fixture กำหนด source_post_key และ source_item_key คงที่ข้ามรอบ ราคาเป็นเลขฐานสิบที่ระบุสกุลเงินชัดเจน

```json
{
  "schema_version": 1,
  "group_key": "demo-group",
  "source_post_key": "demo-42",
  "permalink": "https://www.facebook.com/groups/demo-group/posts/42/",
  "observed_at": "2026-11-06T03:00:00Z",
  "seller": {"identity_scope": "demo-group", "external_actor_key": "demo-s7"},
  "items": [{"source_item_key": "sku-1", "model": "RTX 3060 12GB",
             "price": {"kind": "ask", "amount": "5500.00", "currency": "THB"}}],
  "activity": {"reported_count": 5, "distinct_comments_seen": 2,
               "distinct_known_commenters_seen": 1, "coverage": "partial"},
  "evidence": [{"local_key": "e1", "role": "seller",
                "text_span": "RTX 3060 12GB 5500 บาท", "media_state": "captured"}]
}
```

Envelope ภายในเพิ่ม run_id, capture_id, grant_id, ผลการตรวจหน้าที่มา และ source_kind เป็น fixture หรือ facebook_live และ mapping จาก local_key ไปยัง asset ชั่วคราว Backend ตรวจ schema และสิทธิ์ซ้ำก่อนสร้าง UUID และ FK จริง เมื่อเขียนไฟล์ล้มเหลวให้เก็บเหตุผลและ retry เฉพาะ media งานเก็บไฟล์ต้องเขียนไฟล์ชั่วคราวแล้ว rename แบบ atomic พร้อมกวาด orphan หลัง recovery

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
