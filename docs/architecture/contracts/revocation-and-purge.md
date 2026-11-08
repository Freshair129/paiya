# สัญญาการเพิกถอน งานลบ และการซ้อมกู้คืน (A8)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A8 ตามการอนุมัติของเจ้าของใน [Issue #64](https://github.com/Freshair129/paiya/issues/64) (ความเสี่ยงสูง ขอบเขต H1–H3 ข้อ a) · **ผู้ดูแล** เจ้าของ Worker และฐานข้อมูล

หน้านี้กำหนดคำสั่งเพิกถอน grant งานลบตามขอบเขต การสร้างข้อมูลอนุพันธ์ใหม่ และการ replay คำสั่งลบหลังกู้ backup ใช้คู่กับ [SRS](../../requirements/SRS.md) ข้อ 6, FR-13, AT-09, [แบบจำลองข้อมูล](../data-model.md) ข้อ 6, [สัญญาสถานะรายชิ้น](item-status-and-watches.md), [สัญญากฎสกัด](extraction-and-tags.md) ข้อ 6 และ [สัญญา MCP](mcp-read.md)

## 1. ขอบเขตและข้อห้าม

- สั่งได้ผ่าน CLI เท่านั้น (H1) ไม่มี HTTP endpoint สำหรับเพิกถอนหรือลบในระยะ A
- ทุกคำสั่งรับได้เฉพาะ grant หรือโพสต์ของกลุ่มที่ `group_key` ขึ้นต้นด้วย `synthetic-` ขอบเขตอื่นตอบ `scope_not_permitted` และไม่เปลี่ยนข้อมูล
- ไม่มีคำสั่งใดเปิดเครือข่าย
- ผลของคำสั่งพิมพ์เป็น JSON บรรทัดเดียว และเก็บเฉพาะ id, scope, เวลา และจำนวนแถว ไม่พิมพ์หรือเก็บข้อความประกาศ

## 2. การเพิกถอน (A8a)

`uv run python -m paiya.cli grant revoke <grant_id>`

1. ใน transaction เดียว: lock แถว `source_grants` แบบ `FOR UPDATE` ตั้ง `revoked_at = now` ถ้ายังว่าง แล้วตั้งงาน `queued` และ `leased` ทุกชนิดของ grant นั้นเป็น `cancelled` และล้าง lease
2. การ lock นี้ชนกับ `FOR SHARE` ที่ ingest, collector และ MCP ใช้ตอน commit
   - ถ้า commit ของงานอื่นถือ lock อยู่ก่อน การเพิกถอนรอให้ commit นั้นเสร็จ
   - หลังการเพิกถอน commit ใหม่ทุกครั้งล้มด้วย `grant_unavailable` และไม่มีแถวใหม่
3. grant ที่เพิกถอนแล้วสั่งซ้ำได้ ไม่เปลี่ยน `revoked_at` เดิม และพิมพ์ `already_revoked`
4. หลังเพิกถอน
   - API ตอบ 403 `grant_unavailable` ทุก endpoint รวม `/media` ของหลักฐานที่เคยเปิด
   - MCP ตอบ `grant_unavailable`
   - worker และ `enrich run` ไม่จองงานของ grant นั้น
   - ข้อมูลยังอยู่ในฐานจนกว่าจะสั่งลบตามข้อ 3

## 3. งานลบ (A8b)

`uv run python -m paiya.cli purge run --scope grant:<grant_id>` หรือ `--scope post:<post_id>` (H2)

**ลำดับ**
1. ตรวจขอบเขตตามข้อ 1 ขอบเขต `grant` ต้องเพิกถอนแล้ว มิฉะนั้นตอบ `grant_not_revoked` ขอบเขตที่ไม่มีอยู่ตอบ `scope_not_found`
2. เพิ่มบรรทัดในบันทึกคำสั่งลบตามข้อ 4 และ fsync **ก่อน** แก้ฐานข้อมูล
3. สร้างแถว `purge_jobs` (`id` เดียวกับในบันทึก) สถานะ `running`
4. ใน transaction เดียว lock แถว `posts` ที่อยู่ในขอบเขตแบบ `FOR UPDATE` แล้วลบตามตาราง
5. commit แล้วลบไฟล์ภาพ
6. ตั้ง `purge_jobs` เป็น `succeeded` พร้อม `result_counts`

**ข้อมูลที่ลบ** ขอบเขต `grant` ใช้ observation ของ grant นั้น ส่วนขอบเขต `post` ใช้ทุก observation ของโพสต์นั้นทุก grant

| ข้อมูล | การจัดการ |
|---|---|
| `tags`, `price_facts`, `status_facts`, `evidence`, `comment_versions` ของ observation ในขอบเขต | ลบ |
| `observations` ในขอบเขต | ลบ |
| `jobs`, `capture_runs`, `capture_gaps` ของ grant (ขอบเขต grant) | ลบ ส่วนแถว `source_grants` คงไว้เป็นบันทึกสิทธิ์ที่เพิกถอนแล้ว |
| `comments` ที่ไม่เหลือ version | ลบ |
| `listing_items` ที่ไม่ปรากฏใน observation ที่เหลือของโพสต์ | ลบพร้อม `sale_events`, `current_items` และ `watches` ของชิ้นนั้น |
| `posts` ที่ไม่เหลือ observation | ลบพร้อมชิ้น คอมเมนต์ และ watch ของโพสต์ |
| `sellers` ที่ไม่เหลือโพสต์ | ลบ |
| `media_assets` ที่ไม่เหลือ evidence อ้างถึง | ลบแถวใน transaction แล้วลบไฟล์หลัง commit |

**สร้างใหม่ใน transaction เดียวกัน** สำหรับทุกชิ้นที่ยังเหลือในโพสต์ที่กระทบ:
- สร้าง `sale_events` ใหม่ทั้งชุดจาก `status_facts` ที่เหลือ
- คำนวณ `current_items` ใหม่ตามสัญญาสถานะรายชิ้น ข้อ 3
- คำนวณค่าใน `listing_items` ตามสัญญากฎสกัด ข้อ 6
- นำ watch ของโพสต์หรือชิ้นที่ไม่เหลือหลักฐานออก

สถิติราคา หน้าผู้ขาย และ `/v1/models` คำนวณตอนอ่าน จึงไม่นับข้อมูลที่ลบเองโดยไม่ต้องมีขั้นเพิ่ม

**ไฟล์** ลบไฟล์ไม่สำเร็จ (เช่น ถูกล็อก) ไม่ทำให้ transaction ย้อน ให้นับใน `result_counts.files_pending` และตั้งสถานะ `succeeded`
- สั่ง `purge run` ด้วยขอบเขตเดิมซ้ำจะลองลบไฟล์ที่ค้างอีกครั้ง โดยใช้รายการ storage key ที่เก็บใน `result_counts.pending_storage_keys`
- ค่าในรายการเป็น opaque key ไม่ใช่เนื้อหา

**ซ้ำและล้มเหลว**
- ลบขอบเขตเดิมซ้ำหลังสำเร็จ ได้ `purge_jobs` ใหม่ที่จำนวนแถวเป็นศูนย์ และไม่มีผลข้างเคียง
- ถ้า transaction ล้ม `purge_jobs` เป็น `failed` พร้อมเหตุ ไม่มีแถวใดถูกลบ สั่งซ้ำได้

**`purge_jobs`** (migration ใหม่ เพิ่มตารางอย่างเดียว) คอลัมน์ `id uuid`, `scope_kind` (`grant`, `post`), `scope_key text`, `requested_at`, `state` (`running`, `succeeded`, `failed`), `completed_at?`, `reason?`, `result_counts jsonb` downgrade ปฏิเสธเมื่อตารางมีแถว แทนการลบ

## 4. บันทึกคำสั่งลบและการ replay หลังกู้คืน (A8c)

- `PAIYA_PURGE_LOG_PATH` เป็นพาธไฟล์ JSONL ต้องตั้งค่าทุกครั้งที่สั่ง `purge run` ต้องอยู่นอก `PAIYA_FIXTURE_MEDIA_ROOT` และนอกโฟลเดอร์ข้อมูล ตาม H3 และ SRS ข้อ 6 ค่าที่ไม่ได้ตั้งหรืออยู่ในโฟลเดอร์ข้อมูลทำให้คำสั่งปฏิเสธ
- หนึ่งบรรทัดต่อคำสั่ง `{"id": "<uuid>", "scope_kind": "grant", "scope_key": "<id>", "requested_at": "<UTC>"}` เพิ่มต่อท้ายเท่านั้น
- `paiya.serve`, `python -m paiya.mcp`, worker และ `enrich run` ตอนเริ่มทำงานอ่านบันทึกเมื่อตั้งค่าไว้
  - ถ้ามี id ใดที่ฐานยังไม่มี `purge_jobs` สถานะ `succeeded` ให้ไม่เริ่มทำงาน และแจ้งให้รัน `purge replay`
  - กรณีนี้เกิดหลังกู้ backup ที่เก่ากว่าคำสั่งลบ
- `uv run python -m paiya.cli purge replay` ทำคำสั่งในบันทึกที่ยังไม่สำเร็จตามลำดับ
  - ใช้ id เดิมจากบันทึก ไม่เพิ่มบรรทัดใหม่
  - ขอบเขต `grant` ที่ grant ในชุดกู้คืนยังไม่ถูกเพิกถอน ให้เพิกถอนก่อนตามข้อ 2 ภายในคำสั่งนี้
- **ซ้อมกู้คืนในเทสต์**
  1. snapshot ทุกตารางของ schema ทดสอบ (COPY ออกไปไฟล์ใน tempfile) และสำเนาคลังภาพชั่วคราว
  2. ลบตามข้อ 3
  3. คืนตารางและไฟล์จาก snapshot
  4. ตรวจว่า server ไม่เริ่มทำงาน
  5. รัน `purge replay`
  6. ตรวจว่าค้นข้อมูลที่ลบไม่เจอผ่าน API และ MCP และไฟล์ภาพที่ไม่เหลือ reference ถูกลบ
- การ backup/restore จริงด้วยเครื่องมือของ PostgreSQL เป็นงาน O1 ระยะ C

## 5. เทสต์ที่ต้องมี (AT-09)

**ลำดับเหตุการณ์ของการเพิกถอน**
- เพิกถอนระหว่าง capture ที่ถือ lock อยู่ แล้วการเพิกถอนต้องรอ
- เพิกถอนก่อน commit แล้ว commit ต้องล้ม

**หลังเพิกถอน**
- คิวหยุด
- API, MCP และ `/media` ปฏิเสธ

**หลังลบ**
- ข้อมูลที่ลบไม่ถูกนับใน trends, หน้าผู้ขาย และ `/v1/models`
- ชิ้นในโพสต์ที่ยังมี grant อื่นคงอยู่ และถูกสร้างใหม่จากข้อมูลที่เหลือ
- ไฟล์ภาพที่ยังมี reference อื่นคงอยู่

**กรณีล้มเหลว**
- ลบซ้ำ
- ขอบเขตไม่มีอยู่
- ขอบเขตไม่ใช่ `synthetic-`
- ลบ grant ที่ยังไม่เพิกถอน
- ไม่ตั้ง `PAIYA_PURGE_LOG_PATH`
- ไฟล์ลบไม่สำเร็จ
- transaction ล้มกลางทาง

**กู้คืน:** ซ้อมกู้คืนตามข้อ 4
