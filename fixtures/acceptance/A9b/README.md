# A9b — ชุดข้อมูลสังเคราะห์สำหรับ AT-02 และ AT-03

## จุดประสงค์

ชุดนี้ร่าง fixture และป้ายกำกับสังเคราะห์สำหรับทบทวน AT-02 (การค้นพบ stable posts ในขอบเขตรอบ) และ AT-03 (การผูก seller/post/item และการตัดสินสถานะรายชิ้น) ป้ายใน labels.json เป็น agent-draft; ยังไม่ใช่ answer key จนกว่าเจ้าของงานจะตรวจและยืนยัน

## หน่วยของกรณี

- Discovery หนึ่งแถวคือการ์ดหนึ่งใบใน feed หนึ่งไฟล์ โดย run_label และ card_index ชี้ตำแหน่งใน feed.json; post_key เป็น null เฉพาะการ์ดที่แยก stable key ไม่ได้
- Status หนึ่งแถวคือ item หนึ่งรายการใน post history โดยดู observation รอบสุดท้าย; status ใช้คำศัพท์ open, reserved, sold และ unknown
- AT-02 นับ stable posts ที่ติดป้าย visible ใน scope; รายงานตัวเศษ/ตัวหารต่อรอบและรวมรอบ และแยก coverage ของ seller ที่มี stable ID ตาม SRS
- ชุด sold control นับ clear_sold_evidence=true และ expected_status=sold; A9b ไม่ติดป้ายความถูกต้องของราคา

## จำนวนกรณีร่าง

| หมวด | จำนวน |
|---|---:|
| Discovery label rows | 52 |
| Discovery feeds | 8 |
| Discovery visible=true | 36 |
| Discovery readable=false | 11 |
| Status label rows | 60 |
| Status post histories | 37 |
| Clear sold controls | 23 |
| Expected unknown | 9 |
| Status rows with stable seller ID | 57 |
| Status rows without stable seller ID | 3 |
| รวม label rows | 112 |
| กรณี ambiguous รอคำตอบเจ้าของ | 3 |

มีกรณีกับดักอย่างน้อยสามแถวต่อชนิด: buyer comment role mismatch, seller actor mismatch, closing evidence link missing, two items with one sold, reserved-to-open without evidence, reserved-to-open with seller evidence และ seller without stable ID

## ข้อที่รอเจ้าของยืนยัน

- SRS §7 AT-03 ระบุว่า “ลิงก์ขาดต้องไม่ทำให้ปิดขาย” แต่สัญญาสถานะไม่ได้กำหนด URL แยกบน evidence; T17–T19 จึงร่างเป็น evidence ที่มีอยู่แต่ item ไม่ได้ผูกผ่าน evidence_keys และติด ambiguous ไว้ให้ยืนยัน
- SRS §7 ระบุว่า “รายงาน coverage ของผู้ขายที่มี stable ID แยกต่างหาก” โดยไม่กำหนดสูตรตัวเศษ/ตัวหารในข้อความนี้; fixture จึงเก็บ seller_has_stable_id เพื่อแยกกลุ่มรายงาน ส่วนสูตร coverage ต้องอ้างคำตัดสินของเจ้าของ

## วิธีเจ้าของยืนยัน

ตรวจทุกแถวใน REVIEW.md เทียบกับ fixture และ SRS/สัญญา จากนั้นยืนยันหรือแก้ labels.json เมื่อยืนยันแล้วให้คำนวณ SHA-256 ของ fixtures/acceptance/A9b/labels.json และโพสต์ค่า hash พร้อมการยืนยันใน Issue #72 ผลต้องคงเป็น “labels pending owner review” จนกว่าจะมีการยืนยัน; ห้ามเปลี่ยน label_status จาก agent-draft ก่อนผู้ดูแลอนุมัติ

บน Windows คำนวณ hash ด้วยคำสั่ง Get-FileHash fixtures/acceptance/A9b/labels.json -Algorithm SHA256
