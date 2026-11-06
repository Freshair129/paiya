# S02 — รอบมาช้าแต่ไม่ทับสถานะปัจจุบัน (AT-08)

สามรอบของโพสต์จำลองเดียวกัน: O1 open ที่ 09:00, O2 reserved ที่ 09:30 และ O3 open ที่ 10:00 UTC

**ลำดับส่งเข้า** O1 → O3 → O2 (โดย O2 มาถึงหลังสุด) ต้องเก็บครบสาม observations แต่สร้าง timeline **เรียงเวลาหลักฐาน** ได้ open → reserved → open และ current_status ยังคง open โดยอ้าง `03-open.json/s02-open3` ไม่ย้อนไป reserved

อ้างอิง [SRS §4](../../../docs/requirements/SRS.md) และ [AT-08](../../../docs/requirements/SRS.md): เรียง status_facts ตาม effective_at, observed_at, observation_id และไม่สร้างแจ้งเตือนจากข้อมูลเก่าที่ไม่เปลี่ยนสถานะปัจจุบัน; [data model §4](../../../docs/architecture/data-model.md)
