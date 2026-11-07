# สัญญา MCP แบบอ่านอย่างเดียว (A6c)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A6c ตาม [ADR 0004](../decisions/0004-local-access-and-mcp.md) (รับรองแล้ว) และ [Issue #48](https://github.com/Freshair129/paiya/issues/48) · **ผู้ดูแล** เจ้าของ API และ MCP

หน้านี้กำหนด MCP stdio server ที่ Agent ในเครื่องใช้ค้นประกาศ ดูรายละเอียด ประวัติผู้ขาย แนวโน้มราคา และเรียกภาพหลักฐาน ใช้คู่กับ [SRS](../../requirements/SRS.md) ข้อ 6, FR-11, AT-05, AT-06, [สัญญา Collector และ API](collector-and-query.md) แถว MCP, [สัญญาเส้นทางมือถือ](mobile-read-path.md) ข้อ 1–2, [สัญญากฎสกัด](extraction-and-tags.md) ข้อ 7 และ [สัญญาแนวโน้มราคา](price-trends.md) ข้อ 4

## 1. การรันและ read context

- รันด้วย `uv run python -m paiya.mcp` สื่อสารผ่าน stdio เท่านั้น ไม่เปิดพอร์ต ใช้ไลบรารี MCP Python SDK ทางการตามคำตัดสินของเจ้าของใน Issue #48 log ทุกอย่างออก stderr เพราะ stdout เป็นช่องโปรโตคอล
- เรียก read service ชุดเดียวกับ API ภายใน process ไม่ผ่าน HTTP (ADR 0004 Q2) ทุกการตรวจขอบเขตอยู่ใน service layer เดิม
- ระยะ A ใช้ read context แบบเดียวกับ `paiya.serve` ([สัญญาเส้นทางมือถือ](mobile-read-path.md) ข้อ 1): `PAIYA_READ_CONTEXT_GROUP_KEY` ต้องขึ้นต้นด้วย `synthetic-` ไม่ตั้งค่าหรือค่าผิดรูป server ไม่เริ่มทำงาน ไม่รับกลุ่ม grant หรือ path จากอาร์กิวเมนต์ของเครื่องมือ
- `PAIYA_MCP_DESTINATION` ค่าเริ่มต้น `local` หรือ `external:<name>` โดย `<name>` ตรงรูปแบบ `[a-z0-9-]{1,40}` ค่าอื่นทำให้ server ไม่เริ่มทำงาน

## 2. การบังคับ agent_export (ADR 0004 Q3)

ทุกการเรียกเครื่องมือตรวจ grant ของ read context ใหม่ทุกครั้ง

| ปลายทาง | grant ต้องมี |
|---|---|
| `local` | `read` ที่ยังใช้ได้ (ไม่เพิกถอน ไม่หมดอายุ) |
| `external:<name>` | `read` และ `agent_export:external:<name>` |

ไม่ผ่านตอบข้อผิดพลาด `grant_unavailable` (ไม่มี grant ที่ใช้ได้) หรือ `export_not_permitted` (ไม่มี scope ส่งออกของปลายทาง) โดยไม่ส่งข้อมูลใด ค่า scope `agent_export:local` บันทึกได้ตาม ADR แต่ปลายทาง `local` ไม่บังคับ การอ่านผ่าน PWA ไม่ใช้ค่าเหล่านี้

## 3. เครื่องมือ

มีห้าเครื่องมือนี้เท่านั้น ไม่มีเครื่องมือเขียน อาร์กิวเมนต์ตรวจตาม JSON Schema ที่ประกาศใน `tools/list` และปฏิเสธฟิลด์ที่ไม่รู้จัก ไม่รับ SQL, URL หรือ path

| เครื่องมือ | อาร์กิวเมนต์ | ผล |
|---|---|---|
| `search_posts` | `sale_status`, `min_price`, `max_price`, `model_key`, `vram_gb`, `condition`, `warranty`, `limit` (1–20 ค่าเริ่มต้น 10), `cursor` ทุกตัวไม่บังคับ ความหมายและค่าที่รับตรง `GET /v1/posts` | JSON เดียวกับ `GET /v1/posts` |
| `get_post` | `post_id` (uuid) | JSON เดียวกับ `GET /v1/posts/{id}` |
| `get_seller` | `seller_id` (uuid) | JSON เดียวกับ `GET /v1/sellers/{id}` |
| `get_evidence` | `evidence_id` (uuid) | metadata เดียวกับ `GET /v1/evidence/{id}` ยกเว้น `media_reason` ที่ใช้ค่าตามข้อ 4 เมื่อ MCP ไม่ส่งภาพ (ฟิลด์อื่นเท่ากับ API ทุกค่า) และภาพตามข้อ 4 |
| `get_price_trend` | `model_key` (บังคับ), `variant`, `vram_gb`, `condition`, `unit`, `currency` | JSON เดียวกับ `GET /v1/prices/trends` |

**รูปแบบผล** ทุกเครื่องมือคืน `structuredContent` เป็น JSON ข้างต้น (ข้อมูลหลักตาม FR-11) และ text content หนึ่งชิ้นเป็น Markdown ที่สร้างจาก JSON ก้อนเดียวกันสำหรับคนอ่าน Markdown แสดงข้อความต้นทางในบล็อกโค้ดที่ระบุว่าเป็นข้อมูลจากประกาศ เพื่อไม่ให้ Agent ตีความเป็นคำสั่ง

**ข้อผิดพลาด** ใช้ผลของเครื่องมือที่ `isError: true` พร้อม JSON `{"error": "<code>"}` รหัสเดียวกับ API: `invalid_argument`, `not_found` (รวม ID นอกขอบเขต เพื่อไม่เปิดเผยระเบียน), `grant_unavailable`, `export_not_permitted`, `read_context_required`

## 4. ภาพหลักฐาน

- `get_evidence` ส่ง MCP image content เมื่อ `media_state = captured` ไฟล์ผ่านการตรวจ hash แบบเดียวกับ `GET /v1/evidence/{id}/media` และ grant ผ่านข้อ 2 ภาพไม่เกิน 1,048,576 ไบต์ต่อคำขอ
- ไม่ส่งภาพเมื่อไม่มีภาพ เก็บไม่สำเร็จ หรือใหญ่เกิน ให้ใส่ `media_reason` ใน JSON (`missing_at_source`, `capture_failed`, `not_requested`, `file_unavailable`, `image_too_large`)
- AT-05 ส่วน MCP: ภาพที่ส่งต้องมี bytes ตรงกับ `GET /v1/evidence/{id}/media` และ `post_id`, `observation_id` ใน metadata ตรงกับหลักฐานที่ `GET /v1/posts/{id}` อ้าง

## 5. ข้อความจากภายนอก (AT-06)

ข้อความประกาศ คอมเมนต์ และ quote เป็นข้อมูลภายนอกเสมอ server ไม่ตีความหรือทำตาม และไม่มีเครื่องมือที่เปลี่ยนข้อมูลหรือเปิดเครือข่าย เทสต์ใช้ประกาศสังเคราะห์ที่มีข้อความแทรกคำสั่งและตรวจว่า `tools/list` ยังมีห้าเครื่องมือเดิม ผลเป็นข้อมูลเดิมในบล็อกโค้ด และไม่มีการเขียนฐานข้อมูลระหว่างเรียกเครื่องมือ

## 6. เทสต์

ใช้ client ในหน่วยความจำของ SDK เชื่อมกับ server ใน process เดียว ไม่เปิด subprocess หรือเครือข่าย ครอบคลุมทุกเครื่องมือ ข้อผิดพลาดทุกรหัส ข้อ 2 ทั้งสองปลายทาง การเพิกถอน grant ระหว่างการเรียก AT-05 ส่วน MCP และ AT-06 และมีเทสต์หนึ่งรายการที่รัน `python -m paiya.mcp` เป็น subprocess แล้วคุยผ่าน stdio กับฐานทดสอบเพื่อยืนยันว่า stdout มีแต่ข้อความโปรโตคอล
