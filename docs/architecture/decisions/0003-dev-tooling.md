# ADR 0003 เครื่องมือพัฒนาและโครงสร้างรีโป

**สถานะ** เสนอเพื่อรับรอง · ผู้ตัดสินใจคือ Tech lead · 7 พฤศจิกายน 2026
**เกี่ยวข้อง** [SPEC ข้อ 1](../SPEC.md) · [แบบจำลองข้อมูล](../data-model.md) · [แผนส่งมอบ](../../delivery/ROADMAP.md) · [AGENTS.md](../../../AGENTS.md)

## โจทย์

SPEC กำหนด stack ระดับเทคโนโลยีไว้แล้ว (FastAPI, React + Vite, PostgreSQL 17, SQLAlchemy + Alembic) แต่ยังไม่กำหนดว่าโค้ดวางที่ไหน ใช้เครื่องมือใดติดตั้ง ตรวจ และทดสอบ งานระยะ A จะส่งต่อให้เอเจนต์เขียนโค้ดหลายรอบ ถ้าแต่ละงานเลือกเครื่องมือเอง รีโปจะมีหลายแบบปนกันและ reviewer ทำซ้ำผลไม่ได้ อีกข้อคือเครื่องจริงของผู้ซื้อเป็น Windows ส่วนเอเจนต์และ CI รันบน Linux

## ข้อตัดสินที่เสนอ

| เรื่อง | ทางที่เลือก | เหตุผล |
|---|---|---|
| โครงสร้าง | โปรเจกต์ Python เดียวที่ราก (`pyproject.toml`, `src/paiya/`, `tests/`, `migrations/`) และแอปผู้ใช้ใน `web/` | API, worker, collector และ MCP ใช้โมเดลข้อมูลและสิทธิ์ชุดเดียวกัน แยกเป็นแพ็กเกจย่อยใน `src/paiya/` ได้โดยไม่ต้องจัดการหลายโปรเจกต์ |
| Python | 3.12 จัดการด้วย uv และ `uv.lock` | ติดตั้งซ้ำได้เหมือนกันทั้ง Windows และ Linux |
| ตรวจโค้ด Python | ruff (lint + format) และ mypy แบบ strict สำหรับ `src/` | ใช้ตัวเดียวแทน flake8/black/isort และจับชนิดข้อมูลผิดที่ขอบ API/DB |
| ทดสอบ Python | pytest ใช้ PostgreSQL 17 จริงสำหรับเทสต์ที่แตะฐานข้อมูล | data model ใช้ `UNIQUE NULLS NOT DISTINCT`, partial index และ lock ที่ SQLite จำลองไม่ได้ |
| ฐานข้อมูลตอนพัฒนา | `compose.yaml` ที่รากมี service `db` เป็น `postgres:17` ส่วน CI ใช้ service container รุ่นเดียวกัน | ตรง SPEC และเปิดได้ทั้ง Docker Desktop บน Windows และ Linux |
| Migration | Alembic ใน `migrations/` เขียน DDL ให้ตรงกับ data model ทีละกลุ่มตาราง | ตามลำดับที่ ROADMAP กำหนด และทดสอบ upgrade/downgrade บนฐานว่างได้ |
| Web | Node 24 LTS, pnpm (ตรึงผ่าน `packageManager`), Vite, TypeScript strict, ESLint, vitest | ตรง SPEC และ build ได้ในขั้นตอนเดียว |
| CI | GitHub Actions แยก job `docs`, `python`, `web` บน ubuntu-latest ตรึง action ด้วย SHA | job แยกทำให้เห็นว่าส่วนไหนพัง และไม่ต้องรอส่วนที่ไม่เกี่ยว |

## กติกาที่ตามมา

- โค้ดต้องรันได้ทั้ง Windows และ Linux ใช้ `pathlib` และ config จาก environment variable ห้ามเขียน shell script ที่ใช้ได้ระบบเดียวเป็นทางหลัก CI ตรวจบน Linux ส่วนการรันบน Windows ผู้ดูแลตรวจตอนรับมอบระยะ
- เทสต์ห้ามเรียกเครือข่ายภายนอก ใช้ fixture สังเคราะห์ใน `fixtures/` เท่านั้น
- ค่าที่ใช้เชื่อมฐานข้อมูลอ่านจาก `PAIYA_DATABASE_URL` และสำหรับเทสต์จาก `PAIYA_TEST_DATABASE_URL` ห้ามใส่รหัสผ่านจริงในไฟล์ที่ commit
- `requirements-dev.txt` และ `scripts/check_*.py` เดิมย้ายไปอยู่ใน dependency group ของ uv ในงาน A0 โดยคำสั่งตรวจเอกสารยังใช้ได้

## ทางเลือกที่ไม่เลือก

- **Poetry หรือ pip-tools** ใช้ได้ แต่ uv เร็วกว่าและจัดการรุ่น Python ให้ด้วย ซึ่งช่วยบน Windows
- **SQLite สำหรับเทสต์** เร็วกว่าแต่ทดสอบข้อบังคับหลักของ data model ไม่ได้
- **แยก backend หลายโปรเจกต์** เพิ่มงานจัดการ dependency โดยยังไม่มีเหตุให้ deploy แยกกัน

## เมื่อใดต้องทบทวน

เมื่อต้อง deploy worker แยกเครื่องจาก API เมื่อเวลารันเทสต์ฐานข้อมูลใน CI เกิน 5 นาที หรือเมื่อเครื่องมือใดหยุดรองรับ Windows
