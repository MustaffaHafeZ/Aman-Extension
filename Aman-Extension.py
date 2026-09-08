#!/usr/bin/env python3
# ============================================================
#  Workstation Setup App - GUI (Python/Tkinter)
#  Run:  sudo python3 workstation_app.py
#  Requirement:  sudo apt install python3-tk   (once)
# ============================================================

import os
import shlex
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
from datetime import datetime

# ---------- Theme ----------
BG      = "#0f172a"
PANEL   = "#1e293b"
BTN_BG  = "#334155"
BTN_HOV = "#475569"
FG      = "#e2e8f0"
ACCENT  = "#38bdf8"
OK      = "#4ade80"
ERR     = "#f87171"
WARN    = "#fbbf24"
MUTED   = "#64748b"

output = None   # set during UI build

# ---------- Root check (must run with sudo) ----------
if os.geteuid() != 0:
    r = tk.Tk(); r.withdraw()
    messagebox.showerror("Root required", "Run as root:\n\nsudo python3 workstation_app.py")
    sys.exit(1)

# ---------- Helpers ----------
def log(msg, tag="info"):
    color = {"info": FG, "ok": OK, "err": ERR, "warn": WARN, "cmd": ACCENT}[tag]
    ts = datetime.now().strftime("%H:%M:%S")
    output.configure(state="normal")
    output.insert("end", f"[{ts}] ", ("ts",))
    output.insert("end", msg + "\n", (tag,))
    output.tag_configure("ts", foreground=MUTED)
    output.tag_configure(tag, foreground=color)
    output.see("end")
    output.configure(state="disabled")

def run(cmd):
    """Run a shell command, log it live, return (rc, out, err)."""
    log(f"$ {cmd}", "cmd")
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if p.stdout.strip():
        log(p.stdout.rstrip())
    if p.stderr.strip():
        log(p.stderr.rstrip(), "warn")
    if p.returncode != 0:
        log(f"Exit code: {p.returncode}", "err")
    return p.returncode, p.stdout, p.stderr

def ask_user(title, prompt):
    name = simpledialog.askstring(title, prompt, parent=root)
    return name.strip() if name else None

def user_exists(name):
    return subprocess.run(f"id {shlex.quote(name)}", shell=True,
                          capture_output=True).returncode == 0

def ensure_dmidecode():
    if shutil.which("dmidecode") is None:
        log("dmidecode not found - installing...", "warn")
        run("apt-get install -y dmidecode")

# ---------- Actions ----------
def enable_forti():
    log("Enabling FortiClient...", "warn")
    run("systemctl enable forticlient")
    run("systemctl start forticlient 2>/dev/null")
    log("FortiClient enabled.", "ok")

def fix_permissions():
    user = ask_user("Home Permissions", "Username to set /home permissions (744):")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist.", "err"); return
    run(f"chmod 744 -R /home/{shlex.quote(user)}")
    log(f"/home/{user} permissions set to 744.", "ok")

def collect_hw_info():
    log("Collecting hardware info...", "warn")
    ensure_dmidecode()
    log("--- System Serial Number ---", "warn")
    run("dmidecode -s system-serial-number")
    log("--- Disk Type (ROTA: 0 = SSD / 1 = HDD) ---", "warn")
    run("lsblk -d -o NAME,ROTA")
    log("--- Disk Sizes ---", "warn")
    run("lsblk -d -o NAME,SIZE")
    log("--- RAM Type ---", "warn")
    run("dmidecode -t memory | grep -i 'type:'")
    log("--- Disk Space (/) ---", "warn")
    run("df -Th /")
    log("Hardware info collected.", "ok")

def add_admin():
    user = ask_user("Add Administrator", "Username to make Administrator:")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist.", "err"); return
    run(f"usermod -aG sudo {shlex.quote(user)}")
    log(f"{user} added to sudo group.", "ok")

def enable_wifi():
    user = ask_user("Enable Hidden Wi-Fi", "Username to enable hidden Wi-Fi for:")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist.", "err"); return
    run(f"usermod -aG netdev {shlex.quote(user)}")
    log(f"{user} added to netdev group.", "ok")

def configure_gdm3():
    log("Opening /etc/gdm3/custom.conf in editor...", "warn")
    run("xdg-open /etc/gdm3/custom.conf || gedit /etc/gdm3/custom.conf || nano /etc/gdm3/custom.conf")
    if messagebox.askyesno("Restart GDM3",
            "Restart GDM3 now?\n\nWARNING: all active sessions will be logged out!",
            icon="warning", parent=root):
        run("systemctl restart gdm3")
        log("GDM3 restarted.", "ok")

def remove_user():
    user = ask_user("Remove User", "Username to remove:")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist.", "err"); return
    if messagebox.askyesno("Confirm deletion",
            f"Remove '{user}' PERMANENTLY?\n/home/{user} will be deleted.\nThis cannot be undone!",
            icon="warning", parent=root):
        run(f"rm -rf /home/{shlex.quote(user)}")
        log(f"User '{user}' removed.", "ok")

# ---------- Build UI ----------
root = tk.Tk()
root.title("Workstation Setup")
root.geometry("780x580")
root.configure(bg=BG)

tk.Label(root, text="Workstation Setup", bg=BG, fg=ACCENT,
         font=("Ubuntu", 16, "bold")).pack(pady=(14, 2))
tk.Label(root, text="Click an action - output appears below",
         bg=BG, fg=MUTED, font=("Ubuntu", 9)).pack()

btn_frame = tk.Frame(root, bg=BG)
btn_frame.pack(pady=8, padx=12)

buttons = [
    ("Enable FortiClient",      enable_forti),
    ("Home Permissions (744)",  fix_permissions),
    ("Hardware Info",           collect_hw_info),
    ("Add Administrator",       add_admin),
    ("Enable Hidden Wi-Fi",     enable_wifi),
    ("Configure GDM3",          configure_gdm3),
    ("Remove User",             remove_user),
]
for i, (text, cb) in enumerate(buttons):
    b = tk.Button(btn_frame, text=text, command=cb, bg=BTN_BG, fg=FG,
                  activebackground=BTN_HOV, activeforeground=FG,
                  relief="flat", padx=10, pady=6,
                  font=("Ubuntu", 10), cursor="hand2")
    b.grid(row=i // 2, column=i % 2, padx=6, pady=4, sticky="ew")
    btn_frame.columnconfigure(i % 2, weight=1)

tk.Button(btn_frame, text="Clear Log", command=lambda: (
    output.configure(state="normal"), output.delete("1.0", "end"),
    output.configure(state="disabled")),
    bg=BTN_BG, fg=FG, activebackground=BTN_HOV, activeforeground=FG,
    relief="flat", padx=10, pady=6, font=("Ubuntu", 10), cursor="hand2"
).grid(row=3, column=0, columnspan=2, padx=6, pady=4, sticky="ew")

output = scrolledtext.ScrolledText(root, bg=PANEL, fg=FG, insertbackground=FG,
                                   wrap="word", state="disabled",
                                   font=("monospace", 10), relief="flat")
output.pack(fill="both", expand=True, padx=12, pady=(2, 12))

root.mainloop()