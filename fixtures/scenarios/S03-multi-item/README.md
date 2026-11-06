# S03 — ข้อความผู้ซื้อไม่ทำให้จอง และผู้ขายจองเฉพาะ 3060 (AT-03)

ผู้ขายสมมติขาย RTX 3060 (5,500 บาท) และ RTX 3070 (7,900 บาท) ในโพสต์เดียว แต่ใช้ `source_item_key` คนละค่า

1. **`01-open-both.json`** เวลา 09:00 UTC ผู้ขายเปิดขาย 3060 และ 3070 ทั้งสองรายการ
2. **`02-buyer-only.json`** เวลา 09:15 UTC ผู้สนใจพิมพ์ “จองครับ” (`role=other`, `actor_key=buyer-s03`) โดยยังไม่มีคำยืนยันของผู้ขาย เฉลย: `sku3060=open`, `sku3070=open`, ไม่มี reserved/sold event และใช้ status evidence จากรอบแรก
3. **`03-seller-reserves-3060.json`** เวลา 09:30 UTC ผู้ขายที่มี stable identity ตรงกับ seller ของโพสต์กล่าว “3060 ติดจองแล้ว 3070 ยังขายอยู่” เฉลย: `sku3060=reserved`, `sku3070=open` และไม่มี event จองของ 3070

`expected.json` มี **สอง runs**: (a) replay เฉพาะรอบ 1–2 เพื่อพิสูจน์ว่าข้อความผู้ซื้อไม่เปลี่ยนสถานะสินค้า และ (b) replay รอบ 1–3 เพื่อพิสูจน์ว่าคำยืนยันผู้ขายเปลี่ยนเฉพาะชิ้นที่กล่าวถึง

หลักฐาน `s03-buyer-text` เป็นเพียง activity ไม่มีการอ้างเป็น `status_evidence` ส่วนคำยืนยันการจองใช้ `s03-02-3060` ของรอบ 3 ซึ่งผูกกับ comment ของผู้ขายโดยตรง

อ้างอิง [SRS §4](../../../docs/requirements/SRS.md), [AT-03](../../../docs/requirements/SRS.md), [collector contract](../../../docs/architecture/contracts/collector-and-query.md) เรื่อง comment role, stable actor identity และ evidence scope
