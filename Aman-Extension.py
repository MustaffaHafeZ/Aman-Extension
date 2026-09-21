#!/usr/bin/env python3
# ================================================================
#  Aman — Extension Tool  (GUI)
#  Tabs:  [System Setup]  |  [Domain Setup]  |  [Apps & Maintenance]
#  Run:   sudo python3 Aman-Extension.py
#  Deps:  sudo apt install python3-tk
# ================================================================

import json
import os
import queue
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime

# ================================================================
#  PRE-FLIGHT: ensure python3-tk is installed before importing tkinter.
#  If missing → runs `sudo apt install python3-tk`, then restarts the tool.
# ================================================================
try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, simpledialog, ttk
except ImportError:
    print("python3-tk not found — installing automatically (sudo apt install python3-tk)...")
    rc = subprocess.call(["sudo", "apt", "install", "-y", "python3-tk"])
    if rc != 0:
        # refresh package lists and retry once
        print("apt install failed — running 'sudo apt update' then retrying...")
        subprocess.call(["sudo", "apt", "update"])
        rc = subprocess.call(["sudo", "apt", "install", "-y", "python3-tk"])
    if rc == 0:
        print("python3-tk installed — restarting the tool...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        print(
            "ERROR: could not install python3-tk automatically.\n"
            "Please run manually:  sudo apt install python3-tk\n"
            "then start the tool again."
        )
        sys.exit(1)


# ---------------- AMAN brand palette (logo: blue + white) ----------------
NAVY = "#181830"  # deep navy
NAVY_DARK = "#0d0d1f"
PANEL = "#1e1e3a"
PANEL_HI = "#2b2b52"
GOLD = "#e3c15c"
GOLD_DIM = "#8a6f2a"
FG = "#e8eaf5"
MUTED = "#9aa0b5"
CMD = "#8ab4ff"
OK = "#4ade80"
ERR = "#f87171"
WARN = "#fbbf24"
BTN_TXT = "#ffffff"

# ---------------- Domain config ----------------
DOMAIN = "aman.local"
DOMAIN_USER = "mustafa.mhafez"
HOSTS_SEQ = 10  # 127.0.0.1 ... 127.0.9.1

# ---------------- App Version & GitHub Config ----------------
CURRENT_VERSION = "v1.0.0"
GITHUB_REPO = (
    "MustaffaHafeZ/Aman-Extension"  # Replace with actual "owner/repo" on GitHub
)

# ---------------- Globals ----------------
output = None
status = None
buttons = []  # action buttons list
cmd_queue = queue.Queue()
busy = False
current_proc = None  # active Popen → used by Stop button & interactive input
stop_btn = None      # wired in UI section
input_entry = None   # wired in UI section
LOG_FILE = "/var/log/aman_setup.log"
LOG_COMMANDS = False  # True → echo "$ command" in log ; False → hidden

# ---------------- Root check ----------------
if os.geteuid() != 0:
    r = tk.Tk()
    r.withdraw()
    messagebox.showerror(
        "Root required", "Run as root:\n\nsudo python3 Aman-Extension.py"
    )
    sys.exit(1)


# ================================================================
#  Helpers
# ================================================================
def log(msg, tag="info"):
    color = {
        "info": FG,
        "ok": OK,
        "err": ERR,
        "warn": WARN,
        "cmd": CMD,
        "muted": MUTED,
    }.get(tag, FG)
    ts = datetime.now().strftime("%H:%M:%S")
    output.configure(state="normal")
    output.insert("end", f"[{ts}] ", ("ts",))
    output.insert("end", msg + "\n", (tag,))
    output.tag_configure("ts", foreground=MUTED)
    output.tag_configure(tag, foreground=color)
    output.see("end")
    output.configure(state="disabled")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def set_status(text):
    status.configure(text=text)


def set_busy(on, cmd=""):
    global busy
    busy = on
    for b in buttons:
        try:
            b.configure(state="disabled" if on else "normal")
        except Exception:
            pass
    set_status(f"Running: {cmd}..." if on else "Ready")
    if stop_btn is not None:
        try:
            stop_btn.configure(state="normal" if on else "disabled")
        except Exception:
            pass
    if input_entry is not None:
        try:
            input_entry.configure(state="normal" if on else "disabled")
        except Exception:
            pass


def backup(path):
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak.{datetime.now():%Y%m%d-%H%M%S}")


def ask_user(title, prompt, initial=""):
    v = simpledialog.askstring(title, prompt, initialvalue=initial, parent=root)
    return v.strip() if v else None


def ask_password(title, prompt):
    dlg = tk.Toplevel(root)
    dlg.title(title)
    dlg.configure(bg=NAVY_DARK)
    dlg.resizable(False, False)
    tk.Label(
        dlg, text=prompt, bg=NAVY_DARK, fg=FG, font=("Ubuntu", 10)
    ).pack(padx=20, pady=(15, 8))
    e = tk.Entry(
        dlg,
        show="•",
        bg=PANEL,
        fg=FG,
        insertbackground=FG,
        relief="flat",
        font=("Ubuntu", 11),
        width=28,
    )
    e.pack(padx=20)
    e.focus_set()
    result = {"v": None}

    def ok():
        result["v"] = e.get()
        dlg.destroy()

    def cancel():
        dlg.destroy()

    btns = tk.Frame(dlg, bg=NAVY_DARK)
    btns.pack(pady=12)
    tk.Button(
        btns,
        text="OK",
        command=ok,
        bg=GOLD,
        fg=NAVY_DARK,
        relief="flat",
        padx=16,
        pady=4,
        font=("Ubuntu", 10, "bold"),
        cursor="hand2",
    ).pack(side="left", padx=6)
    tk.Button(
        btns,
        text="Cancel",
        command=cancel,
        bg=PANEL,
        fg=FG,
        relief="flat",
        padx=12,
        pady=4,
        font=("Ubuntu", 10),
        cursor="hand2",
    ).pack(side="left", padx=6)
    dlg.bind("<Return>", lambda _: ok())
    dlg.bind("<Escape>", lambda _: cancel())
    dlg.grab_set()
    root.wait_window(dlg)
    return result["v"]


def user_exists(name):
    return (
        subprocess.run(
            f"id {shlex.quote(name)}", shell=True, capture_output=True
        ).returncode
        == 0
    )


# ---------- Self-Update & Auto Restart Logic ----------
def perform_update_and_restart(download_url):
    """Downloads the updated script, overwrites current file, and restarts the app."""
    try:
        log("🔄 Downloading update file...", "warn")
        script_path = os.path.abspath(sys.argv[0])

        # Backup current script
        shutil.copy2(script_path, script_path + ".bak")

        # Fetch new code
        req = urllib.request.Request(
            download_url, headers={"User-Agent": "Aman-Extension-Tool"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            new_code = response.read()

        # Overwrite current file
        with open(script_path, "wb") as f:
            f.write(new_code)

        os.chmod(script_path, 0o755)
        log(
            "✅ Update applied successfully! Restarting application...", "ok"
        )

        # Close Tkinter and restart application seamlessly
        root.destroy()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except Exception as e:
        messagebox.showerror(
            "Update Error",
            f"Failed to apply update automatically:\n{e}",
            parent=root,
        )
        log(f"Update failed: {e}", "err")


def prompt_update_dialog(latest_version, download_url):
    answer = messagebox.askyesno(
        "Update Available",
        f"A new update ({latest_version}) is available!\n"
        f"Current Version: {CURRENT_VERSION}\n\n"
        f"Do you want to update and restart the application now?",
        parent=root,
    )
    if answer:
        perform_update_and_restart(download_url)


# ---------- Background GitHub Checker ----------
def check_github_updates():
    """Background worker to check for GitHub Releases."""

    def _worker():
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(
                url, headers={"User-Agent": "Aman-Extension-Tool"}
            )

            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                latest_version = data.get("tag_name", "")

                # Find direct .py asset link, or fallback to raw repo file
                download_url = None
                assets = data.get("assets", [])
                for asset in assets:
                    if asset.get("name", "").endswith(".py"):
                        download_url = asset.get("browser_download_url")
                        break

                if not download_url:
                    # Fallback URL if no attachment in release: downloads direct raw file from GitHub tag/main
                    script_name = os.path.basename(sys.argv[0])
                    download_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{latest_version}/{script_name}"

                if latest_version and latest_version != CURRENT_VERSION:
                    log(
                        f"🔔 New Update Available: {latest_version} (Current: {CURRENT_VERSION})",
                        "warn",
                    )
                    # Send prompt to Main Thread
                    root.after(
                        0,
                        lambda: prompt_update_dialog(
                            latest_version, download_url
                        ),
                    )
                else:
                    log(
                        f"✅ App is up to date (Version {CURRENT_VERSION}).",
                        "ok",
                    )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log(
                    "Check update skipped: Repository or releases not found on GitHub.",
                    "muted",
                )
            else:
                log(f"Check update error: HTTP {e.code}", "muted")
        except Exception:
            log("Could not check for updates (No internet connection)", "muted")

    threading.Thread(target=_worker, daemon=True).start()


# ---------- Threaded command runner ----------
def run_cmd(cmd, with_password=None, on_done=None):
    set_busy(True, cmd)
    if LOG_COMMANDS:
        log(f"$ {cmd}", "cmd")
    threading.Thread(
        target=_worker, args=(cmd, with_password, on_done), daemon=True
    ).start()


def _worker(cmd, password, on_done):
    global current_proc
    try:
        p = subprocess.Popen(
            cmd,
            shell=True,
            text=True,
            bufsize=1,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,  # always open → allows Y/N answers while running
        )
        current_proc = p
        if password is not None:
            p.stdin.write(password + "\n")
            p.stdin.flush()
            p.stdin.close()
        for line in p.stdout:
            cmd_queue.put(("out", line.rstrip()))
        p.wait()
        cmd_queue.put(("fin", p.returncode, cmd, on_done))
    except Exception:
        cmd_queue.put(("fin", 1, cmd, on_done))
    finally:
        current_proc = None


def poll_queue():
    try:
        while True:
            kind, *data = cmd_queue.get_nowait()
            if kind == "out":
                log(data[0])
            elif kind == "fin":
                rc, cmd, on_done = data
                if rc == 0:
                    log("OK", "ok")
                else:
                    log(
                        f"Command finished with exit code {rc}",
                        "warn" if rc == 100 else "err",
                    )
                set_busy(False)
                if on_done:
                    root.after(0, on_done)
    except queue.Empty:
        pass
    root.after(80, poll_queue)


# ---------- Stop / Break running command ----------
def stop_cmd():
    """Terminates the currently running command (Stop button)."""
    global current_proc
    p = current_proc
    if p is None or p.poll() is not None:
        log("No command is currently running.", "warn")
        return
    log("⏹ Stop requested — terminating running command...", "warn")
    try:
        if hasattr(os, "killpg"):
            # POSIX: kill the whole process group (shell + children like apt)
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        else:
            p.terminate()
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass
    # Hard-kill fallback if it ignores SIGTERM
    root.after(
        3000,
        lambda: (
            p.poll() is None
            and (
                log("Force-killing command...", "err"),
                (
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    if hasattr(os, "killpg")
                    else p.kill()
                ),
            )
        ),
    )


# ---------- Interactive input (Y/N prompts from running commands) ----------
def send_input(text, entry=None):
    """Sends a line to the running command's stdin (e.g. answer Y/N prompts)."""
    global current_proc
    p = current_proc
    if p is None or p.poll() is not None:
        log("No running command to receive input.", "warn")
        return
    if p.stdin is None or p.stdin.closed:
        log("Command stdin is closed — input cannot be sent.", "err")
        return
    try:
        p.stdin.write(text + "\n")
        p.stdin.flush()
        log(f"→ {text}", "cmd")
        if entry is not None:
            entry.delete(0, "end")
    except Exception as e:
        log(f"Failed to send input: {e}", "err")


# ================================================================
#  SYSTEM SETUP
# ================================================================
def sys_enable_forti():
    run_cmd(
        "systemctl enable forticlient && systemctl start forticlient 2>/dev/null; "
        "echo 'FortiClient service enabled.'"
    )


def sys_fix_permissions():
    user = ask_user("Home Permissions", "Username to set /home permissions (744):")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist", "err")
        return
    run_cmd(
        f"chmod 744 -R /home/{shlex.quote(user)} && "
        f"echo 'Permissions for /home/{user} set to 744.'"
    )


def sys_hardware_info():
    pre = (
        "if ! command -v dmidecode >/dev/null; then "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y dmidecode; fi;"
    )
    run_cmd(
        f"{pre} echo '--- Serial Number ---'; dmidecode -s system-serial-number; "
        f"echo; echo '--- Disk Type (ROTA: 0=SSD 1=HDD) ---'; lsblk -d -o NAME,ROTA; "
        f"echo; echo '--- Disk Sizes ---'; lsblk -d -o NAME,SIZE; "
        f"echo; echo '--- RAM Type ---'; dmidecode -t memory | grep -i 'type:'; "
        f"echo; echo '--- Disk Space (/) ---'; df -Th /"
    )


def sys_add_admin():
    user = ask_user("Add Administrator", "Username to make Administrator:")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist", "err")
        return
    run_cmd(
        f"usermod -aG sudo {shlex.quote(user)} && echo '{user} added to sudo group.'"
    )


def sys_enable_wifi():
    user = ask_user(
        "Enable Hidden Wi-Fi", "Username to enable hidden Wi-Fi for:"
    )
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist", "err")
        return
    run_cmd(
        f"usermod -aG netdev {shlex.quote(user)} && echo '{user} added to netdev group.'"
    )


def sys_configure_gdm3():
    run_cmd(
        "xdg-open /etc/gdm3/custom.conf || gedit /etc/gdm3/custom.conf || nano /etc/gdm3/custom.conf",
        on_done=lambda: messagebox.askyesno(
            "Restart GDM3",
            "Restart GDM3 now?\n\nWARNING: all active sessions will be logged out!",
            icon="warning",
            parent=root,
        )
        and run_cmd("systemctl restart gdm3 && echo 'GDM3 restarted.'"),
    )


def sys_remove_user():
    user = ask_user("Remove User", "Username to remove:")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist", "err")
        return
    if messagebox.askyesno(
        "Confirm deletion",
        f"Remove '{user}' PERMANENTLY?\n/home/{user} will be deleted.\nThis cannot be undone!",
        icon="warning",
        parent=root,
    ):
        run_cmd(f"rm -rf /home/{shlex.quote(user)} && echo 'User {user} removed.'")


# ================================================================
#  DOMAIN SETUP
# ================================================================
class HostsEditor:

    def __init__(self, parent):
        self.parent = parent
        self._reload()
        self._build_ui()

    def _parse(self):
        with open("/etc/hosts") as f:
            raw = f.read().splitlines()
        entries = []
        for i, ln in enumerate(raw):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            parts = ln.split()
            if len(parts) >= 2:
                entries.append((i, parts[0], " ".join(parts[1:])))
        return raw, entries

    def _reload(self):
        self.raw, self.entries = self._parse()

    def _find_lines(self, ip):
        self._reload()
        return [i for i, ln_ip, _ in self.entries if ln_ip == ip]

    def _write(self):
        lines = list(self.raw)
        while lines and lines[-1].strip() == "":
            lines.pop()
        with open("/etc/hosts", "w") as f:
            f.write("\n".join(lines) + "\n")

    def _save(self, msg):
        backup("/etc/hosts")
        self._write()
        self._reload()
        log(msg, "ok")
        self._refresh_listbox()

    def _refresh_listbox(self):
        self.listbox.delete(0, "end")
        self.listbox.selection_clear(0, "end")
        for _, ip, names in self.entries:
            self.listbox.insert("end", f"{ip:<16} {names}")

    def _detect_pcname(self):
        for c in (
            "hostname -s",
            "hostname",
            "hostnamectl --static",
            "cat /etc/hostname",
            "uname -n",
        ):
            try:
                pc = subprocess.run(
                    c, shell=True, capture_output=True, text=True
                ).stdout.strip()
                if pc:
                    return pc.split()[0]
            except Exception:
                pass
        return None

    def _free_ip(self):
        used = {e[1] for e in self.entries}
        for n in range(HOSTS_SEQ):
            ip = f"127.0.{n}.1"
            if ip not in used:
                return ip
        return "127.0.10.1"

    def _ipv6_start(self):
        for i, ln in enumerate(self.raw):
            s = ln.strip()
            if not s:
                continue
            if s.startswith("#"):
                if "ipv6" in s.lower():
                    return i
                continue
            first = ln.split()[0]
            if ":" in first:
                return i
        return None

    def _insert_ipv4_line(self, text):
        self._reload()
        for i, ln in enumerate(self.raw):
            parts = ln.split()
            if parts and parts[0] == "127.0.0.1":
                self.raw.insert(i + 1, text)
                return
        last127 = -1
        for i, ln in enumerate(self.raw):
            s = ln.strip()
            if s and not s.startswith("#") and s.split()[0].startswith("127."):
                last127 = i
        if last127 >= 0:
            self.raw.insert(last127 + 1, text)
            return
        idx = self._ipv6_start()
        if idx is not None:
            self.raw.insert(idx, text)
            return
        self.raw.append(text)

    def _add(self):
        self._reload()
        ip = ask_user("Add Entry", "IP address:", initial=self._free_ip())
        if not ip:
            return
        names = ask_user("Add Entry", "Hostnames (space-separated):")
        if not names:
            return
        new_names = names.split()

        indices = self._find_lines(ip)
        if indices:
            if not messagebox.askyesno(
                "IP already exists",
                f"{ip} is already in /etc/hosts.\n\nAdd the new hostname(s) to that existing line instead?",
                parent=self.win,
            ):
                return
            parts = self.raw[indices[0]].split()
            merged = []
            for n in parts[1:] + new_names:
                if n.lower() not in [m.lower() for m in merged]:
                    merged.append(n)
            self.raw[indices[0]] = f"{parts[0]}\t" + " ".join(merged)
            self._save(f"Merged {', '.join(new_names)} into {ip}")
            return

        all_names = []
        for _, eip, enames in self.entries:
            all_names += enames.split()
        dup = next(
            (
                n
                for n in new_names
                if n.lower() in [x.lower() for x in all_names]
            ),
            None,
        )
        if dup:
            messagebox.showwarning(
                "Duplicate hostname",
                f'"{dup}" already exists in /etc/hosts.',
                parent=self.win,
            )
            return

        self._insert_ipv4_line(f"{ip}\t{names}")
        self._save(f"Added entry: {ip}  {names}")

    def _edit(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo(
                "Edit",
                "Select an entry first (or double-click it)",
                parent=self.win,
            )
            return
        _, ip, names = self.entries[sel[0]]
        indices = self._find_lines(ip)
        if not indices:
            messagebox.showerror(
                "Edit", f"{ip} no longer exists in /etc/hosts", parent=self.win
            )
            return
        line_idx = indices[0]
        new_ip = ask_user("Edit Entry", "IP address:", initial=ip)
        if not new_ip:
            return
        new_names = ask_user(
            "Edit Entry", "Hostnames (space-separated):", initial=names
        )
        if not new_names:
            return
        others = self._find_lines(new_ip)
        if others and others[0] != line_idx:
            messagebox.showerror(
                "Edit Entry",
                f"{new_ip} is already used by another line",
                parent=self.win,
            )
            return
        self.raw[line_idx] = f"{new_ip}\t{new_names}"
        self._save(f"Edited entry: {new_ip}  {new_names}")

    def _delete(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Delete", "Select an entry first", parent=self.win)
            return
        _, ip, names = self.entries[sel[0]]
        if not messagebox.askyesno(
            "Delete Entry",
            f"Delete this line permanently?\n\n{ip}  {names}",
            icon="warning",
            parent=self.win,
        ):
            return
        indices = self._find_lines(ip)
        if not indices:
            messagebox.showerror(
                "Delete", f"{ip} no longer exists in /etc/hosts", parent=self.win
            )
            return
        for i in reversed(indices):
            del self.raw[i]
        self._save(f"Deleted {len(indices)} line(s): {ip}  {names}")
        self.listbox.selection_clear(0, "end")

    def _check_pcname(self):
        pc = self._detect_pcname()
        if not pc:
            pc = ask_user(
                "PC Name", "Could not auto-detect — type the PC name manually:"
            )
            if not pc:
                return
        else:
            log(f'Auto-detected PC name: "{pc}"')
        self._reload()
        for _, eip, enames in self.entries:
            if pc.lower() in [w.lower() for w in enames.split()]:
                log(f'PC name "{pc}" already exists in /etc/hosts', "ok")
                return
        log(f'PC name "{pc}" NOT found in /etc/hosts', "warn")
        if not messagebox.askyesno(
            "Add PC Name",
            f'Add "{pc}" using a free 127.0.x.1 address?',
            parent=self.win,
        ):
            return
        ip = self._free_ip()
        if not ip.startswith("127.0."):
            log("No available 127.0.x.1 address found", "warn")
            return
        self._insert_ipv4_line(f"{ip}\t{pc}")
        self._save(f'Added "{pc}" to {ip}')

    def _view_raw(self):
        try:
            with open("/etc/hosts") as f:
                content = f.read()
        except Exception as ex:
            messagebox.showerror("View Raw", str(ex), parent=self.win)
            return
        w = tk.Toplevel(self.win)
        w.title("/etc/hosts — raw content")
        w.geometry("640x540")
        w.configure(bg=NAVY_DARK)
        txt = scrolledtext.ScrolledText(
            w,
            bg="#0a0a18",
            fg=FG,
            insertbackground=FG,
            font=("monospace", 10),
            relief="flat",
            wrap="none",
        )
        txt.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        txt.insert("1.0", content)
        txt.configure(state="disabled")
        tk.Label(
            w,
            text="Exact file content (comments + blank lines included).",
            bg=NAVY_DARK,
            fg=MUTED,
            font=("Ubuntu", 9),
        ).pack(anchor="w", padx=12, pady=(0, 8))

    def _reload_btn(self):
        self._reload()
        self._refresh_listbox()
        log("/etc/hosts reloaded from disk", "ok")

    def _build_ui(self):
        self.win = tk.Toplevel(self.parent)
        self.win.title("Hosts Manager — /etc/hosts")
        self.win.geometry("820x500")
        self.win.configure(bg=NAVY_DARK)
        self.win.transient(self.parent)

        tk.Label(
            self.win,
            text="Current entries  •  double-click to edit",
            bg=NAVY_DARK,
            fg=MUTED,
            font=("Ubuntu", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))

        body = tk.Frame(self.win, bg=NAVY_DARK)
        body.pack(fill="both", expand=True, padx=14)
        self.listbox = tk.Listbox(
            body,
            bg="#0a0a18",
            fg=FG,
            selectbackground=GOLD,
            selectforeground=NAVY_DARK,
            relief="flat",
            font=("monospace", 10),
            highlightthickness=0,
        )
        sb = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda _: self._edit())

        btns = tk.Frame(self.win, bg=NAVY_DARK)
        btns.pack(fill="x", padx=14, pady=12)
        for text, cb, gold in (
            ("➕ Add", self._add, False),
            ("✏️ Edit", self._edit, False),
            ("🗑 Delete", self._delete, False),
            ("🔎 Check PC Name", self._check_pcname, False),
            ("👁 View Raw", self._view_raw, False),
            ("🔄 Reload", self._reload_btn, False),
            ("Close", self.win.destroy, True),
        ):
            tk.Button(
                btns,
                text=text,
                command=cb,
                bg=GOLD if gold else PANEL,
                fg=NAVY_DARK if gold else FG,
                activebackground="#f0d080" if gold else PANEL_HI,
                relief="flat",
                padx=10,
                pady=6,
                font=("Ubuntu", 9, "bold" if gold else "normal"),
                cursor="hand2",
            ).pack(side="left" if not gold else "right", padx=3)

        self._refresh_listbox()


def dom_hosts():
    HostsEditor(root)


def dom_dns():
    run_cmd(
        "rm -f /etc/resolv.conf && ln -s /run/systemd/resolve/resolv.conf /etc/resolv.conf && "
        "systemctl restart systemd-resolved.service && echo '--- /etc/resolv.conf ---' && "
        "cat /etc/resolv.conf"
    )


def dom_updates():
    run_cmd(
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update && apt-get install -f -y && apt-get autoremove -y && apt-get upgrade -y && "
        "apt-get install -y sssd-ad sssd-tools realmd adcli krb5-user libpam-krb5 libpam-ccreds && "
        "(apt-get install -y krb5-k5tls || echo 'krb5-k5tls unavailable — continuing (normal on 22.04/Zorin 17)')"
    )


def dom_realm_join():
    if (
        subprocess.run(
            "realm list 2>/dev/null | grep -qi 'aman.local'", shell=True
        ).returncode
        == 0
    ):
        log(f"Machine is already joined to {DOMAIN} — skipping")
        return
    user = ask_user(
        "Domain Join",
        f"Domain username to join with [Enter = {DOMAIN_USER}]:",
        initial=DOMAIN_USER,
    )
    if not user:
        return
    pw = ask_password("Domain Join", f"Password for {user}@{DOMAIN}:")
    if pw is None:
        return
    run_cmd(
        f"realm join {DOMAIN} --user={shlex.quote(user)} && pam-auth-update --enable mkhomedir && "
        f"echo 'Joined {DOMAIN} as {user}; mkhomedir enabled.'",
        with_password=pw,
    )


def dom_realm_leave():
    if (
        subprocess.run(
            "realm list 2>/dev/null | grep -qi 'aman.local'", shell=True
        ).returncode
        != 0
    ):
        log(f"Machine is not joined to {DOMAIN} — nothing to leave", "warn")
        return
    if not messagebox.askyesno(
        "Leave Domain",
        f"Leave domain '{DOMAIN}'?\n\nMachine account will be removed.",
        icon="warning",
        parent=root,
    ):
        return
    run_cmd(f"realm leave {DOMAIN} && echo 'Left {DOMAIN}.'")


SSSD_CONF = """[sssd]
domains = aman.local
config_file_version = 2
services = nss, pam

[domain/aman.local]
default_shell = /bin/bash
dns_discovery_domain = aman.local
ad_server = dc.aman.local,adc.aman.local
krb5_store_password_if_offline = True
cache_credentials = True
krb5_realm = aman.local
realmd_tags = manages-system joined-with-adcli
id_provider = ad
fallback_homedir = /home/%u@%d
ad_domain = aman.local
use_fully_qualified_names = True
ldap_id_mapping = True
access_provider = ad
ad_gpo_ignore_unreadable = True
ad_gpo_access_control = permissive
"""

KRB5_LOCAL = """[libdefaults]
    default_realm = AMAN.LOCAL
    rdns = false

[realms]
    AMAN.LOCAL = {
        kdc = dc.aman.local
        kdc = adc.aman.local
        admin_server = dc.aman.local
    }

[domain_realm]
    .aman.local = AMAN.LOCAL
    aman.local = AMAN.LOCAL
"""

KRB5_PROXY = """[libdefaults]
    default_realm = AMAN.LOCAL
    rdns = false
    dns_lookup_kdc = false
    dns_lookup_realm = false
    ticket_lifetime = 24h
    renew_lifetime = 7d
    forwardable = true

[realms]
    AMAN.LOCAL = {
        kdc = https://kdc.eg-aman.com/KdcProxy
        admin_server = kdc.eg-aman.com
        http_anchors = FILE:/etc/ssl/certs/ca-certificates.crt
    }

[domain_realm]
    .aman.local = AMAN.LOCAL
    aman.local = AMAN.LOCAL
"""


def dom_sssd_krb5():
    backup("/etc/sssd/sssd.conf")
    with open("/etc/sssd/sssd.conf", "w") as f:
        f.write(SSSD_CONF)
    os.chmod("/etc/sssd/sssd.conf", 0o600)
    backup("/etc/krb5.conf")
    with open("/etc/krb5.conf", "w") as f:
        f.write(KRB5_LOCAL)
    log("sssd.conf + krb5.conf written (local DCs), sssd.conf mode 600")
    run_cmd("systemctl restart sssd.service && echo 'sssd restarted.'")


def dom_krb5_proxy():
    backup("/etc/krb5.conf")
    with open("/etc/krb5.conf", "w") as f:
        f.write(KRB5_PROXY)
    log("krb5.conf written (KDC proxy)")
    run_cmd("systemctl restart sssd.service && echo 'sssd restarted.'")


def dom_resolved():
    conf = "/etc/systemd/resolved.conf"
    backup(conf)
    with open(conf) as f:
        lines = f.read().splitlines()
    if any(l.strip() == "DNSStubListener=no" for l in lines):
        log("Solved Already")
        return
    changed = False
    for i, l in enumerate(lines):
        import re

        if re.match(r"^\s*#?\s*DNSStubListener=", l):
            lines[i] = "DNSStubListener=no"
            changed = True
    if not changed:
        lines.append("DNSStubListener=no")
    with open(conf, "w") as f:
        f.write("\n".join(lines) + "\n")
    log("DNSStubListener=no set")
    run_cmd(
        "systemctl restart systemd-resolved.service && echo 'resolved restarted.'"
    )


# ================================================================
#  APPS & MAINTENANCE — Maintenance & Health
# ================================================================


def sys_clear_browser_data():
    user = ask_user("Clear Browser Data", "Username to reset Chrome profile data:")
    if not user:
        return
    if not user_exists(user):
        log(f"User '{user}' does not exist", "err")
        return
    if messagebox.askyesno(
        "Clear Browser Data",
        f"Clear Google Chrome cache, cookies, and local profile data for user '{user}'?\n\nUnlinks active logins.",
        icon="warning",
        parent=root,
    ):
        run_cmd(
            f"rm -rf /home/{shlex.quote(user)}/.cache/google-chrome/ "
            f"/home/{shlex.quote(user)}/.config/google-chrome/ && "
            f"echo 'Google Chrome cache and settings cleared for /home/{user}.'"
        )


def sys_cleanup():
    run_cmd(
        "apt-get autoremove -y && apt-get clean && journalctl --vacuum-time=7d && "
        "echo 'System cache, orphaned packages, and old journal logs cleaned.'"
    )


def sys_sync_time():
    run_cmd(
        "systemctl restart systemd-timesyncd && timedatectl set-ntp true && "
        "echo 'Time synchronized with NTP server.' && timedatectl status | grep -i 'synchronized'"
    )


def diag_domain_health():
    run_cmd(
        "echo '--- DNS Query aman.local ---'; resolvectl query aman.local 2>/dev/null || nslookup aman.local; "
        "echo '\n--- Ping Domain Controller ---'; ping -c 2 dc.aman.local 2>/dev/null || echo 'DC unreachable via ping'; "
        "echo '\n--- Active Directory Status ---'; realm list; "
        "echo '\n--- Active Kerberos Tickets ---'; klist 2>/dev/null || echo 'No active root ticket'"
    )


def sys_restart_network():
    run_cmd(
        "systemctl restart NetworkManager && echo 'NetworkManager service restarted successfully.'"
    )


def maint_check_domain():
    run_cmd(
        "echo '--- Realm (Domain) Status ---'; realm list; "
        "echo; echo 'Done.'"
    )


def maint_fix_os_packages():
    run_cmd(
        "echo '--- Fixing OS packages (dpkg configure + apt fix-broken) ---'; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "dpkg --configure -a; "
        "apt --fix-broken install -y; "
        "echo 'OS package fix complete.'"
    )


# ================================================================
#  UI SETUP
# ================================================================
root = tk.Tk()
root.title("Aman — Extension Tool")
root.geometry("880x720")
root.configure(bg=NAVY_DARK)

style = ttk.Style()
style.theme_use("clam")
style.configure("TNotebook", background=NAVY_DARK, borderwidth=0)
style.configure(
    "TNotebook.Tab",
    background=PANEL,
    foreground=MUTED,
    padding=(18, 10),
    font=("Ubuntu", 11, "bold"),
    borderwidth=0,
)
style.map(
    "TNotebook.Tab",
    background=[("selected", GOLD)],
    foreground=[("selected", NAVY_DARK)],
)
style.configure("TFrame", background=NAVY_DARK)
style.configure(
    "TButton",
    background=PANEL,
    foreground=BTN_TXT,
    borderwidth=0,
    padding=(14, 9),
    font=("Ubuntu", 10),
    anchor="w",
)
style.map(
    "TButton",
    background=[("active", PANEL_HI), ("disabled", "#191931")],
    foreground=[("disabled", "#5a5f78")],
)
style.configure(
    "Accent.TButton",
    background=GOLD,
    foreground=NAVY_DARK,
    font=("Ubuntu", 10, "bold"),
)

# ---------- Header ----------
header = tk.Frame(root, bg=NAVY_DARK)
header.pack(fill="x", padx=18, pady=(16, 6))

logo = tk.Canvas(header, width=74, height=56, bg=NAVY_DARK, highlightthickness=0)
logo.create_rectangle(2, 2, 72, 54, fill="#1e3a8a", outline="", width=0)
logo.create_text(37, 28, text="Aman", fill="white", font=("Ubuntu", 17, "bold"))
logo.pack(side="left")

titles = tk.Frame(header, bg=NAVY_DARK)
titles.pack(side="left", padx=12)
tk.Label(
    titles,
    text="Aman Extension Tool",
    bg=NAVY_DARK,
    fg="white",
    font=("Ubuntu", 16, "bold"),
).pack(anchor="w")
tk.Label(
    titles,
    text="System configuration tool  •  aman.eg  •  domain: aman.local",
    bg=NAVY_DARK,
    fg=MUTED,
    font=("Ubuntu", 9),
).pack(anchor="w")

tk.Frame(root, bg=GOLD, height=2).pack(fill="x", padx=18)

# ---------- Notebook ----------
nb = ttk.Notebook(root)
nb.pack(fill="both", expand=True, padx=14, pady=10)


def make_tab(parent, actions):
    fr = ttk.Frame(parent)
    fr.columnconfigure(0, weight=1)
    fr.columnconfigure(1, weight=1)
    btns = []
    for i, (text, cb) in enumerate(actions):
        b = ttk.Button(fr, text=text, command=cb)
        b.grid(row=i // 2, column=i % 2, sticky="ew", padx=6, pady=5)
        btns.append(b)
    return fr, btns


# Tab 1: System Setup
tab_sys, sys_btns = make_tab(
    nb,
    [
        ("⚙  Enable FortiClient", sys_enable_forti),
        ("🗂  Home Permissions (744)", sys_fix_permissions),
        ("🖥  Hardware Info", sys_hardware_info),
        ("👤  Add Administrator", sys_add_admin),
        ("📶  Enable Hidden Wi-Fi", sys_enable_wifi),
        ("⚙  Configure GDM3", sys_configure_gdm3),
        ("🗑  Remove User", sys_remove_user),
    ],
)
nb.add(tab_sys, text="  System Setup  ")

# Tab 2: Domain Setup
tab_dom, dom_btns = make_tab(
    nb,
    [
        ("1  Hosts: Edit / Add / Check", dom_hosts),
        ("2  Fix resolv.conf + resolved", dom_dns),
        ("3  Updates + AD Packages", dom_updates),
        ("4  Join Domain (aman.local)", dom_realm_join),
        ("Leave Domain (aman.local)", dom_realm_leave),
        ("5  sssd.conf + krb5.conf (Local DCs)", dom_sssd_krb5),
        ("6  krb5.conf (KDC Proxy)", dom_krb5_proxy),
        ("7  resolved.conf: DNSStubListener", dom_resolved),
    ],
)
nb.add(tab_dom, text="  Domain Setup  ")

# Tab 3: Apps & Maintenance
tab_apps = ttk.Frame(nb)
tab_apps.columnconfigure(0, weight=1)

# Maintenance Grid Frame
maint_frame = tk.Frame(tab_apps, bg=NAVY_DARK)
maint_frame.pack(fill="x", padx=2, pady=4)
maint_frame.columnconfigure(0, weight=1)
maint_frame.columnconfigure(1, weight=1)

maint_actions = [
    ("🌐  Check Domain (realm list)", maint_check_domain),
    ("🩺  Domain & Network Health Check", diag_domain_health),
    ("🔧  Fix OS Packages (dpkg/apt fix-broken)", maint_fix_os_packages),
    ("⏱️  Sync NTP Time (Fix Kerberos)", sys_sync_time),
    ("🧹  System Cleanup (Cache & Logs)", sys_cleanup),
    ("🗑  Clear User Chrome Data", sys_clear_browser_data),
    ("🌐  Restart NetworkManager", sys_restart_network),
]

apps_maint_btns = []
for idx, (lbl, fn) in enumerate(maint_actions):
    b = ttk.Button(maint_frame, text=lbl, command=fn)
    b.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=4, pady=3)
    apps_maint_btns.append(b)

nb.add(tab_apps, text="  Apps & Maintenance  ")

buttons = sys_btns + dom_btns + apps_maint_btns

# ---------- Log Pane ----------
log_label = tk.Frame(root, bg=NAVY_DARK)
log_label.pack(fill="x", padx=14)
tk.Label(
    log_label,
    text="Output log",
    bg=NAVY_DARK,
    fg=MUTED,
    font=("Ubuntu", 9, "bold"),
).pack(side="left")

stop_btn = tk.Button(
    log_label,
    text="⏹  Stop Command",
    command=stop_cmd,
    state="disabled",
    bg=ERR,
    fg="white",
    activebackground="#b91c1c",
    relief="flat",
    padx=10,
    pady=0,
    font=("Ubuntu", 8, "bold"),
    cursor="hand2",
)
stop_btn.pack(side="right", padx=(0, 8))

tk.Button(
    log_label,
    text="Clear",
    command=lambda: (
        output.configure(state="normal"),
        output.delete("1.0", "end"),
        output.configure(state="disabled"),
    ),
    bg=PANEL,
    fg=FG,
    activebackground=PANEL_HI,
    relief="flat",
    padx=8,
    pady=0,
    font=("Ubuntu", 8),
    cursor="hand2",
).pack(side="right")

# Extended Output log height to 18 lines
output = scrolledtext.ScrolledText(
    root,
    bg="#0a0a18",
    fg=FG,
    insertbackground=FG,
    wrap="word",
    state="disabled",
    font=("monospace", 10),
    relief="flat",
    height=18,
)
output.pack(fill="both", expand=True, padx=14, pady=(2, 6))

# ---------- Interactive command input (Y/N prompts) ----------
input_bar = tk.Frame(root, bg=NAVY_DARK)
input_bar.pack(fill="x", side="bottom", padx=14, pady=(0, 6))
tk.Label(
    input_bar,
    text="Input →",
    bg=NAVY_DARK,
    fg=MUTED,
    font=("Ubuntu", 9, "bold"),
).pack(side="left")
input_entry = tk.Entry(
    input_bar,
    bg=PANEL,
    fg=FG,
    insertbackground=FG,
    relief="flat",
    font=("monospace", 10),
    state="disabled",
)
input_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
send_btn = tk.Button(
    input_bar,
    text="Send ⏎",
    command=lambda: send_input(input_entry.get(), input_entry),
    bg=GOLD,
    fg=NAVY_DARK,
    activebackground="#f0d080",
    relief="flat",
    padx=12,
    pady=2,
    font=("Ubuntu", 9, "bold"),
    cursor="hand2",
)
send_btn.pack(side="left")
input_entry.bind("<Return>", lambda _: send_input(input_entry.get(), input_entry))

# ---------- Status Bar ----------
status = tk.Label(
    root,
    text="Ready",
    bg=GOLD_DIM,
    fg="white",
    anchor="w",
    font=("Ubuntu", 9, "bold"),
    padx=14,
    pady=4,
)
status.pack(fill="x", side="bottom")

log("Aman Extension Tool Initialized", "ok")
log(
    "System Setup: 7 tools   •   Domain Setup: 7 steps   •   Apps & Maintenance: Maintenance & Health",
    "info",
)

# Automated Background GitHub Release Check
check_github_updates()

poll_queue()
root.mainloop()
