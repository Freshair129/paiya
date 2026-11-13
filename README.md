# ป้ายยา (paiya)

แอปในเครื่องสำหรับคนหาของมือสอง ติดตามประกาศจากกลุ่มที่เลือก ดูราคาเสนอขายและสิ่งที่ผู้ขายแจ้งล่าสุด แล้วเปิดข้อความหรือภาพที่ระบบใช้ยืนยันได้จากรายการเดียวกัน

**สถานะโครงการ** อยู่ในขั้นออกแบบ MVP มี schema v1 และตัวตรวจตัวอย่างสังเคราะห์แล้ว สเปกฉบับนี้เสนอให้ผู้ซื้อส่งข้อความพร้อมลิงก์ ตั้งรับสินค้าก่อนพบดีล ติดตามรายชิ้นและเทียบยอดจ่ายบนเครื่องเดียว การนำเข้าโดยผู้ซื้อ (M) และการทดลองค้นพบโพสต์ในกลุ่มที่ได้รับสิทธิ์ (B) มีเกณฑ์รับมอบแยกกัน

| ถ้าต้องการ | เปิดอ่าน |
|---|---|
| เข้าใจผู้ใช้ ขอบเขต MVP และหน้าจอ | [ข้อกำหนดผลิตภัณฑ์](docs/product/PRD.md) |
| ดูว่าแต่ละส่วนรันที่ไหนและส่งข้อมูลอย่างไร | [ข้อกำหนดสถาปัตยกรรม](docs/architecture/SPEC.md) |
| เริ่มเขียนโค้ดจากสัญญาข้อมูล API และเกณฑ์รับมอบ | [สัญญาข้อมูลและ API](docs/architecture/contracts/collector-and-query.md) · [ข้อกำหนดระบบ](docs/requirements/SRS.md) |
| ทดลอง Collector จาก HTML สังเคราะห์และส่ง inbox | [คู่มือ Collector](collector/README.md) · [สเปก Collector](docs/architecture/external-collector.md) |
| ออกแบบฐานข้อมูล PostgreSQL | [ERD](docs/architecture/views/erd.md) · [Data model และข้อบังคับ](docs/architecture/data-model.md) |
| พัฒนาเส้นทางผู้ซื้อ | [สัญญา API ที่เสนอ](docs/architecture/contracts/buyer-workflows.md) · [ตัวอย่างตรวจรับทีละขั้น](docs/quality/buyer-acceptance.md) |
| หางานชิ้นแรกและหลักฐานที่ต้องใช้ปิดงาน | [แผนส่งมอบ](docs/delivery/ROADMAP.md) · [ตารางตรวจย้อนกลับ](docs/quality/TRACEABILITY.md) |

**สำหรับผู้ร่วมพัฒนา** ก่อนหยิบงาน อ่านวิธีเปิด Issue เลือก branch และส่งหลักฐานใน [CONTRIBUTING.md](CONTRIBUTING.md) สำหรับ Agent ให้เริ่มที่ [AGENTS.md](AGENTS.md) เอกสารและบันทึกการตัดสินใจทุกฉบับอยู่ใน [สารบัญเอกสาร](docs/README.md)
