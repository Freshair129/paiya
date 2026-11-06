# S01 — เปิดขาย → จอง → เปิดใหม่ และ retry (AT-08)

ข้อมูลสร้างขึ้นเพื่อทดสอบเท่านั้น ใช้ permalink โดเมน `example.invalid` ที่ไม่ใช่โพสต์จริง

1. `01-open.json` ผู้ขายสมมติระบุชิ้น `sku3060` ว่าเปิดขาย เวลา 09:00 UTC
2. `02-reserved.json` ผู้ขายคนเดิมยืนยันว่า “3060 ติดจองแล้ว” เวลา 09:30 UTC
3. `03-reopened.json` ผู้ขายบอก “3060 หลุดจอง กลับมาเปิดขาย” เวลา 10:00 UTC
4. run ที่สองส่ง `02-reserved.json` ซ้ำหลังรอบสามโดย **ใช้ capture_id เดิม** ผลยังมี 3 observations และประวัติ open → reserved → open

**เฉลย** current_status=open, ask=5500 THB, status_evidence=`03-reopened.json/s01-reopened` ไม่ใช่หลักฐานของงานที่ retry

อ้างอิง [SRS §4](../../../docs/requirements/SRS.md) กฎ A→B→A และไม่สร้าง event ซ้ำ, [SRS AT-08](../../../docs/requirements/SRS.md) รวมถึง [data model §4](../../../docs/architecture/data-model.md) เรื่อง sale_events
