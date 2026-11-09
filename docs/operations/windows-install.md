# ติดตั้งและปฏิบัติการ Paiya บน Windows

คู่มือนี้ครอบคลุมรุ่นที่ใช้กับข้อมูลสังเคราะห์ในระยะ A และขั้นตอน O3 สำหรับ checkout, backup, upgrade และ rollback บนเครื่อง Windows เครื่องเดียว API ใช้ loopback ตาม [ADR 0004](../architecture/decisions/0004-local-access-and-mcp.md); ยังไม่มีการเปิด API ให้เครื่องอื่นใน LAN

CLI ใน source ปัจจุบันรันผ่าน `uv run python -m paiya.cli` และยังไม่มี executable `paiya.exe` ใน `pyproject.toml` หากต้องการพิมพ์รูปแบบคำสั่งสั้น `paiya ...` ใน PowerShell ให้ประกาศ helper ใน session นั้น:

```powershell
function paiya { uv run python -m paiya.cli @args }
```

Task Scheduler ไม่รับ helper จาก PowerShell session ให้ใช้ `uv.exe` พร้อม arguments ตามตัวอย่างในหัวข้อ backup รายวัน

## สิ่งที่ต้องมี

- Windows ที่ติดตั้ง Docker Desktop และเปิด WSL 2 backend พร้อม PostgreSQL 17 จาก Compose service `db` (`docker compose up -d db`) หรือ PostgreSQL 17 ที่เข้าถึงได้จาก `127.0.0.1` เท่านั้น
- Python 3.12 และ `uv` สำหรับติดตั้ง dependency และเรียก Python CLI
- Node 24, `corepack` และ pnpm 10.20.0 ตาม `web/package.json` สำหรับติดตั้งและ build PWA
- PostgreSQL 17 client (`pg_dump` และ `pg_restore`) เป็นทางเลือก หากไม่อยู่ใน `PATH` ให้ตั้ง `PAIYA_PG_DUMP` และ `PAIYA_PG_RESTORE` เป็นคำสั่งของ client รุ่น 17

ตรวจรุ่นและบริการก่อนเริ่ม:

```powershell
docker compose up -d db
docker compose exec -T db pg_isready -U paiya -d paiya_dev
uv --version
uv run python --version
node --version
corepack pnpm --version
pg_dump --version
pg_restore --version
```

## ค่าตั้ง `PAIYA_*`

ตั้งค่าผ่าน environment ของผู้ใช้ Windows หรือใน PowerShell session ห้ามบันทึก secret ลงไฟล์ในรีโป รวมถึงตัวอย่าง `.env` ที่ commit ได้

| ตัวแปร | ความหมาย | ค่าเริ่มต้น | Secret |
|---|---|---|---|
| `PAIYA_DATABASE_URL` | URL ของฐานข้อมูลที่ CLI, API และ worker ใช้ | ไม่ตั้ง; จำเป็นต่อคำสั่งฐานข้อมูล | ใช่ |
| `PAIYA_TEST_DATABASE_URL` | ฐาน PostgreSQL 17 สำหรับเทสต์ที่สร้างฐานชั่วคราวจาก `paiya_test` | ไม่ตั้ง; จำเป็นเมื่อรัน DB tests | ใช่ |
| `PAIYA_FIXTURE_MEDIA_ROOT` | ไดเรกทอรีภาพ fixture; ใช้กับ demo seed, worker และ health | ไม่ตั้ง | ไม่ |
| `PAIYA_FIXTURE_FEED_DIR` | ไดเรกทอรี feed fixture สำหรับ worker | ไม่ตั้ง; ต้องมีเมื่อรัน worker | ไม่ |
| `PAIYA_FIXTURE_REVISIT_DIR` | ไดเรกทอรี revisit fixture | ไม่ตั้ง; ใช้ `PAIYA_FIXTURE_FEED_DIR` | ไม่ |
| `PAIYA_READ_CONTEXT_GROUP_KEY` | กลุ่ม fixture ที่ server และ MCP เลือกอ่าน ต้องขึ้นต้น `synthetic-` | ไม่ตั้ง; API ที่ต้องใช้ context ตอบ 401 และ MCP ไม่เริ่ม | ไม่ |
| `PAIYA_PORT` | พอร์ต loopback ของ `paiya.serve` | `8000` | ไม่ |
| `PAIYA_WEB_DIST` | ตำแหน่งไฟล์ PWA ที่ serve | `web/dist` ใน checkout ปัจจุบัน | ไม่ |
| `PAIYA_MCP_DESTINATION` | ปลายทางที่ MCP ประกาศต่อ service | `local` | ไม่ |
| `PAIYA_PURGE_LOG_PATH` | JSONL log สำหรับคำสั่ง purge; ต้องอยู่นอก media และเก็บแยกจาก backup | ไม่ตั้ง; startup check ไม่มี log ให้ตรวจ | ไม่ |
| `PAIYA_BACKUP_DIR` | โฟลเดอร์ชุด backup | ไม่ตั้ง; ต้องตั้งก่อน `backup create` | ไม่ |
| `PAIYA_BACKUP_ALLOW_SAME_DEVICE` | อนุญาต backup อยู่ disk เดียวกับ media; ใช้เฉพาะ development/test | `0` หรือไม่ตั้ง | ไม่ |
| `PAIYA_PG_DUMP` | คำสั่ง `pg_dump` รุ่น 17 | `pg_dump` จาก `PATH` | ไม่ |
| `PAIYA_PG_RESTORE` | คำสั่ง `pg_restore` รุ่น 17 | `pg_restore` จาก `PATH` | ไม่ |
| `PAIYA_DISK_MIN_FREE_BYTES` | free space ขั้นต่ำก่อนเขียน media | `5368709120` (5 GiB) | ไม่ |
| `PAIYA_JOB_OVERDUE_MINUTES` | นาทีเกินกำหนดก่อนนับ queued job ว่าล่าช้า | `15` | ไม่ |
| `PAIYA_TREND_WINDOW_DAYS` | ช่วงวันของ trend | `7` | ไม่ |
| `PAIYA_TREND_MIN_OFFERS` | จำนวน offer ขั้นต่ำของ trend | `10` | ไม่ |
| `PAIYA_TREND_MIN_SELLERS` | จำนวน seller ขั้นต่ำของ trend | `3` | ไม่ |
| `PAIYA_TREND_FLAT_BAND_PCT` | ช่วงร้อยละที่ตีความว่าแนวโน้มคงที่ | `5` | ไม่ |
| `PAIYA_ENRICH_ENABLED` | เปิด worker เสริมข้อมูลด้วย Ollama | `false` | ไม่ |
| `PAIYA_OLLAMA_URL` | URL ของ Ollama; ต้องเป็น HTTP(S) loopback และไม่มี credential | `http://127.0.0.1:11434` | ไม่ |
| `PAIYA_OLLAMA_MODEL` | ชื่อโมเดล Ollama | `qwen3:4b` | ไม่ |
| `PAIYA_ENRICH_TIMEOUT_SECONDS` | timeout ของคำขอ enrichment | `60` | ไม่ |
| `PAIYA_FAULT_POINT` | จุดจำลองการหยุดของ worker สำหรับ O4 drill เท่านั้น | ไม่ตั้ง | ไม่ |
| `PAIYA_ALLOW_FAULT_INJECTION` | อนุญาต fault injection เมื่อซ้อม O4 เท่านั้น | ไม่ตั้ง; ต้องเป็น `1` พร้อม `PAIYA_FAULT_POINT` | ไม่ |
| `PAIYA_EVIDENCE_DIR` | ปลายทางหลักฐานสำหรับขั้นตอน QA ที่ใช้ภาพ | ไม่ตั้ง | ไม่ |
| `PAIYA_WRITE_A9B_RESULTS` | ให้เทสต์ A9b เขียนไฟล์ผล corpus เมื่อเป็น `1` | ไม่ตั้ง | ไม่ |

`PAIYA_DATABASE_URL` และ `PAIYA_TEST_DATABASE_URL` มักมีรหัสผ่าน ให้ตั้งผ่านหน้าต่าง **Environment Variables** ของบัญชีผู้ใช้ หรือ session ที่รันคำสั่ง ห้ามใส่ค่าจริงใน command block ที่ commit, script ในรีโป, Issue หรือ PR

## ติดตั้งครั้งแรก

1. เปิด PowerShell ที่ราก checkout ของ repository ตรวจว่ามี source ของรุ่นที่จะติดตั้ง
2. เปิด PostgreSQL 17:

   ```powershell
   docker compose up -d db
   docker compose exec -T db pg_isready -U paiya -d paiya_dev
   ```

3. ติดตั้ง Python dependencies จาก lockfile และ web dependencies:

   ```powershell
   uv sync --frozen
   corepack enable
   corepack pnpm --dir web install --frozen-lockfile
   corepack pnpm --dir web build
   ```

4. ตั้ง environment ของบัญชีผู้ใช้หรือ session โดยปรับชื่อ database และไดเรกทอรีให้ตรงกับเครื่อง ตัวอย่างนี้ไม่ใช่ที่เก็บ secret:

   ```powershell
   $env:PAIYA_DATABASE_URL = 'postgresql+psycopg://<user>:<password>@127.0.0.1:5432/paiya_dev'
   $env:PAIYA_FIXTURE_MEDIA_ROOT = 'C:\PaiyaData\media'
   $env:PAIYA_BACKUP_DIR = 'F:\PaiyaBackup'
   $env:PAIYA_PURGE_LOG_PATH = 'C:\PaiyaData\state\purge.jsonl'
   $env:PAIYA_READ_CONTEXT_GROUP_KEY = 'synthetic-demo'
   $env:PAIYA_PORT = '8765'
   New-Item -ItemType Directory -Force $env:PAIYA_FIXTURE_MEDIA_ROOT | Out-Null
   New-Item -ItemType Directory -Force $env:PAIYA_BACKUP_DIR | Out-Null
   New-Item -ItemType Directory -Force (Split-Path $env:PAIYA_PURGE_LOG_PATH) | Out-Null
   ```

   `PAIYA_BACKUP_DIR` ต้องอยู่บน physical disk คนละลูกกับ `PAIYA_FIXTURE_MEDIA_ROOT`, อยู่นอก repository และอยู่นอกโฟลเดอร์ purge log อย่าตั้ง `PAIYA_BACKUP_ALLOW_SAME_DEVICE=1` บนเครื่องใช้งาน

5. สร้าง schema ปัจจุบันและข้อมูลสาธิตสังเคราะห์:

   ```powershell
   uv run alembic upgrade head
   paiya demo seed
   ```

   หากยังไม่ได้ประกาศ helper `paiya` ให้ใช้ `uv run python -m paiya.cli demo seed` แทน `paiya demo seed` ข้อมูล demo ใช้กลุ่ม `synthetic-demo` และไม่มีข้อมูลจากแหล่งจริง

6. เริ่ม server ตามหัวข้อถัดไป แล้วเปิด `http://127.0.0.1:8765/` ใน browser บนเครื่องเดียวกัน

## เริ่ม serve, MCP และ worker

API ผูก `127.0.0.1` ตายตัวตาม ADR 0004; ไม่มีตัวเลือก bind ไปยัง LAN พอร์ตเริ่มต้นจาก `PAIYA_PORT` คือ `8000` ให้ตั้งพอร์ตว่างที่ไม่ใช่ค่านั้นก่อนเริ่ม:

```powershell
$env:PAIYA_PORT = '8765'
uv run python -m paiya.serve
```

รัน MCP เป็น process แยกผ่าน stdio ไม่มี HTTP port และอ่านผ่าน service layer เดียวกับ API:

```powershell
uv run python -m paiya.mcp
```

ตั้ง `PAIYA_READ_CONTEXT_GROUP_KEY=synthetic-demo` ให้ process ของ MCP ด้วย ค่า `PAIYA_MCP_DESTINATION` เริ่มต้นเป็น `local`; อย่ารันกับข้อมูลผู้ซื้อจริงด้วย read context ของ fixture

เริ่ม worker fixture ใน PowerShell อีกหน้าต่างหนึ่ง โดยกำหนด `PAIYA_FIXTURE_FEED_DIR` และ `PAIYA_FIXTURE_MEDIA_ROOT`:

```powershell
paiya worker run
```

หากยังไม่มี helper ให้เรียก `uv run python -m paiya.cli worker run` หรือเพิ่ม `--once` เพื่อประมวลผลหนึ่งรอบแล้วจบ Worker ระยะ A ใช้ fixture adapter เท่านั้น

## การปฏิเสธ startup เมื่อมี purge ค้าง

เมื่อ `PAIYA_PURGE_LOG_PATH` มีคำสั่งที่ฐานข้อมูลยังไม่มี `purge_jobs` สถานะ `succeeded`, `paiya.serve`, MCP และ worker จะปฏิเสธการเริ่มด้วย `purge_replay_required` และ exit code 2 หาก log อ่านไม่ได้หรือผิดรูปแบบ จะปฏิเสธด้วย `purge_log_invalid`

1. หยุด serve, MCP และ worker ที่กำลังใช้ฐานนั้น
2. ตรวจว่า `PAIYA_DATABASE_URL` และ `PAIYA_PURGE_LOG_PATH` ชี้ไปยังระบบกับ log คู่ที่ถูกต้อง แล้วรัน:

   ```powershell
   paiya purge replay
   ```

3. ตรวจ JSON ผลลัพธ์ว่ามี `pending_count: 0` แล้วค่อยเริ่ม process ใหม่

อย่าลบ ตัดต่อ หรือย้ายบรรทัดใน purge log เพื่อข้ามการตรวจ เก็บ log ไว้นอก media และนอกชุด backup เพราะ restore ต้อง replay คำสั่งจาก log ปัจจุบัน

## Backup รายวัน

คำสั่ง O1a สร้าง snapshot ฐานและ media พร้อม manifest/hash:

```powershell
paiya backup create
```

ตั้ง `PAIYA_BACKUP_DIR` ไปยัง disk คนละลูกกับ media จริง เช่น media อยู่ `C:` และ backup อยู่ `F:` ตรวจว่าโฟลเดอร์ปลายทางอยู่นอก repository และไม่ได้อยู่ใต้ media หรือโฟลเดอร์ purge log บันทึก set name จาก JSON เพื่อใช้กับ `backup verify`:

```powershell
paiya backup verify --set 'F:\PaiyaBackup\<set-name>'
```

### ตัวอย่างสร้างงานด้วย Task Scheduler

เจ้าของเครื่องสร้าง task ด้วยมือใน Task Scheduler:

1. เลือก **Create Task** ตั้งชื่อ `Paiya daily backup` และเลือกบัญชี Windows ที่มีค่า environment `PAIYA_DATABASE_URL`, `PAIYA_FIXTURE_MEDIA_ROOT`, `PAIYA_BACKUP_DIR` และ `PAIYA_PURGE_LOG_PATH`
2. เพิ่ม trigger แบบ **Daily** ตามเวลาที่เครื่องเปิดและฐานข้อมูลพร้อม
3. เพิ่ม action **Start a program**: Program `uv.exe`; Arguments `run --directory "C:\Paiya" python -m paiya.cli backup create`; Start in `C:\Paiya` โดยแทนที่ `C:\Paiya` ด้วยตำแหน่ง checkout จริง
4. เปิด **Run task as soon as possible after a scheduled start is missed** เพื่อให้เครื่องที่หลับทำงานเมื่อกลับมาใช้งาน และตรวจ JSON/log หลังรอบแรกด้วยมือ

Task Scheduler ไม่รับค่าจาก `$env:` ใน PowerShell window อื่นโดยอัตโนมัติ ให้เจ้าของตั้ง environment ของบัญชี Task Scheduler ให้เรียบร้อยก่อนสร้าง task และอย่าใส่รหัสผ่านลงใน Arguments

การเข้ารหัสชุด backup, การเก็บกุญแจ และ retention อัตโนมัติเป็น O1b ซึ่งยังไม่ได้ทำและยังรอการตัดสินใจใน Issue #78 ชุดสำรอง O1a ยังไม่เข้ารหัสและไม่มี retention job; อย่าถือว่ามีการลบชุดเก่าอัตโนมัติ

## ตรวจสุขภาพ

คำสั่ง CLI พิมพ์ health report เป็น JSON:

```powershell
paiya health
```

เมื่อ serve ทำงาน ให้เปิดหน้า UI `/admin/health` เช่น `http://127.0.0.1:8765/admin/health` ส่วน endpoint JSON คือ `/v1/admin/health` รายงานจำนวนคิว coverage media disk และ backup โดยไม่คืนเนื้อหาประกาศ

## อัปเกรด

ใช้ PowerShell session ที่มี environment เดิมและปิด process ที่เขียนฐานข้อมูลตามลำดับ:

```powershell
# หยุด worker และ serve ด้วย Ctrl+C หรือหยุด task/service ที่เจ้าของตั้งไว้
paiya backup create
$backupSet = 'F:\PaiyaBackup\<set-name-from-create-output>'
paiya backup verify --set $backupSet
git switch --detach <new-ref>
uv sync --frozen
uv run alembic upgrade head
uv run python scripts/smoke_test.py
```

ถ้าทุก check ใน JSON smoke report ผ่าน ให้เริ่ม `paiya worker run` และ `uv run python -m paiya.serve` ใหม่ โดยตั้ง `PAIYA_PORT` เป็นพอร์ต loopback ที่เจ้าของเลือกไว้

ข้อจำกัดจาก rehearsal ด้านล่าง: ฐานก่อน migration `0010_backup_runs` ยังสั่ง `paiya backup create` ไม่ได้ เพราะคำสั่งต้องเขียนตาราง `backup_runs` ซึ่ง migration นี้เป็นผู้สร้าง จนกว่าจะแก้ O1a หรือมีวิธีสำรองฐานก่อน `0010` ที่อนุมัติแล้ว อย่าใช้ขั้นตอนนี้เป็นหลักฐานว่าการอัปเกรดจาก `0009_purge_jobs` มี backup ก่อน migrate

## Rollback

O1a restore เขียนได้เฉพาะฐานข้อมูลและ media ปลายทางใหม่ที่ว่าง ขั้นตอน rollback จึงเก็บระบบเดิมไว้ก่อนและเตรียมชื่อฐานใหม่กับไดเรกทอรี media ใหม่:

1. หยุด worker และ serve
2. ตรวจ set ก่อน restore และสร้างฐานว่างกับโฟลเดอร์ปลายทางใหม่บน PostgreSQL 17:

   ```powershell
   paiya backup verify --set $backupSet
   docker compose exec -T db createdb -U paiya paiya_rollback
   $targetMedia = 'C:\PaiyaData\media-rollback'
   New-Item -ItemType Directory $targetMedia
   ```

3. Restore ไป target ใหม่ด้วย CLI ของรุ่นที่มี O1a:

   ```powershell
   $targetDatabaseUrl = 'postgresql+psycopg://<user>:<password>@127.0.0.1:5432/paiya_rollback'
   paiya backup restore --set $backupSet `
     --target-database-url $targetDatabaseUrl `
     --target-media-root $targetMedia
   ```

   Restore ตรวจ hash, replay purge log ปัจจุบัน และ migrate target ไป head ของโค้ดที่รันคำสั่ง หากไม่ผ่านทุกขั้น อย่าเปิด target ให้ค้น

4. เปลี่ยน `PAIYA_DATABASE_URL` และ `PAIYA_FIXTURE_MEDIA_ROOT` ไปยัง target ที่พร้อมแล้ว และตรวจ read API ด้วยโค้ดรุ่นก่อนหน้าที่ต้องการกลับไป:

   ```powershell
   $env:PAIYA_DATABASE_URL = $targetDatabaseUrl
   $env:PAIYA_FIXTURE_MEDIA_ROOT = $targetMedia
   git switch --detach <previous-ref>
   uv sync --frozen
   uv run alembic current
   ```

   ใน rehearsal นี้ restore ได้อัปเกรด target ไปถึง `0010_backup_runs` แล้ว ref ก่อน migration นี้ตอบ `alembic current` ไม่ได้เพราะ revision ไม่อยู่ใน migration graph ของ ref นั้น ส่วน read API sample ของ ref เก่ายังตอบ 200 ได้ การทดสอบอ่านอย่างเดียวจึงไม่ยืนยันว่า rollback พร้อมใช้

   ref ก่อน O3 ไม่มี `scripts/smoke_test.py`; ตรวจ read context กับ TestClient ด้วย sample สังเคราะห์นี้:

   ```powershell
   uv run python -c "from fastapi.testclient import TestClient; from paiya.api.app import app; from paiya.read.context import read_context_group_key_from_env; key=read_context_group_key_from_env(); assert key is not None; client=TestClient(app); groups=client.get('/v1/groups'); health=client.get('/healthz'); assert groups.status_code == 200 and any(row['group_key']==key for row in groups.json()['groups']); assert health.status_code == 200; print({'GET /v1/groups':groups.status_code,'GET /healthz':health.status_code})"
   ```

   หากรุ่นก่อนหน้าอ่าน schema หรือข้อมูลไม่ได้ ให้หยุดและคง target/backup ไว้เพื่อวิเคราะห์

การ restore ทับ live database หรือ media ที่มีข้อมูลไม่มีให้ใช้ใน O1a; นั่นเป็น O1b ที่ยังไม่รวมในขอบเขตนี้

## ซ้อมความต่อเนื่อง O4

O4 ใช้เฉพาะข้อมูล fixture สังเคราะห์และฐาน PostgreSQL 17 ชั่วคราว ใช้ `PAIYA_TEST_DATABASE_URL` ชี้ไปยังฐาน `paiya_test` ที่อนุญาตให้สร้างและลบฐานชั่วคราว จากราก checkout รัน:

```powershell
uv run python scripts/continuity_drill.py
```

สคริปต์สร้างชื่อฐานไม่ซ้ำ, ทดสอบ worker หลังจุดจำลองการหยุด, replay/restore, API และ MCP แล้วลบเฉพาะฐานและไดเรกทอรีที่มันสร้าง ตรวจ JSON สรุปว่าทุก step และ `cleanup.passed` เป็น `true` ก่อนถือว่าซ้อมผ่าน สคริปต์ใช้เฉพาะ loopback ที่เลือกพอร์ตเองซึ่งมากกว่า 8000 และไม่มีคำขอไปแหล่งภายนอก หากใช้ PG client ผ่าน Docker wrapper ให้ตรวจ `paiya-pg17` และ `PAIYA_PG_DUMP`/`PAIYA_PG_RESTORE` ให้ตรงกับสัญญา PostgreSQL 17

## ผลซ้อม upgrade/rollback

ซ้อมด้วยสคริปต์ชั่วคราวใน system temp directory (`paiya-o3-rehearsal.py`) จาก `a748f44` ซึ่งอยู่ก่อน commit `d65744c` ที่เพิ่ม migration `0010_backup_runs` ไปยัง HEAD ของ worktree O3/O4 โดยใช้ PostgreSQL 17.11 และ fixture `synthetic-demo` เท่านั้น ใช้ virtualenv ที่ติดตั้งไว้แล้วเพื่อทำตามข้อกำหนด no-packages; ไม่ติดตั้ง dependency เพิ่ม ไม่มี release tag ในขณะซ้อม จึงเป็น commit-to-commit rehearsal ไม่ใช่ tag-to-tag

คำสั่งหลักที่สคริปต์รันและผลจริง:

```text
git worktree add --detach <system-temp>\pre-0010 a748f44       -> exit 0
python -m alembic -c <old-ref>\alembic.ini upgrade 0009_purge_jobs -> exit 0
python -m paiya.cli demo seed                                  -> synthetic-demo seeded
python -m paiya.cli backup create  (HEAD, at 0009)             -> exit 1, database_error
python -m alembic upgrade head                                 -> exit 0; current=0010_backup_runs
python scripts/smoke_test.py                                   -> exit 0; all 5 checks passed
python -m paiya.cli backup create  (after 0010, control only)   -> exit 0; 2 media files, 10,548 media bytes
python -m paiya.cli backup verify --set <backup-set>            -> exit 0; verified=true, missing_files=[], extra_files=[]
python -m paiya.cli backup restore --set <backup-set> ...       -> exit 0; all 8 restore steps true
python -m alembic -c <old-ref>\alembic.ini current             -> failed; old ref does not recognize 0010_backup_runs
old-ref TestClient GET /v1/groups and GET /healthz              -> 200 and 200; synthetic-demo context matched
git worktree remove <system-temp>\pre-0010                     -> exit 0
DROP DATABASE <two temporary rehearsal databases>              -> both dropped
```

Rehearsal output: pre-`0010` backup returned `{"ok":false,"error":{"code":"database_error","message":"The database operation failed"}}`; post-migration smoke returned `ok=true` with `alembic_head`, `purge_log`, `paiya_health_json`, `read_api_sample` and `testclient_read_routes` all passing. Post-migration backup verification had no missing or extra files. Restore reported all eight steps true, zero pending purge commands and zero media orphans. The old-ref `alembic current` command failed (PowerShell exit `4294967295`) because the target was at `0010_backup_runs`; the old ref's read sample still returned 200 for both endpoints.

The pre-`0010` backup failure means the passed backup/verify/restore control is not a backup-before-upgrade or a successful rollback rehearsal. O1a currently writes `backup_runs` before starting the snapshot, while migration `0010` creates that table. Restore also advances its target to current HEAD, which the prior migration graph cannot inspect. These are open points for O1a/rollback compatibility; do not claim upgrade and rollback acceptance from `0009_purge_jobs` until resolved. Cleanup completed: the temporary worktree was removed with `git worktree remove`, both temporary databases were dropped, and media, backup set and worktree directory were deleted. The rehearsal script was run from system temp and was not committed.
