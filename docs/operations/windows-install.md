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

ใช้ PowerShell session ที่มี environment เดิม หยุด process ที่เขียนฐานข้อมูล ตรวจ revision ปัจจุบัน และสำรองก่อนสั่ง `alembic upgrade head` เสมอ ชุดสำรองต้องตรงกับ revision เก่าก่อน migration:

ถ้า revision ปัจจุบันยังอยู่ก่อน `0010_backup_runs` อย่ารัน `paiya backup create` ในตัวอย่างนี้ ให้ทำทางครั้งเดียวด้านล่างก่อน แล้วทำขั้นตอนต่อจาก `git switch --detach <new-ref>`

```powershell
# หยุด worker และ serve ด้วย Ctrl+C หรือหยุด task/service ที่เจ้าของตั้งไว้
uv run alembic current
paiya backup create
$backupSet = 'F:\PaiyaBackup\<set-name-from-create-output>'
paiya backup verify --set $backupSet
# ตรวจ manifest ว่าฐานสำรองมาจาก revision ปัจจุบันก่อนอัปเกรด
git switch --detach <new-ref>
uv sync --frozen
uv run alembic upgrade head
uv run python scripts/smoke_test.py
```

ถ้าทุก check ใน JSON smoke report ผ่าน ให้เริ่ม `paiya worker run` และ `uv run python -m paiya.serve` ใหม่ โดยตั้ง `PAIYA_PORT` เป็นพอร์ต loopback ที่เจ้าของเลือกไว้

### ทางครั้งเดียวสำหรับฐานข้อมูลก่อน migration 0010

ใช้ทางนี้เฉพาะการอัปเกรดครั้งแรกจาก revision ก่อน `0010_backup_runs` ซึ่งโดยทั่วไปคือ `0009_purge_jobs` ก่อน O1a เท่านั้น ฐานดังกล่าวยังใช้ `paiya backup create` ไม่ได้ เพราะตาราง `backup_runs` เกิดจาก migration 0010 ให้สำรองด้วยมือก่อน migration โดยเก็บทั้ง archive ฐานข้อมูลและสำเนาคลัง media ไว้นอก media root, repository และ purge log:

```powershell
# หยุด worker และ serve แล้วตรวจ revision เก่า
uv run alembic current

# ตั้งค่า PostgreSQL client ให้ใช้ credential จาก pgpass หรือ credential store
# โดยไม่ใส่รหัสผ่านใน command line
$pgDsn = 'host=127.0.0.1 port=5432 dbname=paiya user=paiya'
$mediaSource = (Resolve-Path $env:PAIYA_FIXTURE_MEDIA_ROOT).Path.TrimEnd('\')
$manualRoot = 'F:\PaiyaPre0010-<timestamp>'
$manualRootFull = [IO.Path]::GetFullPath($manualRoot).TrimEnd('\')
if ($manualRootFull -eq $mediaSource -or
    $manualRootFull.StartsWith($mediaSource + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $mediaSource.StartsWith($manualRootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Manual backup must be outside the media root'
}
if (Test-Path -LiteralPath $manualRoot) { throw 'Manual backup target already exists' }
New-Item -ItemType Directory -Path $manualRoot | Out-Null
$manualArchive = Join-Path $manualRoot 'database.dump'
$mediaCopy = Join-Path $manualRoot 'media'
pg_dump --format=custom --file $manualArchive --dbname $pgDsn
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }

$sourceHashes = @(Get-ChildItem -LiteralPath $mediaSource -File -Recurse | ForEach-Object {
  $relative = $_.FullName.Substring($mediaSource.Length).TrimStart('\').Replace('\', '/')
  '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $relative
} | Sort-Object)
robocopy $mediaSource $mediaCopy /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ
if ($LASTEXITCODE -ge 8) { throw 'Media copy failed' }
$copiedHashes = @(Get-ChildItem -LiteralPath $mediaCopy -File -Recurse | ForEach-Object {
  $relative = $_.FullName.Substring($mediaCopy.Length).TrimStart('\').Replace('\', '/')
  '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $relative
} | Sort-Object)
if (Compare-Object -ReferenceObject $sourceHashes -DifferenceObject $copiedHashes) {
  throw 'Media copy hash verification failed'
}
$sourceHashes | Set-Content -Encoding ascii (Join-Path $manualRoot 'media-sha256.txt')
```

เลือก `$manualRoot` ให้อยู่นอกคลัง media, repository และ purge log บันทึก revision เก่าและเก็บ archive, media copy และ hash manifest ไว้ด้วยกันจนตรวจการกู้คืนเสร็จ หาก `pg_dump` หรือการเทียบ hash ล้มเหลว ให้หยุดและอย่าเริ่ม migration

เมื่อจำเป็นต้องกู้คืนชุด manual นี้ ให้ใช้โค้ดก่อน O1a ที่ตรงกับ revision ใน archive สร้างฐานเป้าหมายใหม่กับ media directory ว่าง จากนั้นกู้ฐานด้วย `pg_restore` และคัดลอก media พร้อมตรวจ hash กับ manifest ก่อนเปลี่ยน settings:

```powershell
# หยุดทุก process; checkout โค้ดก่อน O1a ที่ตรงกับ archive แล้วติดตั้ง lockfile
git switch --detach <pre-0010-ref>
uv sync --frozen

$restoreDsn = 'host=127.0.0.1 port=5432 dbname=paiya_pre0010_restore user=paiya'
$restoreDatabaseUrl = 'postgresql+psycopg://<user>:<password>@127.0.0.1:5432/paiya_pre0010_restore'
$targetMedia = 'C:\PaiyaData\media-pre0010-restore'
docker compose exec -T db createdb -U paiya paiya_pre0010_restore
New-Item -ItemType Directory -Path $targetMedia | Out-Null
pg_restore --exit-on-error --no-owner --no-privileges --dbname $restoreDsn $manualArchive
if ($LASTEXITCODE -ne 0) { throw 'pg_restore failed' }
robocopy $mediaCopy $targetMedia /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ
if ($LASTEXITCODE -ge 8) { throw 'Media restore copy failed' }
$restoredHashes = @(Get-ChildItem -LiteralPath $targetMedia -File -Recurse | ForEach-Object {
  $relative = $_.FullName.Substring($targetMedia.Length).TrimStart('\').Replace('\', '/')
  '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $relative
} | Sort-Object)
$expectedHashes = @(Get-Content (Join-Path $manualRoot 'media-sha256.txt'))
if (Compare-Object -ReferenceObject $expectedHashes -DifferenceObject $restoredHashes) {
  throw 'Restored media hash verification failed'
}
$env:PAIYA_DATABASE_URL = $restoreDatabaseUrl
$env:PAIYA_FIXTURE_MEDIA_ROOT = $targetMedia
uv run alembic current
# Confirm current equals the revision recorded before the original upgrade.
uv run python -c "from fastapi.testclient import TestClient; from paiya.api.app import app; from paiya.read.context import read_context_group_key_from_env; key=read_context_group_key_from_env(); assert key is not None; client=TestClient(app); groups=client.get('/v1/groups'); health=client.get('/healthz'); assert groups.status_code == 200 and any(row['group_key']==key for row in groups.json()['groups']); assert health.status_code == 200; print({'GET /v1/groups':groups.status_code,'GET /healthz':health.status_code})"
```

Do not run `alembic upgrade head` during this manual restore: restore the old revision first and validate it with the matching old code. The first release tag `v0.1.0` already contains migration 0010, so this one-time path does not apply to upgrades between release tags that both contain O1a.

## Rollback

O1a restore เขียนได้เฉพาะฐานข้อมูลและ media ปลายทางใหม่ที่ว่าง และคำสั่ง restore จะรัน `alembic upgrade head` ด้วยโค้ดของ ref ที่เรียกมัน (สัญญา [backup-and-restore.md](../architecture/contracts/backup-and-restore.md) `4.3` ขั้น 3) ดังนั้น rollback ต้องใช้ลำดับนี้:

1. หยุด worker, serve, MCP และ process อื่นที่เขียนฐานเดิม
2. checkout ref ก่อนหน้าและติดตั้ง dependency จาก lockfile ของ ref นั้น:

   ```powershell
   git switch --detach <previous-ref>
   uv sync --frozen
   ```

3. ตรวจชุดที่สร้างก่อนอัปเกรด แล้วสร้างฐานว่างและ media directory ใหม่บน PostgreSQL 17:

   ```powershell
   paiya backup verify --set $backupSet
   docker compose exec -T db createdb -U paiya paiya_rollback
   $targetMedia = 'C:\PaiyaData\media-rollback'
   New-Item -ItemType Directory -Path $targetMedia
   ```

4. เรียก `paiya backup restore` จาก ref ก่อนหน้า โดยใช้ชุดที่สำรองไว้ก่อน migration และ restore ไป target ใหม่:

   ```powershell
   $targetDatabaseUrl = 'postgresql+psycopg://<user>:<password>@127.0.0.1:5432/paiya_rollback'
   paiya backup restore --set $backupSet `
     --target-database-url $targetDatabaseUrl `
     --target-media-root $targetMedia
   ```

   Restore ตรวจ hash, replay purge log ปัจจุบัน และ migrate target ไป head ของโค้ดที่รันคำสั่ง ซึ่งตอนนี้เป็น ref ก่อนหน้า ตรวจผล JSON ว่าทุกขั้นผ่านแล้วจึงดำเนินต่อ

5. เปลี่ยน settings ให้ชี้ไปยัง target ที่ restore เสร็จ และตรวจด้วยเครื่องมือ smoke/read ของ ref ก่อนหน้า:

   ```powershell
   $env:PAIYA_DATABASE_URL = $targetDatabaseUrl
   $env:PAIYA_FIXTURE_MEDIA_ROOT = $targetMedia
   uv run alembic current
   uv run python -m paiya.cli health
   ```

   ชุด backup ต้องถูกสร้างก่อน `alembic upgrade head` ขณะฐานยังอยู่ revision เก่า หาก ref ก่อนหน้าไม่มี `scripts/smoke_test.py` ให้ใช้ smoke/read checks ที่มีใน ref นั้น ใน rehearsal `0edc221` ใช้ `alembic current` ซึ่งได้ `0010_backup_runs`, `uv run python -m paiya.cli health` ซึ่งคืน JSON สำเร็จ และ read API sample ที่ผ่าน หาก ref ไม่มี health command ให้บันทึกข้อจำกัดและตรวจ `/healthz` ผ่าน read API sample แทน

   read API sample ด้วยข้อมูลสังเคราะห์:

   ```powershell
   uv run python -c "from fastapi.testclient import TestClient; from paiya.api.app import app; from paiya.read.context import read_context_group_key_from_env; key=read_context_group_key_from_env(); assert key is not None; client=TestClient(app); groups=client.get('/v1/groups'); health=client.get('/healthz'); assert groups.status_code == 200 and any(row['group_key']==key for row in groups.json()['groups']); assert health.status_code == 200; print({'GET /v1/groups':groups.status_code,'GET /healthz':health.status_code})"
   ```

   หาก smoke/read check ใดที่มีใน ref ก่อนหน้าไม่ผ่าน ให้หยุดและคง target กับ backup ไว้เพื่อวิเคราะห์

การ restore ทับ live database หรือ media ที่มีข้อมูลไม่มีให้ใช้ใน O1a; นั่นเป็น O1b ที่ยังไม่รวมในขอบเขตนี้

## ซ้อมความต่อเนื่อง O4

O4 ใช้เฉพาะข้อมูล fixture สังเคราะห์และฐาน PostgreSQL 17 ชั่วคราว ใช้ `PAIYA_TEST_DATABASE_URL` ชี้ไปยังฐาน `paiya_test` ที่อนุญาตให้สร้างและลบฐานชั่วคราว จากราก checkout รัน:

```powershell
uv run python scripts/continuity_drill.py
```

สคริปต์สร้างชื่อฐานไม่ซ้ำ, ทดสอบ worker หลังจุดจำลองการหยุด, replay/restore, API และ MCP แล้วลบเฉพาะฐานและไดเรกทอรีที่มันสร้าง ตรวจ JSON สรุปว่าทุก step และ `cleanup.passed` เป็น `true` ก่อนถือว่าซ้อมผ่าน สคริปต์ใช้เฉพาะ loopback ที่เลือกพอร์ตเองซึ่งมากกว่า 8000 และไม่มีคำขอไปแหล่งภายนอก หากใช้ PG client ผ่าน Docker wrapper ให้ตรวจ `paiya-pg17` และ `PAIYA_PG_DUMP`/`PAIYA_PG_RESTORE` ให้ตรงกับสัญญา PostgreSQL 17

## ผลซ้อม upgrade/rollback

ซ้อมระหว่าง ref ที่มี O1a ทั้งคู่: ref เก่า `0edc221` (main หลัง PR #80, head `0010_backup_runs`) และ ref ใหม่ HEAD ของสาขานี้ `b839dc7` ใช้ PostgreSQL 17.11, fixture `synthetic-demo`, `UV_OFFLINE=1` และปิด network ภายนอก ไม่มี release tag ในการซ้อม จึงเป็น commit-to-commit rehearsal ไม่ใช่ tag-to-tag ตรวจ diff ของ migrations แล้วไม่พบความเปลี่ยนแปลงระหว่าง ref; `alembic upgrade head` บน ref ใหม่จึงเป็น no-op และไม่มี migration รันระหว่างสอง ref

ก่อนเริ่ม C: มีพื้นที่ว่าง `102,111,981,568` bytes; หัก write bound `500,000,000` bytes แล้วยังเหลือ `101,611,981,568` bytes สูงกว่า floor `70,000,000,000` bytes ผลซ้อมใช้ฐานข้อมูลและ media ชั่วคราวที่สร้างใหม่

คำสั่งกับผลจริงที่สำคัญ:

```text
git worktree add --detach <system-temp>\old-ref 0edc221
  -> exit 0; detached at 0edc2214eab08166f367772bf8ebcb449f728da1
uv sync --frozen                              (old ref, UV_OFFLINE=1)
  -> exit 0; Checked 53 packages
uv run alembic upgrade head                   (old ref)
  -> exit 0; applied 0009_purge_jobs -> 0010_backup_runs
uv run alembic current                        (old ref)
  -> exit 0; 0010_backup_runs (head)
uv run python -m paiya.cli demo seed
  -> exit 0; group_key=synthetic-demo, 4 observations
uv run python -m paiya.cli backup create     (old ref; before new-ref upgrade)
  -> exit 0; backup_run_id=51d045c2-a26e-4f2f-9fc1-2be5c4cbca50
     set=20261109T012156Z-51d045c2-a26e-4f2f-9fc1-2be5c4cbca50
     db_bytes=71660, media_count=2, media_bytes=10548
uv run python -m paiya.cli backup verify --set <set>
  -> exit 0; verified=true, missing_files=[], extra_files=[]
git switch --detach b839dc7                  (same temp worktree)
  -> exit 0
uv sync --frozen                              (new ref, UV_OFFLINE=1)
  -> exit 0; Checked 53 packages
uv run alembic current                        (before upgrade)
  -> exit 0; 0010_backup_runs (head)
uv run alembic upgrade head                   (new ref)
  -> exit 0; no migration ran
uv run alembic current                        (after upgrade)
  -> exit 0; 0010_backup_runs (head)
uv run python scripts/smoke_test.py           (new ref)
  -> exit 0; all 5 checks true:
     alembic_head, purge_log, paiya_health_json,
     read_api_sample, testclient_read_routes
     GET /v1/groups=200, GET /healthz=200
git switch --detach 0edc221                   (rollback; old ref first)
  -> exit 0
uv sync --frozen                              (old ref, before restore)
  -> exit 0; Checked 53 packages
docker exec paiya-pg17 createdb -U paiya paiya_o3_r1b_restore_e84fc9b11981
  -> exit 0; new empty target database
uv run python -m paiya.cli backup verify --set <set> (old ref)
  -> exit 0; verified=true, missing_files=[], extra_files=[]
uv run python -m paiya.cli backup restore --set <set> --target-database-url <new-target> --target-media-root <new-media>
  -> exit 0; ready=true; all 8 steps true:
     verify, target_check, pg_restore, media_copy, alembic_upgrade,
     purge_replay, orphan_check, api_sample
     migration_upgrade_performed=false, pending_count=0,
     database/media orphans=0, posts=3, timelines=3, images=2
uv run alembic current                        (restored target, old ref)
  -> exit 0; 0010_backup_runs (head)
uv run python -m paiya.cli health             (restored target, old ref)
  -> exit 0; JSON returned; media disk=ok, backup disk=ok,
     backup state=ok
uv run python -c <synthetic read API sample> (restored target, old ref)
  -> exit 0; GET /v1/groups=200, GET /healthz=200,
     read_context_matches=true
```

ใน PowerShell ของ rehearsal ไม่มี `paiya` console launcher ให้ `uv run paiya health` จึงจบด้วย exit 2 (`Failed to spawn: paiya`); ใช้ module entry point `uv run python -m paiya.cli health` ซึ่งทำงานบน ref เก่าแทน Health JSON มี `last_failure_reason=backup_destination_not_permitted` จากการลองตั้ง backup directory ชั่วคราวที่ไม่ผ่าน path policy ก่อนการซ้อมรอบสำเร็จ; backup set ที่ใช้ในผลด้านบนสร้างและ verify สำเร็จ ส่วน old ref ไม่มี `scripts/smoke_test.py` จึงตรวจ rollback ด้วย revision, health และ read API sample ตามข้างต้น

ผล limitation จาก round 1 ยังคงไว้: ref ก่อน O1a ที่ revision `0009_purge_jobs` สั่ง `paiya backup create` ไม่ได้ เพราะ migration `0010_backup_runs` ยังไม่สร้างตาราง `backup_runs`; คำสั่งจบด้วย exit 1 และ `database_error` ในการซ้อมรอบนั้น นี่เป็นข้อจำกัดของลำดับ runbook และ rehearsal ก่อนข้าม 0010 ไม่ใช่ code bug ทาง manual backup ก่อน 0010 ในหัวข้ออัปเกรดใช้สำหรับข้ามครั้งแรกเท่านั้น; tag แรก `v0.1.0` มี 0010 อยู่แล้ว จึงไม่กระทบ upgrade ระหว่าง release tags ที่มี O1a

ผลนี้ยืนยันเส้นทาง upgrade/rollback ระหว่างสอง commit ที่มี O1a เท่านั้น ไม่ใช่การซ้อมก่อน 0010 และไม่ใช่ tag-to-tag release rehearsal การเก็บ worktree, ฐานข้อมูล และไฟล์ชั่วคราวเป็น cleanup แยกจากผล verification

Cleanup ที่ทำจริง: `git worktree remove` ของ old-ref exit 0, `dropdb` ของฐานชั่วคราวทั้งสอง exit 0 และลบ `rehearsal.ps1` กับ `pg_client_bridge.py` แล้ว ตรวจซ้ำพบว่า worktree และฐานทั้งสองไม่อยู่แล้ว ระบบอนุมัติอัตโนมัติปฏิเสธคำสั่งลบ directory แบบ recursive ก่อนเริ่มทำงาน จึงยังมีเฉพาะ backup/media และ transcript ของ rehearsal นี้ใน `<system-temp>\paiya-o3-r1b-c2430aacc9b549a086b17c2e4d575a0c` และ directory backup ข้างเคียง รวมประมาณ 149 KB; ไม่ได้อยู่ใต้ F:
