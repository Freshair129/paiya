"""Owner-operated local Collector controls; the Facebook session stays in Chrome."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from uuid import UUID

from paiya_collector.browser_adapter import RateLimitPause
from paiya_collector.desktop_controller import DesktopController
from paiya_collector.desktop_live import LocalDesktopOperations

REASONS = {
    "challenge_required": "Facebook ขอให้ยืนยันตัวตน กรุณาจัดการด้วยตัวเองใน Chrome",
    "session_required": "กรุณาล็อกอิน Facebook ด้วยตัวเองใน Chrome",
    "wrong_group": "กรุณาเปิดกลุ่มที่กำหนดไว้ใน Chrome โปรไฟล์เฉพาะ",
    "no_visible_posts": "ยังไม่พบโพสต์บนหน้า กรุณาเปิดหน้ากลุ่มให้แสดงโพสต์",
    "selectors_unverified": "ยังตรวจตำแหน่งโพสต์หรือคอมเมนต์ไม่ครบ กรุณาเปิดคอมเมนต์ที่มองเห็นแล้วตรวจอีกครั้ง",
    "security_scan_incomplete": "ตรวจหน้าด้านความปลอดภัยไม่ครบ จึงยังเริ่มเก็บไม่ได้",
    "rate_limited": "Facebook จำกัดคำขอ กรุณารอตาม Retry-After ก่อนลองใหม่",
    "inspection_unavailable": "ตรวจหน้าไม่ได้ กรุณาเปิด Chrome โปรไฟล์เฉพาะแล้วลองอีกครั้ง",
    "embedded_frame_unverified": "หน้านี้มีส่วนฝังที่ยังตรวจความปลอดภัยไม่ได้ จึงยังไม่เริ่มเก็บ",
}


class CollectorWindow:
    def __init__(self, root: tk.Tk, operations: LocalDesktopOperations) -> None:
        self.root = root
        self.operations = operations
        self.controller = DesktopController(operations)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.ready = False
        self.confirmed = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="เปิด Facebook แล้วเข้าสู่ระบบด้วยตัวคุณเอง")
        root.title("ป้ายยา — ควบคุมการเก็บข้อมูล")
        root.geometry("660x510")
        root.minsize(560, 450)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ป้ายยา", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, text="ควบคุมกลุ่ม Facebook และดูผลในแดชบอร์ด", wraplength=610).pack(
            anchor="w", pady=(2, 16)
        )
        ttk.Label(frame, text="1. เปิดกลุ่มและเข้าสู่ระบบ", font=("Segoe UI", 11, "bold")).pack(
            anchor="w"
        )
        self.open_button = ttk.Button(
            frame, text="เปิด Facebook ใน Chrome โปรไฟล์เฉพาะ", command=self.open_browser
        )
        self.open_button.pack(fill="x", pady=(5, 12))
        ttk.Label(frame, text="2. ตรวจหน้าและตำแหน่งข้อมูล", font=("Segoe UI", 11, "bold")).pack(
            anchor="w"
        )
        self.check_button = ttk.Button(frame, text="ตรวจความพร้อม", command=self.inspect)
        self.check_button.pack(fill="x", pady=(5, 5))
        ttk.Checkbutton(
            frame,
            variable=self.confirmed,
            command=self.refresh_buttons,
            text="ฉันตรวจแล้วว่าหน้ากลุ่มแสดงปกติ และโพสต์ ชื่อผู้ขายกับภาพตรงกัน",
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(frame, text="3. เก็บและดูผล", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.run_button = ttk.Button(frame, text="เก็บโพสต์หนึ่งรอบ", command=self.run)
        self.run_button.pack(fill="x", pady=(5, 5))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="เปิดแดชบอร์ด", command=self.open_dashboard).pack(side="left")
        ttk.Button(actions, text="ดูรหัสผ่านเจ้าของเครื่อง", command=self.show_password).pack(
            side="left", padx=(8, 0)
        )
        ttk.Separator(frame).pack(fill="x", pady=(2, 8))
        ttk.Label(frame, textvariable=self.status, wraplength=610, justify="left").pack(anchor="w")
        self.refresh_buttons()
        root.after(150, self.poll)

    def refresh_buttons(self) -> None:
        self.open_button.configure(state="disabled" if self.busy else "normal")
        self.check_button.configure(state="disabled" if self.busy else "normal")
        self.run_button.configure(
            state="normal" if not self.busy and self.ready and self.confirmed.get() else "disabled"
        )

    def work(self, name: str, action) -> None:
        if self.busy:
            return
        self.busy = True
        self.refresh_buttons()

        def invoke() -> None:
            try:
                self.events.put((name, action()))
            except RateLimitPause as pause:
                if pause.retry_after_until is None:
                    text = "Facebook จำกัดคำขอ ยังไม่มีเวลาลองใหม่ กรุณาตรวจด้วยตัวเองก่อน"
                else:
                    when = pause.retry_after_until.astimezone().strftime("%d/%m/%Y %H:%M")
                    text = f"Facebook จำกัดคำขอ กรุณารออย่างน้อยถึง {when}"
                self.events.put(("error", text))
            except RuntimeError as error:
                if str(error) == "dedicated_browser_not_opened":
                    text = "เปิด Chrome ไม่สำเร็จ หากโปรไฟล์เฉพาะเปิดอยู่แล้วให้กลับไปที่หน้ากลุ่ม"
                elif str(error) == "retention_task_unavailable":
                    text = "ระบบลบข้อมูลตามอายุยังไม่พร้อม จึงยังเริ่มเก็บไม่ได้"
                else:
                    text = "ทำรายการไม่ได้ กรุณาตรวจฐานข้อมูลและ Chrome โปรไฟล์เฉพาะ"
                self.events.put(("error", text))
            except Exception:
                self.events.put(
                    ("error", "ทำรายการไม่ได้ กรุณาตรวจว่า Docker และ Chrome โปรไฟล์เฉพาะพร้อมใช้งาน")
                )

        threading.Thread(target=invoke, daemon=True).start()

    def open_browser(self) -> None:
        self.ready = False
        self.confirmed.set(False)
        self.status.set("กำลังเปิด Chrome โปรไฟล์เฉพาะ…")
        self.work("opened", self.operations.open_browser)

    def inspect(self) -> None:
        self.ready = False
        self.status.set("กำลังตรวจสิทธิ์และหน้ากลุ่ม…")

        def action():
            self.operations.verify_grant()
            return self.operations.inspect_page()

        self.work("inspected", action)

    def run(self) -> None:
        if not self.ready or not self.confirmed.get():
            return
        self.ready = False
        self.status.set("กำลังตรวจซ้ำและเก็บข้อมูลหนึ่งรอบ…")
        self.work("completed", self.controller.run_once)

    def poll(self) -> None:
        try:
            while True:
                name, value = self.events.get_nowait()
                self.busy = False
                if name == "error":
                    self.ready = False
                    self.status.set(str(value))
                elif name == "opened":
                    self.status.set("เข้าสู่ระบบใน Chrome แล้วกลับมากดตรวจความพร้อม")
                elif name == "inspected" and isinstance(value, dict):
                    self.ready = bool(value.get("ready"))
                    self.status.set(
                        "ตรวจตำแหน่งที่มองเห็นแล้ว กรุณายืนยันว่าข้อมูลตรงกัน"
                        if self.ready
                        else REASONS.get(str(value.get("reason")), "ยังตรวจหน้าไม่ครบ")
                    )
                elif name == "completed" and isinstance(value, dict):
                    state = value.get("state")
                    if state == "done":
                        coverage = {"complete": "ครบ", "partial": "บางส่วน"}.get(
                            str(value.get("coverage")), "ไม่ทราบ"
                        )
                        self.status.set(
                            f"บันทึกแล้ว {value.get('cards_parsed', 0)} โพสต์ · "
                            f"ความครอบคลุม {coverage} เปิดแดชบอร์ดเพื่อดูผล"
                        )
                    elif state == "not_ready":
                        self.status.set(REASONS.get(str(value.get("reason")), "ยังตรวจหน้าไม่ครบ"))
                    elif state == "already_run":
                        self.status.set("รอบทดลองที่อนุมัตินี้เคยเริ่มแล้ว จึงไม่เริ่มซ้ำ")
                    else:
                        self.status.set("รอบตรวจหยุดหรือข้อมูลไม่ผ่านการตรวจ จึงไม่บันทึกเข้าฐาน")
                    self.confirmed.set(False)
                self.refresh_buttons()
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def open_dashboard(self) -> None:
        webbrowser.open("http://127.0.0.1:8766/")

    def show_password(self) -> None:
        helper = self.operations.data_root / "b2-ops" / "Show-PaiyaB2OwnerPassword.ps1"
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "RemoteSigned",
                    "-File",
                    str(helper),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if len(lines) < 2:
                raise ValueError("credential_unavailable")
            messagebox.showinfo("รหัสผ่านเจ้าของเครื่อง", lines[-1], parent=self.root)
        except Exception:
            messagebox.showerror("ป้ายยา", "ไม่สามารถอ่านรหัสผ่านของผู้ใช้ Windows คนนี้ได้", parent=self.root)

    def close(self) -> None:
        if self.busy:
            self.status.set("กรุณารอให้รายการปัจจุบันเสร็จก่อนปิดหน้าต่าง")
            return
        self.root.destroy()


def main() -> None:
    repository = Path(__file__).resolve().parents[2]
    data_root = Path(os.environ.get("PAIYA_DESKTOP_DATA_ROOT", r"F:\paiya-collector-data"))
    run_id = UUID(os.environ["PAIYA_DESKTOP_PILOT_RUN_ID"])
    root = tk.Tk()
    CollectorWindow(
        root, LocalDesktopOperations(repository_root=repository, data_root=data_root, run_id=run_id)
    )
    root.mainloop()


if __name__ == "__main__":
    main()
