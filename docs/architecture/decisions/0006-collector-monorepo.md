# ADR 0006 เก็บ Collector ใน Paiya monorepo

**สถานะ** รับรองแล้ว · ผู้ตัดสินใจคือเจ้าของรีโป เมื่อ 13 พฤศจิกายน 2026 · บันทึกใน [Issue #91](https://github.com/Freshair129/paiya/issues/91#issuecomment-6845809929)

## บริบท

[ADR 0005](0005-owner-directed-collection.md) กำหนดให้โค้ด Collector อยู่นอกรีโป paiya ขณะที่เจ้าของยืนยันให้ Paiya เป็นโปรเจกต์และรีโปเดียว โดย Collector เป็นส่วนหนึ่งของโปรเจกต์ ไม่ใช่รีโปคู่ขนาน การตัดสินนี้เปลี่ยนตำแหน่งโค้ดเท่านั้น

## คำตัดสิน

1. เก็บโค้ด Collector ไว้ที่ `collector/` ในรีโป `Freshair129/paiya` เป็น Python 3.12 + uv subproject มี `pyproject.toml`, lockfile, CLI, fixture, tests และ PowerShell scripts ของตัวเอง
2. Collector ยังเป็นโปรแกรมและ process ที่รันแยกจากแอป Paiya ไม่ import worker ภายใน `src/paiya/collector`, ไม่เปิดการเชื่อมต่อ PostgreSQL และส่งข้อมูลให้ Paiya ผ่าน inbox bundle v1 เท่านั้น
3. เก็บ group mapping จริง, grant mapping, browser profile, checkpoint/state, inbox, temporary files และผลรันไว้นอก repository ตามขอบเขตของสัญญา ห้าม commit credential, cookie, URL กลุ่มจริง หรือข้อมูลสมาชิก
4. การอนุมัติตาม Issue #91 ยังคงจำกัดให้พัฒนาโค้ดและตรวจด้วยข้อมูลสังเคราะห์/ออฟไลน์ การทดลองกลุ่มจริงต้องมี mapping ภายในเครื่องที่เจ้าของเลือก, active grant ที่ตรงขอบเขต, dedicated visible browser profile และการเริ่มรันโดยเจ้าของเอง
5. paiya runtime ไม่เปิดหรือส่งคำขอไปยัง Facebook การรวมโค้ดใน monorepo ไม่ได้เปลี่ยนข้อห้าม stealth, fingerprint evasion, cookie export/import, hidden endpoints หรือการอ่านนอกขอบเขต grant และไม่อนุมัติ I2 SQL ingest เพิ่มเติม

## ผลที่ตามมา

- คำสั่ง CLI ใช้ `collector/` เป็น working project ภายใน checkout เดียวกับ Paiya
- สคริปต์ทดสอบและ CI ต้องรัน Collector tests แยกจาก tests ของ `src/paiya/` และใช้ fixture ออฟไลน์
- [สเปก Collector](../external-collector.md), [สัญญา inbox](../contracts/collector-inbox.md) และ [ROADMAP](../../delivery/ROADMAP.md) อธิบายตำแหน่งใหม่ ขณะที่ inbox และ state ยังคงอยู่นอก repository
- ยกเลิกเฉพาะข้อ 3 “ตำแหน่งของ Collector” ใน ADR 0005; การยอมรับความเสี่ยงและกติกาความปลอดภัยในข้อนั้นยังใช้ต่อ

## ทางเลือกที่ไม่เลือก

- **แยกเป็น Git repository อีกชุด** ไม่เลือกเพราะเจ้าของต้องการโปรเจกต์เดียวที่เปิด ตรวจ และเปลี่ยนสัญญากับ Collector ได้ใน PR เดียว
- **ย้าย Collector เข้า `src/paiya/`** ไม่เลือกเพราะจะทำให้เส้นแบ่ง process และ dependency ระหว่างตัวอ่านแหล่งกับ service ของ Paiya ไม่ชัด
