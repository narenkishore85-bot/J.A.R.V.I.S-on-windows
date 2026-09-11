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
    Jarvis tracks the PID returned when it launches an application.

    It does NOT use:
        taskkill /IM chrome.exe /T /F

    Therefore it will not intentionally kill every Chrome process.
"""

from __future__ import annotations

import difflib
import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import time
import winreg
from pathlib import Path


# ============================================================
# APPLICATION ALIASES
# ============================================================

APP_ALIASES = {
    "notepad": "notepad.exe",

    "calculator": "calc.exe",
    "calc": "calc.exe",

    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "crome": "chrome.exe",

    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",

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

    target = target.lower().strip()

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

    if target == "notepad":

        system_root = os.environ.get(
            "SystemRoot",
            r"C:\Windows",
        )

        path = os.path.join(
            system_root,
            "System32",
            "notepad.exe",
        )

        if os.path.isfile(path):
            return path

    return None


# ============================================================
# TRACKED PROCESSES / WINDOWS
# ============================================================

# Normal applications are tracked by PID. Chrome is special: Chrome is a
# multi-process application, so killing a Chrome PID can close an unrelated
# Chrome session. For Chrome we therefore remember the exact top-level window
# (HWND) that Jarvis opened and close that window with WM_CLOSE.
TRACKED_PROCESSES: dict[str, list[int]] = {}
TRACKED_WINDOWS: dict[str, list[int]] = {}

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
# REGISTRY APP DISCOVERY
# ============================================================

def discover_registry_apps() -> dict[str, str]:

    discovered = {}

    registry_locations = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    ]

    for hive, base_path in registry_locations:

        try:
            root = winreg.OpenKey(
                hive,
                base_path,
            )
        except OSError:
            continue

        try:

            count = winreg.QueryInfoKey(root)[0]

            for i in range(count):

                try:

                    subkey_name = winreg.EnumKey(
                        root,
                        i,
                    )

                    subkey = winreg.OpenKey(
                        root,
                        subkey_name,
                    )

                    try:
                        display_name = winreg.QueryValueEx(
                            subkey,
                            "DisplayName",
                        )[0]
                    except OSError:
                        display_name = None

                    if not display_name:

                        subkey.Close()
                        continue

                    display_name = str(
                        display_name
                    ).strip()

                    executable = None

                    try:

                        display_icon = winreg.QueryValueEx(
                            subkey,
                            "DisplayIcon",
                        )[0]

                        if display_icon:

                            display_icon = str(
                                display_icon
                            )

                            display_icon = (
                                display_icon
                                .split(",")[0]
                                .strip('"')
                            )

                            if os.path.isfile(
                                display_icon
                            ):

                                executable = (
                                    display_icon
                                )

                    except OSError:
                        pass

                    if executable is None:

                        try:

                            install_location = (
                                winreg.QueryValueEx(
                                    subkey,
                                    "InstallLocation",
                                )[0]
                            )

                            if install_location:

                                location = Path(
                                    str(
                                        install_location
                                    )
                                )

                                if location.is_dir():

                                    candidates = list(
                                        location.glob(
                                            "*.exe"
                                        )
                                    )

                                    if candidates:

                                        executable = str(
                                            candidates[0]
                                        )

                        except OSError:
                            pass

                    if executable:

                        discovered[
                            display_name.lower()
                        ] = executable

                    subkey.Close()

                except OSError:
                    continue

        finally:

            root.Close()

    return discovered


# ============================================================
# BUILD APP DATABASE
# ============================================================

def build_app_database() -> dict[str, str]:

    apps = dict(APP_ALIASES)

    try:

        registry_apps = (
            discover_registry_apps()
        )

        for name, executable in registry_apps.items():

            if name not in apps:

                apps[name] = executable

    except Exception:
        pass

    return apps


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
# CLEAN DEAD PROCESSES
# ============================================================

def clean_tracked_processes():

    for name in list(
        TRACKED_PROCESSES.keys()
    ):

        alive = []

        for pid in TRACKED_PROCESSES[name]:

            if process_exists(pid):

                alive.append(pid)

        if alive:

            TRACKED_PROCESSES[name] = alive

        else:

            del TRACKED_PROCESSES[name]


def clean_tracked_windows():
    for name in list(TRACKED_WINDOWS.keys()):
        alive = [hwnd for hwnd in TRACKED_WINDOWS[name] if window_exists(hwnd)]
        if alive:
            TRACKED_WINDOWS[name] = alive
        else:
            del TRACKED_WINDOWS[name]


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

    # Chrome is deliberately closed by HWND, not by taskkill/PID.
    if matched_name == "chrome":
        hwnds = TRACKED_WINDOWS.get("chrome", [])

        if not hwnds:
            print("[jarvis] No tracked Chrome window.")
            print("[jarvis] Refusing to close an untracked Chrome window.")
            return False

        success = False
        remaining = []

        for hwnd in hwnds:
            if not window_exists(hwnd):
                continue

            print(f"[jarvis] Closing tracked Chrome window HWND={hwnd}...")
            if close_window(hwnd):
                print(f"[jarvis] Closed Chrome window (HWND={hwnd})")
                success = True
            else:
                remaining.append(hwnd)

        if remaining:
            TRACKED_WINDOWS["chrome"] = remaining
        else:
            TRACKED_WINDOWS.pop("chrome", None)

        TRACKED_PROCESSES.pop("chrome", None)
        return success

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

    print(
        f"[jarvis] Closing tracked "
        f"'{matched_name}' PID(s): "
        f"{pids}"
    )

    success = False
    remaining = []

    for pid in pids:
        if not process_exists(pid):
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

    if remaining:
        TRACKED_PROCESSES[matched_name] = remaining
    else:
        TRACKED_PROCESSES.pop(matched_name, None)

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

    target = normalize_text(
        target
    )

    if not target:

        print(
            "[jarvis] No application specified."
        )

        return False

    matched_name = target

    # --------------------------------------------------------
    # Try known executable path first.
    # --------------------------------------------------------

    executable = resolve_known_executable(
        target
    )

    # --------------------------------------------------------
    # Alias fallback.
    # --------------------------------------------------------

    if executable is None:

        executable = APP_ALIASES.get(
            target
        )

    # --------------------------------------------------------
    # Fuzzy alias.
    # --------------------------------------------------------

    if executable is None:

        matched = best_match(
            target,
            APP_ALIASES,
        )

        if matched:

            matched_name = matched

            executable = APP_ALIASES[
                matched
            ]

    # --------------------------------------------------------
    # Registry fallback.
    # --------------------------------------------------------

    if executable is None:

        matched = best_match(
            target,
            APP_DATABASE,
        )

        if matched:

            matched_name = matched

            executable = APP_DATABASE[
                matched
            ]

    if executable is None:

        print(
            f"[jarvis] Could not find "
            f"application '{target}'."
        )

        return False

    # --------------------------------------------------------
    # Normalize full path.
    # --------------------------------------------------------

    executable_path = str(
        executable
    )

    print(
        f"[jarvis] Executable: "
        f"{executable_path}"
    )

    # --------------------------------------------------------
    # Launch.
    # --------------------------------------------------------

    try:

        before_chrome = (
            snapshot_chrome_windows()
            if matched_name == "chrome"
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

        track_process(
            matched_name,
            process,
        )

        if matched_name == "chrome":
            hwnd = find_new_chrome_window(before_chrome)
            if hwnd is not None:
                TRACKED_WINDOWS.setdefault("chrome", []).append(hwnd)
                print(
                    f"[jarvis] Tracking 'chrome' window HWND={hwnd}"
                )
            else:
                print(
                    "[jarvis] Could not identify the new Chrome window. "
                    "Chrome close will remain protected."
                )

        print(
            f"[jarvis] Launched "
            f"'{matched_name}' -> "
            f"{executable_path}"
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