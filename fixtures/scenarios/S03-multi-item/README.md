# S03 — โพสต์มีสองชิ้น แต่จองเฉพาะ 3060 (AT-03)

`01-open-both.json` ผู้ขายเปิด 3060 (5,500 บาท) และ 3070 (7,900 บาท) คนละ `source_item_key` เริ่มด้วย open ทั้งคู่

`02-seller-reserves-3060.json` มี **ผู้ซื้อ** พูด “จองครับ” (role=other, actor_key ของผู้ซื้อ) และ **ผู้ขายที่ตรวจ identity ได้** พูด “3060 ติดจองแล้ว 3070 ยังขายอยู่” (role=seller, actor_key ตรงกับ seller ในโพสต์) จึงยืนยัน reserved เฉพาะ `sku3060` ส่วน `sku3070` ยังคง open ไม่มี event จองของ 3070

เฉลยใช้หลักฐาน `s03-02-3060` และ `s03-02-3070`; `s03-buyer-text` เป็นเพียง activity ไม่ใช่ seller confirmation

อ้างอิง [SRS §4](../../../docs/requirements/SRS.md), [AT-03](../../../docs/requirements/SRS.md), และ [collector contract](../../../docs/architecture/contracts/collector-and-query.md) เรื่อง comment role, actor และ evidence scope
