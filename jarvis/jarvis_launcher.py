"""
Offline deterministic Windows Jarvis launcher.

Features
--------
OPEN:
    open chrome
    launch chrome
    start chrome
    open calculator
    open notepad
    open vs code
    open file explorer

CLOSE:
    close chrome
    close calculator
    close notepad
    close vs code
    close file explorer

CLOSE:
    close chrome
    exit chrome
    quit chrome
    stop chrome

SEARCH:
    find resume
    locate resume
    where is my resume
    search for project report

Important:
    Jarvis persistently tracks the PID returned when it launches an application.
    Tracking is stored in jarvis_tracking.json beside this file so separate
    JARVIS/test processes can safely close only applications JARVIS opened.
    For PID-tracked apps, executable path and process start time are also saved
    so a reused PID is rejected instead of terminating the wrong process.

    It does NOT use:
        taskkill /IM chrome.exe /T /F

    Therefore it will not intentionally kill every Chrome process.
"""

from __future__ import annotations

import difflib
import json
import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import time
from pathlib import Path


# ============================================================
# APPLICATION ALIASES
# ============================================================

APP_ALIASES = {
    # Chrome
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "crome": "chrome.exe",

    # Notepad
    "notepad": "notepad.exe",

    # Calculator
    "calculator": "calc.exe",
    "calc": "calc.exe",

    # File Explorer
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",

    # Visual Studio Code
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "vscode": "Code.exe",
    "code": "Code.exe",
}


# ============================================================
# KNOWN WINDOWS EXECUTABLE LOCATIONS
# ============================================================

def find_chrome() -> str | None:

    candidates = [
        os.path.expandvars(
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
        ),
    ]

    for path in candidates:

        if path and os.path.isfile(path):
            return path

    return None


def find_vscode() -> str | None:

    candidates = [
        os.path.expandvars(
            r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Microsoft VS Code\Code.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe"
        ),
    ]

    for path in candidates:

        if path and os.path.isfile(path):
            return path

    return None


def resolve_known_executable(
    target: str,
) -> str | None:
    target = normalize_text(target)

    if target in {
        "chrome",
        "google chrome",
        "crome",
    }:
        return find_chrome()

    if target in {
        "vs code",
        "visual studio code",
        "vscode",
        "code",
    }:
        return find_vscode()

    system_root = os.environ.get(
        "SystemRoot",
        r"C:\Windows",
    )

    if target == "notepad":
        path = os.path.join(system_root, "System32", "notepad.exe")
        if os.path.isfile(path):
            return path

    if target in {"calculator", "calc"}:
        path = os.path.join(system_root, "System32", "calc.exe")
        if os.path.isfile(path):
            return path
        return "calc.exe"

    if target in {"explorer", "file explorer"}:
        path = os.path.join(system_root, "explorer.exe")
        if os.path.isfile(path):
            return path
        return "explorer.exe"

    return None


# ============================================================
# TRACKED PROCESSES / WINDOWS
# ============================================================

# Normal applications are tracked by PID. Chrome is special: Chrome is a
# multi-process application, so killing a Chrome PID can close an unrelated
# Chrome session. Modern Windows 11 Notepad can also use/recreate processes, so
# Notepad is tracked by its exact top-level window when possible. For these apps
# we remember the exact top-level window
# (HWND) that Jarvis opened and close that window with WM_CLOSE.
TRACKED_PROCESSES: dict[str, list[int]] = {}
TRACKED_WINDOWS: dict[str, list[int]] = {}
TRACKED_PROCESS_META: dict[str, dict[str, dict[str, str]]] = {}

# Persistent tracking file. This allows a later JARVIS process (or a separate
# PowerShell test command) to know which PIDs/HWNDs were opened by JARVIS.
# The file contains only process IDs/window handles and is never used to
# discover arbitrary processes.
TRACKING_FILE = Path(__file__).resolve().with_name("jarvis_tracking.json")

user32 = ctypes.windll.user32
WM_CLOSE = 0x0010
GW_OWNER = 4


def enumerate_windows_for_pids(pids: set[int]) -> list[int]:
    """Return visible top-level windows belonging to any PID in pids."""
    hwnds: list[int] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids:
            hwnds.append(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return hwnds


def get_chrome_pids() -> set[int]:
    """Return current Chrome process IDs without touching/killing them."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-Process chrome -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty Id",
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return {
            int(line.strip())
            for line in result.stdout.splitlines()
            if line.strip().isdigit()
        }
    except Exception:
        return set()


def snapshot_chrome_windows() -> set[int]:
    """Snapshot visible Chrome top-level windows before a new launch."""
    return set(enumerate_windows_for_pids(get_chrome_pids()))


def find_new_chrome_window(before: set[int]) -> int | None:
    """Wait briefly for a Chrome window that did not exist before launch."""
    for _ in range(40):
        current = set(enumerate_windows_for_pids(get_chrome_pids()))
        new_windows = current - before
        if new_windows:
            return next(iter(new_windows))
        time.sleep(0.20)
    return None


def get_process_ids_by_name(process_name: str) -> set[int]:
    """Return process IDs for a process image name without killing anything."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty Id",
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return {
            int(line.strip())
            for line in result.stdout.splitlines()
            if line.strip().isdigit()
        }
    except Exception:
        return set()


def snapshot_explorer_windows() -> set[int]:
    """Snapshot visible Windows Explorer top-level windows before launch."""
    return set(enumerate_windows_for_pids(get_process_ids_by_name("explorer")))


def find_new_explorer_window(before: set[int]) -> int | None:
    """Wait briefly for an Explorer window that did not exist before launch."""
    for _ in range(40):
        current = set(enumerate_windows_for_pids(get_process_ids_by_name("explorer")))
        new_windows = current - before
        if new_windows:
            return next(iter(new_windows))
        time.sleep(0.20)
    return None


def snapshot_notepad_windows() -> set[int]:
    """Snapshot visible Notepad top-level windows before launch."""
    return set(enumerate_windows_for_pids(get_process_ids_by_name("notepad")))


def find_new_notepad_window(before: set[int]) -> int | None:
    """Wait briefly for a Notepad window that did not exist before launch."""
    for _ in range(40):
        current = set(enumerate_windows_for_pids(get_process_ids_by_name("notepad")))
        new_windows = current - before
        if new_windows:
            return next(iter(new_windows))
        time.sleep(0.20)
    return None


def window_exists(hwnd: int) -> bool:
    try:
        return bool(user32.IsWindow(hwnd))
    except Exception:
        return False


def close_window(hwnd: int) -> bool:
    """Gracefully close one exact top-level Windows window."""
    if not window_exists(hwnd):
        return False

    try:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        for _ in range(30):
            time.sleep(0.10)
            if not window_exists(hwnd):
                return True
    except Exception as exc:
        print(f"[jarvis] Window close failed: {exc}")

    return not window_exists(hwnd)

# ============================================================
# COMMAND TRIGGERS
# ============================================================

OPEN_TRIGGERS = (
    "open",
    "launch",
    "start",
    "run",
)

CLOSE_TRIGGERS = (
    "close",
    "exit",
    "quit",
    "stop",
)

LOCATE_TRIGGERS = (
    "locate",
    "find",
    "where is",
    "search for",
    "show me",
)


# ============================================================
# FILLER WORDS
# ============================================================

FILLER_WORDS = {
    "the",
    "my",
    "please",
    "for",
    "me",
    "a",
    "an",
}


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(
    text: str,
) -> str:

    text = text.lower().strip()

    text = re.sub(
        r"[^\w\s.-]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# APPLICATION DATABASE
# ============================================================

# Jarvis intentionally uses a whitelist instead of scanning the Windows
# registry. This prevents arbitrary installed programs from becoming
# launchable just because they appear in the registry.
def build_app_database() -> dict[str, str]:
    return dict(APP_ALIASES)


APP_DATABASE = build_app_database()


# ============================================================
# FUZZY MATCHING
# ============================================================

def best_match(
    target: str,
    database: dict[str, str],
):

    target = normalize_text(
        target
    )

    if not target:
        return None

    if target in database:
        return target

    names = list(
        database.keys()
    )

    matches = difflib.get_close_matches(
        target,
        names,
        n=1,
        cutoff=0.60,
    )

    if matches:
        return matches[0]

    return None


# ============================================================
# OPEN COMMAND PARSER
# ============================================================

def extract_open_target(
    command: str,
) -> str:

    text = normalize_text(
        command
    )

    for trigger in OPEN_TRIGGERS:

        prefix = trigger + " "

        if text.startswith(prefix):

            text = text[
                len(prefix):
            ].strip()

            break

    words = [
        word
        for word in text.split()
        if word not in FILLER_WORDS
    ]

    return " ".join(
        words
    ).strip()


# ============================================================
# CLOSE COMMAND PARSER
# ============================================================

def is_close_command(
    command: str,
) -> bool:

    text = normalize_text(
        command
    )

    for trigger in CLOSE_TRIGGERS:

        if text == trigger:
            return True

        if text.startswith(
            trigger + " "
        ):
            return True

    return False


def extract_close_target(
    command: str,
) -> str:

    text = normalize_text(
        command
    )

    for trigger in CLOSE_TRIGGERS:

        prefix = trigger + " "

        if text.startswith(prefix):

            text = text[
                len(prefix):
            ].strip()

            break

    words = [
        word
        for word in text.split()
        if word not in FILLER_WORDS
    ]

    return " ".join(
        words
    ).strip()


# ============================================================
# PROCESS CHECK
# ============================================================

def process_exists(
    pid: int,
) -> bool:

    try:

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"(Get-Process -Id {pid} "
                    f"-ErrorAction SilentlyContinue) "
                    f"-ne $null"
                ),
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        return (
            result.stdout.strip().lower()
            == "true"
        )

    except Exception:

        return False


# ============================================================
# PERSISTENT SAFE TRACKING
# ============================================================

def save_tracking_state():
    """Atomically save JARVIS-owned PIDs/HWNDs for future processes."""
    data = {
        "processes": {
            name: [int(pid) for pid in pids]
            for name, pids in TRACKED_PROCESSES.items()
            if pids
        },
        "windows": {
            name: [int(hwnd) for hwnd in hwnds]
            for name, hwnds in TRACKED_WINDOWS.items()
            if hwnds
        },
        "process_meta": TRACKED_PROCESS_META,
    }

    temp_file = TRACKING_FILE.with_suffix(".tmp")
    try:
        temp_file.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_file, TRACKING_FILE)
    except Exception as exc:
        print(f"[jarvis] Warning: could not save tracking state: {exc}")
        try:
            if temp_file.exists():
                temp_file.unlink()
        except OSError:
            pass


def load_tracking_state():
    """Load only previously recorded JARVIS tracking data."""
    global TRACKED_PROCESSES, TRACKED_WINDOWS, TRACKED_PROCESS_META

    if not TRACKING_FILE.exists():
        return

    try:
        data = json.loads(TRACKING_FILE.read_text(encoding="utf-8"))
        processes = data.get("processes", {})
        windows = data.get("windows", {})
        process_meta = data.get("process_meta", {})

        if isinstance(processes, dict):
            TRACKED_PROCESSES = {
                str(name): [int(pid) for pid in pids if str(pid).isdigit()]
                for name, pids in processes.items()
                if isinstance(pids, list)
            }

        if isinstance(windows, dict):
            TRACKED_WINDOWS = {
                str(name): [int(hwnd) for hwnd in hwnds if str(hwnd).isdigit()]
                for name, hwnds in windows.items()
                if isinstance(hwnds, list)
            }

        if isinstance(process_meta, dict):
            TRACKED_PROCESS_META = {
                str(name): {
                    str(pid): {
                        "path": str(info.get("path", "")),
                        "start_time": str(info.get("start_time", "")),
                    }
                    for pid, info in entries.items()
                    if isinstance(info, dict)
                }
                for name, entries in process_meta.items()
                if isinstance(entries, dict)
            }

    except Exception as exc:
        print(f"[jarvis] Warning: could not load tracking state: {exc}")
        TRACKED_PROCESSES = {}
        TRACKED_WINDOWS = {}
        TRACKED_PROCESS_META = {}


def clear_tracking_file_if_empty():
    """Remove the state file when JARVIS owns no live tracked objects."""
    if TRACKED_PROCESSES or TRACKED_WINDOWS:
        save_tracking_state()
        return

    try:
        if TRACKING_FILE.exists():
            TRACKING_FILE.unlink()
    except OSError as exc:
        print(f"[jarvis] Warning: could not remove tracking file: {exc}")


def initialize_tracking():
    """Load and clean persisted state at module startup."""
    load_tracking_state()
    clean_tracked_processes()
    clean_tracked_windows()
    clear_tracking_file_if_empty()


def get_process_identity(pid: int) -> tuple[str, str] | None:
    """Return (executable path, process creation time) for one PID."""
    try:
        ps = (
            f"$p=Get-CimInstance Win32_Process -Filter "
            f"\"ProcessId = {int(pid)}\" -ErrorAction Stop; "
            f"if ($null -eq $p) {{ exit 2 }}; "
            f"[PSCustomObject]@{{Path=$p.ExecutablePath;StartTime=[string]$p.CreationDate}} | "
            "ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        return str(data.get("Path") or ""), str(data.get("StartTime") or "")
    except Exception:
        return None


def tracked_pid_is_same_process(app_name: str, pid: int) -> bool:
    """Reject a reused PID when its executable identity is known and differs."""
    meta = TRACKED_PROCESS_META.get(app_name, {}).get(str(pid))
    if not meta:
        return True

    current = get_process_identity(pid)
    if current is None:
        # Keep the tracking record if Windows temporarily denies metadata access.
        return True

    stored_path = meta.get("path", "").lower()
    stored_start = meta.get("start_time", "")
    current_path, current_start = current

    if stored_path and current_path and current_path.lower() != stored_path:
        return False
    if stored_start and current_start and current_start != stored_start:
        return False
    return True


# ============================================================
# CLEAN DEAD PROCESSES
# ============================================================

def clean_tracked_processes():

    changed = False

    for name in list(
        TRACKED_PROCESSES.keys()
    ):

        alive = []

        for pid in TRACKED_PROCESSES[name]:

            if process_exists(pid) and tracked_pid_is_same_process(name, pid):

                alive.append(pid)
            else:
                TRACKED_PROCESS_META.get(name, {}).pop(str(pid), None)
                changed = True

        if alive:

            TRACKED_PROCESSES[name] = alive

        else:

            del TRACKED_PROCESSES[name]
            TRACKED_PROCESS_META.pop(name, None)
            changed = True

    if changed:
        save_tracking_state()


def clean_tracked_windows():
    changed = False
    for name in list(TRACKED_WINDOWS.keys()):
        alive = [hwnd for hwnd in TRACKED_WINDOWS[name] if window_exists(hwnd)]
        if alive:
            if len(alive) != len(TRACKED_WINDOWS[name]):
                changed = True
            TRACKED_WINDOWS[name] = alive
        else:
            del TRACKED_WINDOWS[name]
            changed = True

    if changed:
        save_tracking_state()


# Load persisted state after all tracking/cleanup helpers exist.
initialize_tracking()


# ============================================================
# TRACK PROCESS
# ============================================================

def track_process(
    app_name: str,
    process: subprocess.Popen,
):

    app_name = normalize_text(
        app_name
    )

    pid = process.pid

    TRACKED_PROCESSES.setdefault(
        app_name,
        [],
    )

    if pid not in TRACKED_PROCESSES[
        app_name
    ]:

        TRACKED_PROCESSES[
            app_name
        ].append(pid)

    identity = get_process_identity(pid)
    if identity is not None:
        path, start_time = identity
        TRACKED_PROCESS_META.setdefault(app_name, {})[str(pid)] = {
            "path": path,
            "start_time": start_time,
        }

    save_tracking_state()

    print(
        f"[jarvis] Tracking "
        f"'{app_name}' PID={pid}"
    )


# ============================================================
# TERMINATE ONE PID
# ============================================================

def terminate_pid(
    pid: int,
) -> bool:

    try:

        # Graceful termination.
        result = subprocess.run(
            [
                "taskkill",
                "/PID",
                str(pid),
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        time.sleep(
            0.30
        )

        if not process_exists(pid):

            return True

        # Force ONLY this PID.
        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(pid),
                "/F",
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        time.sleep(
            0.30
        )

        return not process_exists(pid)

    except Exception as exc:

        print(
            f"[jarvis] PID termination "
            f"failed: {exc}"
        )

        return False


# ============================================================
# CLOSE APPLICATION
# ============================================================

def close_application(
    target: str,
) -> bool:
    target = normalize_text(target)

    if not target:
        print("[jarvis] No close target.")
        return False

    clean_tracked_processes()
    clean_tracked_windows()

    matched_name = target

    if target not in APP_ALIASES:
        matched = best_match(target, APP_ALIASES)
        if matched:
            matched_name = matched

    # Use one tracking key for aliases.
    if matched_name in {"google chrome", "crome"}:
        matched_name = "chrome"
    elif matched_name in {"calculator", "calc"}:
        matched_name = "calculator"
    elif matched_name in {"vs code", "visual studio code", "vscode", "code"}:
        matched_name = "vs code"
    elif matched_name == "file explorer":
        matched_name = "explorer"

    # Chrome, Notepad, and Explorer are window-based where possible.
    # This is especially important for modern Windows 11 Notepad, where
    # terminating one PID can leave/recreate another process for the same app.
    # Explorer is also the Windows shell, so it must never be task-killed.
    if matched_name in {"chrome", "notepad", "explorer", "file explorer"}:
        window_key = "chrome" if matched_name == "chrome" else (
            "notepad" if matched_name == "notepad" else "explorer"
        )
        hwnds = TRACKED_WINDOWS.get(window_key, [])

        if not hwnds:
            print(f"[jarvis] No tracked {window_key} window.")
            print(f"[jarvis] Refusing to close an untracked {window_key} window.")
            return False

        success = False
        remaining = []

        for hwnd in hwnds:
            if not window_exists(hwnd):
                continue

            print(f"[jarvis] Closing tracked {window_key} window HWND={hwnd}...")
            if close_window(hwnd):
                print(f"[jarvis] Closed {window_key} window (HWND={hwnd})")
                success = True
            else:
                remaining.append(hwnd)

        if remaining:
            TRACKED_WINDOWS[window_key] = remaining
        else:
            TRACKED_WINDOWS.pop(window_key, None)

        if window_key == "chrome":
            TRACKED_PROCESSES.pop("chrome", None)
            TRACKED_PROCESS_META.pop("chrome", None)
        elif window_key == "notepad":
            TRACKED_PROCESSES.pop("notepad", None)
            TRACKED_PROCESS_META.pop("notepad", None)
        else:
            TRACKED_PROCESSES.pop("explorer", None)
            TRACKED_PROCESSES.pop("file explorer", None)
            TRACKED_PROCESS_META.pop("explorer", None)
            TRACKED_PROCESS_META.pop("file explorer", None)

        clear_tracking_file_if_empty()
        save_tracking_state() if (TRACKED_PROCESSES or TRACKED_WINDOWS) else None
        return success

    pids = TRACKED_PROCESSES.get(matched_name, [])

    if not pids:
        print(f"[jarvis] No tracked PID for '{matched_name}'.")
        print("[jarvis] Refusing to kill untracked processes.")
        return False

    print(
        f"[jarvis] Closing tracked '{matched_name}' PID(s): {pids}"
    )

    success = False
    remaining = []

    for pid in pids:
        if not process_exists(pid):
            continue

        if not tracked_pid_is_same_process(matched_name, pid):
            print(
                f"[jarvis] PID={pid} no longer matches the process Jarvis opened. "
                "Refusing to terminate it."
            )
            continue

        if terminate_pid(pid):
            print(f"[jarvis] Closed '{matched_name}' (PID={pid})")
            success = True
        else:
            remaining.append(pid)

    if remaining:
        TRACKED_PROCESSES[matched_name] = remaining
    else:
        TRACKED_PROCESSES.pop(matched_name, None)
        TRACKED_PROCESS_META.pop(matched_name, None)
    for pid in list(TRACKED_PROCESS_META.get(matched_name, {}).keys()):
        if int(pid) not in remaining:
            TRACKED_PROCESS_META[matched_name].pop(pid, None)

    clear_tracking_file_if_empty()
    if TRACKED_PROCESSES or TRACKED_WINDOWS:
        save_tracking_state()

    return success


# ============================================================
# SEARCH LOCATIONS
# ============================================================

def search_locations():

    home = Path.home()

    locations = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Pictures",
        home / "Videos",
        home / "Music",
    ]

    return [
        path
        for path in locations
        if path.exists()
    ]


# ============================================================
# FIND FILES
# ============================================================

def find_files(
    target: str,
):

    target = normalize_text(
        target
    )

    if not target:
        return []

    results = []

    for location in search_locations():

        try:

            for path in location.rglob("*"):

                if not path.is_file():
                    continue

                filename = normalize_text(
                    path.name
                )

                if target in filename:

                    results.append(
                        path
                    )

                    if len(results) >= 20:

                        return results

        except (
            PermissionError,
            OSError,
        ):

            continue

    if not results:

        try:

            for path in Path.home().rglob("*"):

                if not path.is_file():
                    continue

                filename = normalize_text(
                    path.name
                )

                if target in filename:

                    results.append(
                        path
                    )

                    if len(results) >= 20:

                        break

        except (
            PermissionError,
            OSError,
        ):

            pass

    return results


# ============================================================
# LOCATE ITEM
# ============================================================

def extract_locate_target(
    command: str,
) -> str:

    text = normalize_text(
        command
    )

    for trigger in LOCATE_TRIGGERS:

        prefix = trigger + " "

        if text.startswith(prefix):

            text = text[
                len(prefix):
            ].strip()

            break

    words = [
        word
        for word in text.split()
        if word not in FILLER_WORDS
    ]

    return " ".join(
        words
    ).strip()


def locate_item(
    target: str,
) -> bool:

    results = find_files(
        target
    )

    if not results:

        print(
            f"[jarvis] Could not find "
            f"'{target}'."
        )

        return False

    print(
        f"[jarvis] Found "
        f"{len(results)} result(s)."
    )

    for path in results[:10]:

        print(
            f"  {path}"
        )

    first = results[0]

    try:

        subprocess.Popen(
            [
                "explorer",
                "/select,",
                str(first),
            ]
        )

        print(
            f"[jarvis] Opening Explorer: "
            f"{first}"
        )

        return True

    except Exception as exc:

        print(
            f"[jarvis] Explorer error: "
            f"{exc}"
        )

        return False


# ============================================================
# LAUNCH APPLICATION
# ============================================================

def launch_application(
    target: str,
) -> bool:
    target = normalize_text(target)

    if not target:
        print("[jarvis] No application specified.")
        return False

    # --------------------------------------------------------
    # WHITELIST CHECK
    # --------------------------------------------------------
    matched_name = target

    if target not in APP_ALIASES:
        matched = best_match(target, APP_ALIASES)
        if matched:
            matched_name = matched
        else:
            print(
                f"[jarvis] Application '{target}' is not in the Jarvis whitelist."
            )
            print(
                "[jarvis] Allowed apps: Chrome, Notepad, Calculator, "
                "File Explorer, VS Code."
            )
            return False

    # Canonical tracking key: aliases refer to the same application.
    if matched_name in {"google chrome", "crome"}:
        matched_name = "chrome"
    elif matched_name in {"calculator", "calc"}:
        matched_name = "calculator"
    elif matched_name in {"vs code", "visual studio code", "vscode", "code"}:
        matched_name = "vs code"
    elif matched_name == "file explorer":
        matched_name = "explorer"

    # --------------------------------------------------------
    # Resolve executable path
    # --------------------------------------------------------
    executable = resolve_known_executable(matched_name)

    if executable is None:
        executable = APP_ALIASES.get(matched_name)

    if executable is None:
        print(f"[jarvis] Could not find application '{matched_name}'.")
        return False

    executable_path = str(executable)

    print(f"[jarvis] Executable: {executable_path}")

    # --------------------------------------------------------
    # Launch
    # --------------------------------------------------------
    try:
        before_chrome = (
            snapshot_chrome_windows()
            if matched_name == "chrome"
            else set()
        )

        before_explorer = (
            snapshot_explorer_windows()
            if matched_name in {"explorer", "file explorer"}
            else set()
        )

        before_notepad = (
            snapshot_notepad_windows()
            if matched_name == "notepad"
            else set()
        )

        launch_args = [executable_path]

        if matched_name == "chrome":
            # Force a separate Chrome window so Jarvis can identify exactly
            # which window it opened.
            launch_args.append("--new-window")

        process = subprocess.Popen(
            launch_args,
            shell=False,
        )

        # Explorer is tracked by HWND, not PID. Its process is the Windows
        # shell and must never be terminated by Jarvis.
        if matched_name in {"explorer", "file explorer"}:
            hwnd = find_new_explorer_window(before_explorer)
            if hwnd is not None:
                TRACKED_WINDOWS.setdefault("explorer", []).append(hwnd)
                save_tracking_state()
                print(f"[jarvis] Tracking 'explorer' window HWND={hwnd}")
            else:
                print(
                    "[jarvis] Could not identify the new Explorer window. "
                    "Explorer close will remain protected."
                )
        else:
            track_process(matched_name, process)

        if matched_name == "notepad":
            hwnd = find_new_notepad_window(before_notepad)
            if hwnd is not None:
                TRACKED_WINDOWS.setdefault("notepad", []).append(hwnd)
                save_tracking_state()
                print(f"[jarvis] Tracking 'notepad' window HWND={hwnd}")
            else:
                print(
                    "[jarvis] Could not identify the new Notepad window. "
                    "Notepad PID tracking remains active."
                )

        if matched_name == "chrome":
            hwnd = find_new_chrome_window(before_chrome)
            if hwnd is not None:
                TRACKED_WINDOWS.setdefault("chrome", []).append(hwnd)
                save_tracking_state()
                print(f"[jarvis] Tracking 'chrome' window HWND={hwnd}")
            else:
                print(
                    "[jarvis] Could not identify the new Chrome window. "
                    "Chrome close will remain protected."
                )

        print(
            f"[jarvis] Launched '{matched_name}' -> {executable_path}"
        )

        return True

    except FileNotFoundError:
        print(f"[jarvis] Executable not found: {executable_path}")
        return False

    except Exception as exc:
        print(f"[jarvis] Launch failed: {exc}")
        return False


# ============================================================
# MAIN ROUTER
# ============================================================

def launch_from_command(
    command: str,
) -> bool:

    command = normalize_text(
        command
    )

    # "Hey Jarvis" is only the wake word. It is not required in commands.
    # If Vosk accidentally captures it, remove it before routing.
    for wake_prefix in ("hey jarvis ", "jarvis "):
        if command.startswith(wake_prefix):
            command = command[len(wake_prefix):].strip()
            break

    if not command:

        print(
            "[jarvis] Empty command."
        )

        return False

    print(
        f"[jarvis] Routing command: "
        f"{command!r}"
    )

    # ========================================================
    # CLOSE HAS HIGHEST PRIORITY
    # ========================================================

    if is_close_command(
        command
    ):

        target = extract_close_target(
            command
        )

        print(
            "[jarvis] CLOSE command detected."
        )

        print(
            f"[jarvis] Close target: "
            f"{target!r}"
        )

        return close_application(
            target
        )

    # ========================================================
    # LOCATE / FIND
    # ========================================================

    for trigger in LOCATE_TRIGGERS:

        if (
            command == trigger
            or command.startswith(
                trigger + " "
            )
        ):

            target = extract_locate_target(
                command
            )

            print(
                "[jarvis] LOCATE command detected."
            )

            print(
                f"[jarvis] Search target: "
                f"{target!r}"
            )

            return locate_item(
                target
            )

    # ========================================================
    # OPEN
    # ========================================================

    target = extract_open_target(
        command
    )

    return launch_application(
        target
    )


# ============================================================
# MANUAL TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("JARVIS LAUNCHER TEST")
    print("=" * 60)

    print()
    print("Opening Chrome...")
    launch_from_command(
        "open chrome"
    )

    time.sleep(
        2
    )

    print()
    print("Closing Chrome...")
    launch_from_command(
        "close chrome"
    )