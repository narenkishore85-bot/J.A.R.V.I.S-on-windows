"""
J.A.R.V.I.S. — Deterministic Windows Application Launcher

Optimized for low response latency.

Important design:
    Voice command
        ↓
    launch_from_command()
        ↓
    subprocess.Popen()
        ↓
    return True immediately
        ↓
    JARVIS can speak immediately

Slow tracking operations such as window discovery and process
identity verification are performed in background threads.

Safety:
    JARVIS only closes processes/windows that it has tracked.
    It does NOT use taskkill /IM, so it will not blindly terminate
    every instance of an application.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import difflib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import quote_plus
from . import config


# ============================================================
# LOGGING
# ============================================================

log = logging.getLogger("jarvis.launcher")


# ============================================================
# APPLICATION ALIASES
# ============================================================

APP_ALIASES = {
    # Chrome
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "crome": "chrome.exe",

    # Windows
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",

    # VS Code
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "vscode": "Code.exe",
    "code": "Code.exe",

    # Arduino
    "arduino": "arduino-ide.exe",
    "arduino ide": "arduino-ide.exe",

    # KiCad
    "kicad": "kicad.exe",
    "ki cad": "kicad.exe",

    # Web apps
    "instagram": "__instagram__",
    "instagram app": "__instagram__",
    "claude": "__claude__",

    # Engineering / other applications
    "proteus": (
        r"C:\Program Files\Labcenter Electronics"
        r"\Proteus 8 Professional\BIN\PDS.EXE"
    ),

    "keil": (
        r"C:\Users\naren\AppData\Local"
        r"\Keil_v5\UV4\UV4.exe"
    ),

    "keil uvision": (
        r"C:\Users\naren\AppData\Local"
        r"\Keil_v5\UV4\UV4.exe"
    ),

    "blender": (
        r"C:\Program Files\Blender Foundation"
        r"\Blender 5.0\blender-launcher.exe"
    ),

    "vlc": (
        r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    ),

    "vlc media player": (
        r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    ),

    "davinci": (
        r"C:\Program Files\Blackmagic Design"
        r"\DaVinci Resolve\Resolve.exe"
    ),

    "davinci resolve": (
        r"C:\Program Files\Blackmagic Design"
        r"\DaVinci Resolve\Resolve.exe"
    ),

    "spotify": (
        r"C:\Users\naren\AppData\Roaming"
        r"\Spotify\Spotify.exe"
    ),

    # Microsoft Office
    "word": "winword.exe",
    "microsoft word": "winword.exe",

    "excel": "excel.exe",
    "microsoft excel": "excel.exe",

    "powerpoint": "powerpnt.exe",
    "power point": "powerpnt.exe",
    "microsoft powerpoint": "powerpnt.exe",
}


SITE_URLS = {
    "instagram": "https://www.instagram.com/",
    "claude": "https://claude.ai/new",
}


# ============================================================
# TRACKING STATE
# ============================================================

TRACKED_PROCESSES: dict[str, list[int]] = {}
TRACKED_WINDOWS: dict[str, list[int]] = {}

# Process identity is kept separately so that PID reuse is
# protected when JARVIS later executes a close command.
#
# Example:
# {
#     "chrome": {
#         "12345": {
#             "path": "...chrome.exe",
#             "start_time": "..."
#         }
#     }
# }
TRACKED_PROCESS_META: dict[
    str,
    dict[str, dict[str, str]]
] = {}


TRACKING_FILE = (
    Path(__file__).resolve().with_name(
        "jarvis_tracking.json"
    )
)


_tracking_lock = threading.RLock()


# ============================================================
# WIN32
# ============================================================

user32 = ctypes.windll.user32

WM_CLOSE = 0x0010
GW_OWNER = 4


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text: str) -> str:
    """
    Normalize speech-recognition output for deterministic
    command matching.
    """

    text = str(text).lower().strip()

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
# APPLICATION DISCOVERY
# ============================================================

def find_chrome() -> str | None:
    """
    Locate Google Chrome quickly without scanning the disk.
    """

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

    return shutil.which("chrome.exe")


def find_vscode() -> str | None:
    """
    Locate Visual Studio Code.
    """

    candidates = [
        os.path.expandvars(
            r"%LocalAppData%\Programs"
            r"\Microsoft VS Code\Code.exe"
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

    return shutil.which("code.exe")


def find_arduino() -> str | None:
    """
    Locate Arduino IDE.
    """

    candidates = [
        os.path.expandvars(
            r"%ProgramFiles%\Arduino IDE\Arduino IDE.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Arduino IDE\arduino-ide.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Programs"
            r"\Arduino IDE\Arduino IDE.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Programs"
            r"\Arduino IDE\arduino-ide.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Arduino\arduino.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Arduino\arduino.exe"
        ),
    ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    for name in (
        "arduino-ide.exe",
        "Arduino IDE.exe",
        "arduino.exe",
    ):
        found = shutil.which(name)

        if found:
            return found

    return None


def find_kicad() -> str | None:
    """
    Locate KiCad without recursively scanning the whole drive.
    """

    program_files = Path(
        os.environ.get(
            "ProgramFiles",
            r"C:\Program Files",
        )
    )

    program_files_x86 = Path(
        os.environ.get(
            "ProgramFiles(x86)",
            r"C:\Program Files (x86)",
        )
    )

    roots = [
        program_files / "KiCad",
        program_files_x86 / "KiCad",
    ]

    candidates: list[Path] = []

    for root in roots:
        if not root.exists():
            continue

        candidates.extend([
            root / "bin" / "kicad.exe",
            root / "10.0" / "bin" / "kicad.exe",
            root / "9.0" / "bin" / "kicad.exe",
            root / "8.0" / "bin" / "kicad.exe",
            root / "7.0" / "bin" / "kicad.exe",
        ])

        try:
            candidates.extend(
                root.glob(
                    "*/bin/kicad.exe"
                )
            )
        except OSError:
            pass

    for path in candidates:
        if path.is_file():
            return str(path)

    return shutil.which("kicad.exe")


def find_claude_desktop() -> str | None:
    """
    Locate Claude Desktop if installed.
    """

    candidates = [
        os.path.expandvars(
            r"%LocalAppData%\Programs\Claude\Claude.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Programs\claude\Claude.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\AnthropicClaude\Claude.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Claude\Claude.exe"
        ),
    ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    return shutil.which("Claude.exe")


def resolve_known_executable(
    target: str,
) -> str | None:
    """
    Resolve an application name to an executable.

    No expensive disk scan is performed here.
    """

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
        path = os.path.join(
            system_root,
            "System32",
            "notepad.exe",
        )

        return (
            path
            if os.path.isfile(path)
            else "notepad.exe"
        )

    if target in {
        "calculator",
        "calc",
    }:
        path = os.path.join(
            system_root,
            "System32",
            "calc.exe",
        )

        return (
            path
            if os.path.isfile(path)
            else "calc.exe"
        )

    if target in {
        "explorer",
        "file explorer",
    }:
        path = os.path.join(
            system_root,
            "explorer.exe",
        )

        return (
            path
            if os.path.isfile(path)
            else "explorer.exe"
        )

    if target in {
        "arduino",
        "arduino ide",
    }:
        return find_arduino()

    if target in {
        "kicad",
        "ki cad",
    }:
        return find_kicad()

    if target == "claude":
        return find_claude_desktop()

    return None


# ============================================================
# WINDOW ENUMERATION
# ============================================================

def enumerate_visible_windows(
    title_keywords: tuple[str, ...] = (),
    class_names: tuple[str, ...] = (),
) -> set[int]:
    """
    Fast native Win32 window enumeration.
    """

    hwnds: set[int] = set()

    title_keywords = tuple(
        keyword.lower()
        for keyword in title_keywords
    )

    class_names = tuple(
        class_name.lower()
        for class_name in class_names
    )

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    def callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True

            if user32.GetWindow(hwnd, GW_OWNER):
                return True

            title_buffer = ctypes.create_unicode_buffer(
                512
            )

            class_buffer = ctypes.create_unicode_buffer(
                256
            )

            user32.GetWindowTextW(
                hwnd,
                title_buffer,
                len(title_buffer),
            )

            user32.GetClassNameW(
                hwnd,
                class_buffer,
                len(class_buffer),
            )

            title = (
                title_buffer.value
                .strip()
                .lower()
            )

            class_name = (
                class_buffer.value
                .strip()
                .lower()
            )

            title_match = (
                bool(title_keywords)
                and any(
                    keyword in title
                    for keyword in title_keywords
                )
            )

            class_match = (
                bool(class_names)
                and class_name in class_names
            )

            if title_match or class_match:
                hwnds.add(int(hwnd))

        except Exception:
            pass

        return True

    user32.EnumWindows(
        EnumWindowsProc(callback),
        0,
    )

    return hwnds


def snapshot_chrome_windows() -> set[int]:
    return enumerate_visible_windows(
        class_names=(
            "Chrome_WidgetWin_1",
        )
    )


def snapshot_explorer_windows() -> set[int]:
    return enumerate_visible_windows(
        class_names=(
            "CabinetWClass",
        )
    )


def snapshot_notepad_windows() -> set[int]:
    return enumerate_visible_windows(
        class_names=(
            "Notepad",
        )
    )


def snapshot_vscode_windows() -> set[int]:
    return enumerate_visible_windows(
        title_keywords=(
            "visual studio code",
        )
    )


def snapshot_calculator_windows() -> set[int]:
    return enumerate_visible_windows(
        title_keywords=(
            "calculator",
        )
    )


def snapshot_site_windows(
    key: str,
) -> set[int]:
    keywords = {
        "instagram": (
            "instagram",
        ),
        "claude": (
            "claude",
            "anthropic",
        ),
    }

    return enumerate_visible_windows(
        title_keywords=keywords.get(
            key,
            (key,),
        )
    )


def window_exists(hwnd: int) -> bool:
    try:
        return bool(
            user32.IsWindow(hwnd)
        )
    except Exception:
        return False


def close_window(hwnd: int) -> bool:
    """
    Gracefully close one exact tracked window.
    """

    if not window_exists(hwnd):
        return True

    try:
        user32.PostMessageW(
            hwnd,
            WM_CLOSE,
            0,
            0,
        )

        deadline = (
            time.monotonic()
            + 1.0
        )

        while time.monotonic() < deadline:
            if not window_exists(hwnd):
                return True

            time.sleep(0.05)

    except Exception as exc:
        print(
            f"[jarvis] Window close failed: {exc}"
        )

    return not window_exists(hwnd)


# ============================================================
# TRACKING FILE
# ============================================================

def save_tracking_state():
    """
    Atomically save tracking information.
    """

    with _tracking_lock:
        data = {
            "processes": {
                name: [
                    int(pid)
                    for pid in pids
                ]
                for name, pids
                in TRACKED_PROCESSES.items()
                if pids
            },

            "windows": {
                name: [
                    int(hwnd)
                    for hwnd in hwnds
                ]
                for name, hwnds
                in TRACKED_WINDOWS.items()
                if hwnds
            },

            "process_meta": (
                TRACKED_PROCESS_META
            ),
        }

        temp_file = (
            TRACKING_FILE.with_suffix(
                ".tmp"
            )
        )

        try:
            temp_file.write_text(
                json.dumps(
                    data,
                    indent=2,
                ),
                encoding="utf-8",
            )

            os.replace(
                temp_file,
                TRACKING_FILE,
            )

        except Exception as exc:
            print(
                "[jarvis] Warning: could not "
                f"save tracking state: {exc}"
            )

            try:
                if temp_file.exists():
                    temp_file.unlink()
            except OSError:
                pass


def load_tracking_state():
    global TRACKED_PROCESSES
    global TRACKED_WINDOWS
    global TRACKED_PROCESS_META

    if not TRACKING_FILE.exists():
        return

    try:
        data = json.loads(
            TRACKING_FILE.read_text(
                encoding="utf-8"
            )
        )

        processes = data.get(
            "processes",
            {},
        )

        windows = data.get(
            "windows",
            {},
        )

        process_meta = data.get(
            "process_meta",
            {},
        )

        if isinstance(
            processes,
            dict,
        ):
            TRACKED_PROCESSES = {
                str(name): [
                    int(pid)
                    for pid in pids
                    if str(pid).isdigit()
                ]
                for name, pids
                in processes.items()
                if isinstance(
                    pids,
                    list,
                )
            }

        if isinstance(
            windows,
            dict,
        ):
            TRACKED_WINDOWS = {
                str(name): [
                    int(hwnd)
                    for hwnd in hwnds
                    if str(hwnd).isdigit()
                ]
                for name, hwnds
                in windows.items()
                if isinstance(
                    hwnds,
                    list,
                )
            }

        if isinstance(
            process_meta,
            dict,
        ):
            TRACKED_PROCESS_META = {
                str(name): {
                    str(pid): {
                        "path": str(
                            info.get(
                                "path",
                                "",
                            )
                        ),

                        "start_time": str(
                            info.get(
                                "start_time",
                                "",
                            )
                        ),
                    }
                    for pid, info
                    in entries.items()
                    if isinstance(
                        info,
                        dict,
                    )
                }
                for name, entries
                in process_meta.items()
                if isinstance(
                    entries,
                    dict,
                )
            }

    except Exception as exc:
        print(
            "[jarvis] Warning: could not "
            f"load tracking state: {exc}"
        )

        TRACKED_PROCESSES = {}
        TRACKED_WINDOWS = {}
        TRACKED_PROCESS_META = {}


def clear_tracking_file_if_empty():
    with _tracking_lock:
        if (
            TRACKED_PROCESSES
            or TRACKED_WINDOWS
        ):
            save_tracking_state()
            return

        try:
            if TRACKING_FILE.exists():
                TRACKING_FILE.unlink()
        except OSError as exc:
            print(
                "[jarvis] Warning: could not "
                f"remove tracking file: {exc}"
            )


# ============================================================
# PROCESS IDENTITY
# ============================================================

def get_process_identity(
    pid: int,
) -> tuple[str, str] | None:
    """
    Get executable path and creation time.

    This is intentionally used outside the launch critical path.
    """

    try:
        ps_command = (
            "$p=Get-CimInstance Win32_Process "
            f"-Filter \"ProcessId = {int(pid)}\" "
            "-ErrorAction Stop; "
            "if ($null -eq $p) { exit 2 }; "
            "[PSCustomObject]@{"
            "Path=$p.ExecutablePath;"
            "StartTime=[string]$p.CreationDate"
            "} | ConvertTo-Json -Compress"
        )

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                ps_command,
            ],
            capture_output=True,
            text=True,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
            ),
            timeout=3.0,
        )

        if (
            result.returncode != 0
            or not result.stdout.strip()
        ):
            return None

        data = json.loads(
            result.stdout
        )

        return (
            str(
                data.get(
                    "Path",
                    "",
                )
            ),

            str(
                data.get(
                    "StartTime",
                    "",
                )
            ),
        )

    except Exception:
        return None


def process_exists(
    pid: int,
) -> bool:
    """
    Native process existence check.

    Much faster than launching PowerShell.
    """

    PROCESS_QUERY_LIMITED_INFORMATION = (
        0x1000
    )

    try:
        handle = (
            ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                int(pid),
            )
        )

        if handle:
            ctypes.windll.kernel32.CloseHandle(
                handle
            )
            return True

        return False

    except Exception:
        return False


def tracked_pid_is_same_process(
    app_name: str,
    pid: int,
) -> bool:
    """
    Protect against PID reuse.

    If identity metadata has not yet been collected,
    allow the process temporarily because it was just launched
    by JARVIS.

    Once metadata exists, verify it.
    """

    meta = (
        TRACKED_PROCESS_META
        .get(
            app_name,
            {},
        )
        .get(
            str(pid)
        )
    )

    if not meta:
        return True

    current = get_process_identity(
        pid
    )

    if current is None:
        return True

    stored_path = str(
        meta.get(
            "path",
            "",
        )
    ).lower()

    stored_start = str(
        meta.get(
            "start_time",
            "",
        )
    )

    current_path, current_start = (
        current
    )

    if (
        stored_path
        and current_path
        and (
            current_path.lower()
            != stored_path
        )
    ):
        return False

    if (
        stored_start
        and current_start
        and current_start
        != stored_start
    ):
        return False

    return True


# ============================================================
# TRACKING CLEANUP
# ============================================================

def clean_tracked_processes():
    """
    Remove dead PIDs.

    This can be slower because identity checking may invoke
    PowerShell, so it should never run during normal launch.
    """

    changed = False

    with _tracking_lock:
        for name in list(
            TRACKED_PROCESSES.keys()
        ):
            alive = []

            for pid in TRACKED_PROCESSES[name]:

                if (
                    process_exists(pid)
                    and tracked_pid_is_same_process(
                        name,
                        pid,
                    )
                ):
                    alive.append(pid)

                else:
                    (
                        TRACKED_PROCESS_META
                        .get(
                            name,
                            {},
                        )
                        .pop(
                            str(pid),
                            None,
                        )
                    )

                    changed = True

            if alive:
                TRACKED_PROCESSES[name] = alive
            else:
                del TRACKED_PROCESSES[name]

                TRACKED_PROCESS_META.pop(
                    name,
                    None,
                )

                changed = True

    if changed:
        save_tracking_state()


def clean_tracked_windows():
    changed = False

    with _tracking_lock:
        for name in list(
            TRACKED_WINDOWS.keys()
        ):
            alive = [
                hwnd
                for hwnd
                in TRACKED_WINDOWS[name]
                if window_exists(hwnd)
            ]

            if alive:
                if (
                    len(alive)
                    != len(
                        TRACKED_WINDOWS[name]
                    )
                ):
                    changed = True

                TRACKED_WINDOWS[name] = alive

            else:
                del TRACKED_WINDOWS[name]
                changed = True

    if changed:
        save_tracking_state()


def initialize_tracking():
    """
    Startup cleanup.

    This happens once when the launcher module is imported.
    """

    load_tracking_state()

    # Deliberately do NOT perform expensive process identity
    # checks here.
    #
    # They are done when a close operation needs them.
    clean_tracked_windows()


initialize_tracking()


# ============================================================
# PROCESS TRACKING
# ============================================================

def track_process(
    app_name: str,
    process: subprocess.Popen,
    save: bool = True,
):
    """
    Register a process immediately.

    This function intentionally does NOT query PowerShell.
    """

    app_name = normalize_text(
        app_name
    )

    pid = int(
        process.pid
    )

    with _tracking_lock:
        TRACKED_PROCESSES.setdefault(
            app_name,
            [],
        )

        if (
            pid
            not in TRACKED_PROCESSES[
                app_name
            ]
        ):
            TRACKED_PROCESSES[
                app_name
            ].append(pid)

    if save:
        save_tracking_state()

    print(
        f"[jarvis] Tracking '{app_name}' "
        f"PID={pid}"
    )


def _record_process_identity_background(
    app_name: str,
    pid: int,
):
    """
    Slow identity collection performed in a daemon thread.
    """

    identity = get_process_identity(
        pid
    )

    if identity is None:
        return

    path, start_time = identity

    with _tracking_lock:
        if (
            pid
            not in TRACKED_PROCESSES.get(
                app_name,
                [],
            )
        ):
            return

        TRACKED_PROCESS_META.setdefault(
            app_name,
            {},
        )[str(pid)] = {
            "path": path,
            "start_time": start_time,
        }

    save_tracking_state()


def track_process_metadata_async(
    app_name: str,
    pid: int,
):
    thread = threading.Thread(
        target=(
            _record_process_identity_background
        ),
        args=(
            app_name,
            pid,
        ),
        daemon=True,
        name="jarvis-process-meta",
    )

    thread.start()


# ============================================================
# BACKGROUND WINDOW TRACKING
# ============================================================

def _capture_new_window(
    app_name: str,
    before: set[int],
    snapshot_func,
    max_wait: float = 3.0,
):
    """
    Search for the new application window without blocking
    JARVIS's voice-response path.
    """

    deadline = (
        time.monotonic()
        + max_wait
    )

    while (
        time.monotonic()
        < deadline
    ):
        try:
            current = snapshot_func()

            new_windows = (
                current - before
            )

            if new_windows:
                hwnd = next(
                    iter(new_windows)
                )

                with _tracking_lock:
                    TRACKED_WINDOWS.setdefault(
                        app_name,
                        [],
                    )

                    if (
                        hwnd
                        not in TRACKED_WINDOWS[
                            app_name
                        ]
                    ):
                        TRACKED_WINDOWS[
                            app_name
                        ].append(hwnd)

                save_tracking_state()

                print(
                    f"[jarvis] Tracking "
                    f"'{app_name}' "
                    f"window HWND={hwnd}"
                )

                return hwnd

        except Exception:
            pass

        time.sleep(0.10)

    return None


def track_window_async(
    app_name: str,
    before: set[int],
    snapshot_func,
):
    thread = threading.Thread(
        target=_capture_new_window,
        args=(
            app_name,
            before,
            snapshot_func,
        ),
        daemon=True,
        name=(
            f"jarvis-window-{app_name}"
        ),
    )

    thread.start()


# ============================================================
# WEBSITE LAUNCH
# ============================================================

def launch_web_site(
    key: str,
    url: str,
) -> bool:
    chrome = find_chrome()

    if not chrome:
        print(
            "[jarvis] Chrome was not found."
        )
        return False

    before_chrome = (
        snapshot_chrome_windows()
    )

    try:
        process = subprocess.Popen(
            [
                chrome,
                "--new-window",
                "--app=" + url,
            ],
            shell=False,
        )

        track_process(
            key,
            process,
            save=True,
        )

        track_process_metadata_async(
            key,
            int(process.pid),
        )

        track_window_async(
            key,
            before_chrome,
            snapshot_chrome_windows,
        )

        print(
            f"[jarvis] Opened {key}: {url}"
        )

        # IMPORTANT:
        # Return immediately.
        return True

    except Exception as exc:
        print(
            f"[jarvis] Could not open "
            f"{key}: {exc}"
        )
        return False


# ============================================================
# GOOGLE SEARCH
# ============================================================

def google_search(
    query: str,
) -> bool:
    query = normalize_text(
        query
    )

    if not query:
        print(
            "[jarvis] No Google search query."
        )
        return False

    chrome = find_chrome()

    if not chrome:
        print(
            "[jarvis] Chrome was not found."
        )
        return False

    url = (
        "https://www.google.com/search?q="
        + quote_plus(query)
    )

    try:
        subprocess.Popen(
            [
                chrome,
                "--new-window",
                url,
            ],
            shell=False,
        )

        print(
            f"[jarvis] Google search: "
            f"{query}"
        )

        return True

    except Exception as exc:
        print(
            f"[jarvis] Google search "
            f"failed: {exc}"
        )
        return False


def extract_google_query(
    command: str,
) -> str | None:
    text = normalize_text(
        command
    )

    patterns = (
        r"^search google for (.+)$",
        r"^search google (.+)$",
        r"^google search for (.+)$",
        r"^google search (.+)$",
        r"^search the web for (.+)$",
        r"^search web for (.+)$",
        r"^search (.+) on google$",
        r"^google (.+)$",
    )

    for pattern in patterns:
        match = re.match(
            pattern,
            text,
        )

        if match:
            return (
                match.group(1)
                .strip()
            )

    return None


# ============================================================
# CLAUDE COMMAND
# ============================================================

def extract_claude_prompt(
    command: str,
) -> str | None:
    text = normalize_text(
        command
    )

    patterns = (
        r"^(?:ask|tell|command|give)"
        r" claude(?: to)? (.+)$",

        r"^claude (.+)$",
    )

    for pattern in patterns:
        match = re.match(
            pattern,
            text,
        )

        if match:
            return (
                match.group(1)
                .strip()
            )

    return None


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

    matches = difflib.get_close_matches(
        target,
        list(database.keys()),
        n=1,
        cutoff=0.60,
    )

    return (
        matches[0]
        if matches
        else None
    )


# ============================================================
# COMMAND PARSING
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


# ============================================================
# PROCESS TERMINATION
# ============================================================

def terminate_pid(
    pid: int,
) -> bool:
    """
    Gracefully terminate one exact PID.

    If graceful termination does not work, force termination
    is used for that exact PID only.
    """

    try:
        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(pid),
            ],
            capture_output=True,
            text=True,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
            ),
            timeout=2.0,
        )

        deadline = (
            time.monotonic()
            + 0.5
        )

        while (
            time.monotonic()
            < deadline
        ):
            if not process_exists(pid):
                return True

            time.sleep(0.05)

        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(pid),
                "/F",
            ],
            capture_output=True,
            text=True,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
            ),
            timeout=2.0,
        )

        deadline = (
            time.monotonic()
            + 0.5
        )

        while (
            time.monotonic()
            < deadline
        ):
            if not process_exists(pid):
                return True

            time.sleep(0.05)

        return not process_exists(pid)

    except Exception as exc:
        print(
            f"[jarvis] PID termination "
            f"failed: {exc}"
        )

        return False


# ============================================================
# CANONICAL APPLICATION NAMES
# ============================================================

def canonical_app_name(
    name: str,
) -> str:
    name = normalize_text(
        name
    )

    aliases = {
        "google chrome": "chrome",
        "crome": "chrome",

        "calculator": "calculator",
        "calc": "calculator",

        "vs code": "vs code",
        "visual studio code": "vs code",
        "vscode": "vs code",
        "code": "vs code",

        "file explorer": "explorer",

        "arduino ide": "arduino",

        "ki cad": "kicad",
    }

    return aliases.get(
        name,
        name,
    )


# ============================================================
# CLOSE APPLICATION
# ============================================================

def close_application(
    target: str,
) -> bool:
    target = normalize_text(
        target
    )

    if not target:
        print(
            "[jarvis] No close target."
        )
        return False

    # Clean dead window records.
    # This is fast native Win32 work.
    clean_tracked_windows()

    matched_name = target

    if target not in APP_ALIASES:
        matched = best_match(
            target,
            APP_ALIASES,
        )

        if matched:
            matched_name = matched

    matched_name = canonical_app_name(
        matched_name
    )

    # --------------------------------------------------------
    # WEB APPS
    # --------------------------------------------------------

    if matched_name in {
        "instagram",
        "claude",
    }:
        hwnds = TRACKED_WINDOWS.get(
            matched_name,
            [],
        )

        if not hwnds:
            print(
                f"[jarvis] No tracked "
                f"{matched_name} window."
            )

            print(
                "[jarvis] Refusing to close "
                "an untracked window."
            )

            return False

        success = False
        remaining = []

        for hwnd in hwnds:
            if not window_exists(hwnd):
                continue

            print(
                f"[jarvis] Closing tracked "
                f"{matched_name} "
                f"HWND={hwnd}..."
            )

            if close_window(hwnd):
                success = True
            else:
                remaining.append(hwnd)

        with _tracking_lock:
            if remaining:
                TRACKED_WINDOWS[
                    matched_name
                ] = remaining
            else:
                TRACKED_WINDOWS.pop(
                    matched_name,
                    None,
                )

            TRACKED_PROCESSES.pop(
                matched_name,
                None,
            )

            TRACKED_PROCESS_META.pop(
                matched_name,
                None,
            )

        clear_tracking_file_if_empty()

        return success

    # --------------------------------------------------------
    # WINDOW-TRACKED APPLICATIONS
    # --------------------------------------------------------

    if matched_name in {
        "chrome",
        "notepad",
        "calculator",
        "vs code",
        "explorer",
    }:

        hwnds = TRACKED_WINDOWS.get(
            matched_name,
            [],
        )

        # Explorer is especially protected.
        if matched_name == "explorer":
            if not hwnds:
                print(
                    "[jarvis] No tracked "
                    "Explorer window."
                )

                print(
                    "[jarvis] Refusing to "
                    "close untracked Explorer."
                )

                return False

        if hwnds:
            success = False
            remaining = []

            for hwnd in hwnds:
                if not window_exists(hwnd):
                    continue

                print(
                    f"[jarvis] Closing tracked "
                    f"{matched_name} "
                    f"HWND={hwnd}..."
                )

                if close_window(hwnd):
                    success = True
                else:
                    remaining.append(hwnd)

            with _tracking_lock:
                if remaining:
                    TRACKED_WINDOWS[
                        matched_name
                    ] = remaining
                else:
                    TRACKED_WINDOWS.pop(
                        matched_name,
                        None,
                    )

                TRACKED_PROCESSES.pop(
                    matched_name,
                    None,
                )

                TRACKED_PROCESS_META.pop(
                    matched_name,
                    None,
                )

            clear_tracking_file_if_empty()

            return success

        # Chrome / Notepad / Explorer do not fall back to
        # arbitrary process killing.
        if matched_name not in {
            "calculator",
            "vs code",
        }:
            print(
                f"[jarvis] No tracked "
                f"{matched_name} window."
            )

            print(
                "[jarvis] Refusing to close "
                "an untracked application."
            )

            return False

        print(
            f"[jarvis] No tracked "
            f"{matched_name} window; "
            "using tracked PID fallback."
        )

    # --------------------------------------------------------
    # PID-TRACKED APPLICATIONS
    # --------------------------------------------------------

    pids = TRACKED_PROCESSES.get(
        matched_name,
        [],
    )

    if not pids:
        print(
            f"[jarvis] No tracked PID "
            f"for '{matched_name}'."
        )

        print(
            "[jarvis] Refusing to kill "
            "untracked processes."
        )

        return False

    success = False
    remaining = []

    for pid in pids:

        if not process_exists(pid):
            continue

        # Identity verification is intentionally done only
        # during close, never during launch.
        if not tracked_pid_is_same_process(
            matched_name,
            pid,
        ):
            print(
                f"[jarvis] PID={pid} no longer "
                "matches the process JARVIS opened."
            )

            print(
                "[jarvis] Refusing to terminate it."
            )

            continue

        if terminate_pid(pid):
            print(
                f"[jarvis] Closed "
                f"'{matched_name}' "
                f"(PID={pid})"
            )

            success = True

        else:
            remaining.append(pid)

    with _tracking_lock:
        if remaining:
            TRACKED_PROCESSES[
                matched_name
            ] = remaining
        else:
            TRACKED_PROCESSES.pop(
                matched_name,
                None,
            )

            TRACKED_PROCESS_META.pop(
                matched_name,
                None,
            )

        meta = TRACKED_PROCESS_META.get(
            matched_name,
            {},
        )

        for pid in list(
            meta.keys()
        ):
            if int(pid) not in remaining:
                meta.pop(
                    pid,
                    None,
                )

    clear_tracking_file_if_empty()

    return success


# ============================================================
# FILE SEARCH
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


def _search_directory(
    location: Path,
    target: str,
    results: list[Path],
    max_results: int = 20,
):
    try:
        for path in location.rglob("*"):

            try:
                if not path.is_file():
                    continue

                filename = normalize_text(
                    path.name
                )

                if target in filename:
                    results.append(path)

                    if (
                        len(results)
                        >= max_results
                    ):
                        return

            except (
                PermissionError,
                OSError,
            ):
                continue

    except (
        PermissionError,
        OSError,
    ):
        pass


def find_files(
    target: str,
):
    target = normalize_text(
        target
    )

    if not target:
        return []

    results: list[Path] = []

    # --------------------------------------------------------
    # First: common user folders
    # --------------------------------------------------------

    for location in search_locations():

        _search_directory(
            location,
            target,
            results,
            max_results=20,
        )

        if len(results) >= 20:
            return results

    # --------------------------------------------------------
    # Second: files directly inside user home
    # --------------------------------------------------------

    home = Path.home()

    try:
        for path in home.iterdir():

            try:
                if (
                    path.is_file()
                    and target
                    in normalize_text(
                        path.name
                    )
                ):
                    results.append(path)

                    if (
                        len(results)
                        >= 20
                    ):
                        return results

            except (
                PermissionError,
                OSError,
            ):
                continue

    except (
        PermissionError,
        OSError,
    ):
        pass

    # --------------------------------------------------------
    # Last resort: recursive home search
    # --------------------------------------------------------

    if not results:
        _search_directory(
            home,
            target,
            results,
            max_results=20,
        )

    return results


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
            ],
            shell=False,
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
    """
    Launch an application immediately.

    IMPORTANT:
        This function intentionally does not wait for the
        application window to appear.

    That is the main latency optimization.
    """

    target = normalize_text(
        target
    )

    if not target:
        print(
            "[jarvis] No application specified."
        )

        return False

    # --------------------------------------------------------
    # WEB APPS
    # --------------------------------------------------------

    if target in {
        "instagram",
        "claude",
    }:
        return launch_web_site(
            target,
            SITE_URLS[target],
        )

    # --------------------------------------------------------
    # WHITELIST
    # --------------------------------------------------------

    matched_name = target

    if target not in APP_ALIASES:
        matched = best_match(
            target,
            APP_ALIASES,
        )

        if matched:
            matched_name = matched

        else:
            print(
                f"[jarvis] Application "
                f"'{target}' is not in "
                "the Jarvis whitelist."
            )

            return False

    matched_name = canonical_app_name(
        matched_name
    )

    # --------------------------------------------------------
    # RESOLVE EXECUTABLE
    # --------------------------------------------------------

    executable = resolve_known_executable(
        matched_name
    )

    if executable is None:
        executable = APP_ALIASES.get(
            matched_name
        )

    if executable is None:
        print(
            f"[jarvis] Could not find "
            f"application '{matched_name}'."
        )

        return False

    executable_path = str(
        executable
    )

    print(
        f"[jarvis] Executable: "
        f"{executable_path}"
    )

    # --------------------------------------------------------
    # WINDOW SNAPSHOTS
    #
    # These happen BEFORE Popen so a newly created window can
    # be identified later.
    #
    # They are native Win32 calls and are much faster than
    # PowerShell process inspection.
    # --------------------------------------------------------

    before_chrome = (
        snapshot_chrome_windows()
        if matched_name == "chrome"
        else set()
    )

    before_explorer = (
        snapshot_explorer_windows()
        if matched_name == "explorer"
        else set()
    )

    before_notepad = (
        snapshot_notepad_windows()
        if matched_name == "notepad"
        else set()
    )

    before_calculator = (
        snapshot_calculator_windows()
        if matched_name == "calculator"
        else set()
    )

    before_vscode = (
        snapshot_vscode_windows()
        if matched_name == "vs code"
        else set()
    )

    # --------------------------------------------------------
    # START APPLICATION
    # --------------------------------------------------------

    try:
        launch_args = [
            executable_path
        ]

        if matched_name == "chrome":
            launch_args.append(
                "--new-window"
            )

        process = subprocess.Popen(
            launch_args,
            shell=False,
        )

        pid = int(
            process.pid
        )

        # ----------------------------------------------------
        # CRITICAL LATENCY OPTIMIZATION
        #
        # Register PID immediately.
        # No PowerShell.
        # No waiting for process startup.
        # No window polling.
        # ----------------------------------------------------

        if matched_name != "explorer":

            track_process(
                matched_name,
                process,
                save=True,
            )

            # Slow identity lookup happens in background.
            track_process_metadata_async(
                matched_name,
                pid,
            )

        # ----------------------------------------------------
        # WINDOW TRACKING
        #
        # All waiting happens in background.
        # ----------------------------------------------------

        if matched_name == "explorer":

            track_window_async(
                "explorer",
                before_explorer,
                snapshot_explorer_windows,
            )

        elif matched_name == "notepad":

            track_window_async(
                "notepad",
                before_notepad,
                snapshot_notepad_windows,
            )

        elif matched_name == "chrome":

            track_window_async(
                "chrome",
                before_chrome,
                snapshot_chrome_windows,
            )

        elif matched_name == "calculator":

            track_window_async(
                "calculator",
                before_calculator,
                snapshot_calculator_windows,
            )

        elif matched_name == "vs code":

            track_window_async(
                "vs code",
                before_vscode,
                snapshot_vscode_windows,
            )

        # ----------------------------------------------------
        # RETURN IMMEDIATELY
        # ----------------------------------------------------

        print(
            f"[jarvis] Launched "
            f"'{matched_name}' "
            f"-> {executable_path}"
        )

        return True

    except FileNotFoundError:
        print(
            f"[jarvis] Executable not found: "
            f"{executable_path}"
        )

        return False

    except Exception as exc:
        print(
            f"[jarvis] Launch failed: "
            f"{exc}"
        )

        return False


# ============================================================
# MAIN COMMAND ROUTER
# ============================================================

def launch_from_command(
    command: str,
) -> bool:
    """
    Main function used by jarvis_main.py.

    Expected examples:

        open chrome
        launch chrome
        open notepad
        open calculator
        open vs code
        close chrome
        close calculator
        find project
        locate report
        search google for esp32
        hey jarvis open chrome
    """

    command = normalize_text(
        command
    )

    # --------------------------------------------------------
    # Optional wake-word prefix
    # --------------------------------------------------------

    for wake_prefix in (
        "hey jarvis ",
        "jarvis ",
    ):
        if command.startswith(
            wake_prefix
        ):
            command = command[
                len(wake_prefix):
            ].strip()

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

    # --------------------------------------------------------
    # GOOGLE
    # --------------------------------------------------------

    google_query = (
        extract_google_query(
            command
        )
    )

    if google_query:
        return google_search(
            google_query
        )

    # --------------------------------------------------------
    # CLAUDE
    # --------------------------------------------------------

    claude_prompt = (
        extract_claude_prompt(
            command
        )
    )

    if claude_prompt:

        url = (
            SITE_URLS["claude"]
            + "?q="
            + quote_plus(
                claude_prompt
            )
        )

        return launch_web_site(
            "claude",
            url,
        )

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    if is_close_command(
        command
    ):
        target = (
            extract_close_target(
                command
            )
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

    # --------------------------------------------------------
    # LOCATE / FIND
    # --------------------------------------------------------

    for trigger in LOCATE_TRIGGERS:

        if (
            command == trigger
            or command.startswith(
                trigger + " "
            )
        ):
            target = (
                extract_locate_target(
                    command
                )
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

    # --------------------------------------------------------
    # OPEN / LAUNCH
    # --------------------------------------------------------

    target = extract_open_target(
        command
    )

    return launch_application(
        target
    )


# ============================================================
# OPTIONAL MANUAL TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("JARVIS LAUNCHER TEST")
    print("=" * 60)
    print()

    print(
        "Opening Chrome..."
    )

    result = launch_from_command(
        "open chrome"
    )

    print()
    print(
        f"Launch result: {result}"
    )

    print()
    print(
        "The launcher returned immediately."
    )

    print(
        "Window/process tracking continues "
        "in the background."
    )

    print()
    print(
        "Waiting 3 seconds only for this "
        "manual test..."
    )

    time.sleep(3)

    print()
    print(
        "Closing Chrome..."
    )

    launch_from_command(
        "close chrome"
    )
