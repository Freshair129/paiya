# S04 — ต้นทางรายงาน 5 คอมเมนต์ โหลดได้ 2 (AT-04)

โพสต์สมมติแสดงยอดรวม **5** แต่โหลดข้อความได้เพียง **2** ทั้งสองข้อความเป็น stable actor คนเดียวกัน จึงมี distinct_known_commenters_seen=**1** และ coverage=**partial** ไม่ใช่ 5 คนหรือ 5 comments ที่อ่านได้

ราคาตั้งขายของ `sku3070` คือ 7,200 THB แต่ข้อความไม่มีคำยืนยันสถานะ จึงเป็น **unknown** พร้อม status_history ว่างและ status_evidence=null; ห้ามสร้าง event จองหรือขายจากช่องว่างของ comment

อ้างอิง [SRS §4](../../../docs/requirements/SRS.md) และ [AT-04](../../../docs/requirements/SRS.md): เก็บ reported, seen, known แยกกันโดยไม่สมมติผู้ใช้ที่มองไม่เห็น
