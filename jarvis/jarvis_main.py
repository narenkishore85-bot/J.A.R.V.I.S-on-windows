
"""J.A.R.V.I.S. - Windows voice assistant main controller.

Flow:

    Startup
        ↓
    Greeting
        ↓
    Wake word
        ↓
    "Yes, sir?"
        ↓
    Listen for command
        ↓
    Execute command
        ↓
    Speak response
        ↓
    Keep listening for commands
        ↓
    Session timeout
        ↓
    Return to wake-word listening

Designed for fast local/offline operation.
"""

import logging
from logging.handlers import RotatingFileHandler
import random
import re
import subprocess
import threading
import time

import pystray
from PIL import Image, ImageDraw

from . import config
from . import stt
from . import tts
from . import wake_listener
from .jarvis_launcher import launch_from_command


# ============================================================
# GLOBAL STATE
# ============================================================

STOP = threading.Event()
PAUSED = threading.Event()

TRAY = None
WAKE_MODEL = None


# ============================================================
# LOGGING
# ============================================================

def setup_logging():
    """Configure both file and PowerShell console logging."""

    config.LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Prevent duplicate handlers.
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # --------------------------------------------------------
    # File logging
    # --------------------------------------------------------

    file_handler = RotatingFileHandler(
        config.LOG_PATH,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )

    file_handler.setFormatter(formatter)

    # --------------------------------------------------------
    # Console logging
    # --------------------------------------------------------

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)


log = logging.getLogger("jarvis")


# ============================================================
# RANDOM RESPONSE HELPER
# ============================================================

def choose(options):
    """Return a random response from a list."""
    return random.choice(options)


# ============================================================
# NATURAL RESPONSES
# ============================================================

RESPONSES = {

    "open": [
        "Certainly, sir. It's opening now.",
        "Right away, sir. Consider it handled.",
        "Of course, sir. Launching it now.",
        "As requested, sir. It's on its way.",
        "Already on it, sir. Give me just a moment.",
        "Done, sir. You may proceed.",
        "And there we are, sir. Everything is in place.",
        "Consider it handled, sir. That was rather straightforward.",
        "Certainly, sir. I've taken care of it.",
        "At once, sir. The application is launching.",
    ],

    "close": [
        "Certainly, sir. I'll take care of that.",
        "Right away, sir. Closing it now.",
        "Of course, sir. That's being dealt with.",
        "Done, sir. One less distraction.",
        "As you wish, sir. It's closed.",
        "Consider it handled, sir.",
        "And that's taken care of, sir.",
        "Certainly, sir. We've cleared that from the screen.",
    ],

    "search": [
        "Searching now, sir. Let's see what we can find.",
        "Certainly, sir. I'll have a look.",
        "On it, sir. Searching your files now.",
        "Right away, sir. Let's track it down.",
        "Searching, sir. Hopefully it hasn't decided to disappear.",
        "Of course, sir. I'll interrogate the filesystem.",
        "I'm on it, sir. Give me a moment.",
        "Searching now, sir. I have a feeling we'll find it.",
    ],

    "not_found": [
        "I'm afraid I couldn't locate it, sir.",
        "Nothing turned up, sir. It seems to be hiding.",
        "I couldn't find that file, sir. Perhaps we should try another approach.",
        "No luck, sir. The file appears to be playing hard to get.",
        "I'm afraid that's eluding me, sir.",
        "Nothing useful came up, sir. I suspect the file has gone into hiding.",
    ],

    "unclear": [
        "I'm sorry, sir. I didn't quite catch that.",
        "Could you repeat that, sir?",
        "I'm afraid I missed that, sir.",
        "One more time, sir. I wasn't quite able to make that out.",
        "You'll have to repeat that, sir. I seem to have missed an important detail.",
        "I'm listening, sir. Please try that once more.",
    ],

    "general": [
        "Certainly, sir.",
        "Right away, sir.",
        "Of course, sir.",
        "Consider it handled, sir.",
        "Already on it, sir.",
        "As you wish, sir.",
        "Done, sir.",
        "At once, sir.",
        "I've got it, sir.",
        "Naturally, sir.",
    ],

    "humor": [
        "Done, sir. That was almost disappointingly easy.",
        "Certainly, sir. I was beginning to feel underutilized.",
        "Already handled, sir. You do make this look remarkably easy.",
        "Done, sir. Another crisis successfully avoided.",
        "Of course, sir. I'll pretend that was difficult.",
        "Consider it handled, sir. We can all breathe again.",
        "Right away, sir. I do enjoy being useful.",
        "Completed, sir. No explosions required.",
        "Done, sir. Your impeccable timing strikes again.",
        "Naturally, sir. What else would I be doing?",
    ],
}


def natural_response(action_type="general"):
    """
    Generate a short cinematic response.

    The response is selected according to the actual action type.
    Humor is occasionally used for variety.
    """

    pool = RESPONSES.get(
        action_type,
        RESPONSES["general"],
    )

    if random.random() < 0.18:
        pool = RESPONSES["humor"]

    return choose(pool)


# ============================================================
# DETERMINE ACTION TYPE
# ============================================================

def get_action_type(command):
    """
    Determine which response category belongs to a command.

    This does NOT execute the command.
    """

    text = command.lower().strip()

    # Google search
    if (
        "search google" in text
        or "google search" in text
    ):
        return "search"

    # Search / find
    if (
        text.startswith("search ")
        or text.startswith("find ")
        or text.startswith("locate ")
        or text.startswith("where is ")
        or text.startswith("where are ")
    ):
        return "search"

    # Close
    if (
        text.startswith("close ")
        or text.startswith("exit ")
        or text.startswith("quit ")
    ):
        return "close"

    # Open
    if (
        text.startswith("open ")
        or text.startswith("launch ")
        or text.startswith("start ")
        or text.startswith("run ")
    ):
        return "open"

    return "general"


# ============================================================
# TRAY ICON
# ============================================================

def make_icon(paused=False):
    """Create a simple tray icon."""

    image = Image.new(
        "RGB",
        (64, 64),
        "black",
    )

    draw = ImageDraw.Draw(image)

    if paused:

        draw.rectangle(
            (17, 17, 47, 47),
            outline="white",
            width=4,
        )

        draw.line(
            (24, 24, 40, 40),
            fill="white",
            width=4,
        )

        draw.line(
            (40, 24, 24, 40),
            fill="white",
            width=4,
        )

    else:

        draw.ellipse(
            (15, 15, 49, 49),
            outline="white",
            width=4,
        )

        draw.ellipse(
            (27, 27, 37, 37),
            fill="white",
        )

    return image


def update_tray():
    """Update tray icon and title."""

    if TRAY is None:
        return

    TRAY.icon = make_icon(
        PAUSED.is_set()
    )

    TRAY.title = (
        "Jarvis — Paused"
        if PAUSED.is_set()
        else "Jarvis — Listening"
    )

    try:
        TRAY.update_menu()
    except Exception:
        pass


# ============================================================
# TRAY PAUSE / RESUME
# ============================================================

def toggle_pause(icon=None, item=None):
    """Pause or resume microphone listening."""

    if PAUSED.is_set():

        PAUSED.clear()

        log.info(
            "Listening resumed from tray."
        )

        try:

            tts.speak(
                choose([
                    "Listening resumed, sir.",
                    "I'm listening again, sir.",
                    "Back online and listening, sir.",
                ])
            )

        except Exception:
            log.exception(
                "Failed to speak resume message."
            )

    else:

        PAUSED.set()

        log.info(
            "Listening paused."
        )

        try:

            tts.speak(
                choose([
                    "Listening paused, sir.",
                    "I'll wait here, sir.",
                    "Paused, sir.",
                ])
            )

        except Exception:
            log.exception(
                "Failed to speak pause message."
            )

    update_tray()


# ============================================================
# OPEN LOG
# ============================================================

def open_log(icon=None, item=None):
    """Open the JARVIS log file in Windows Explorer."""

    try:

        config.LOG_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not config.LOG_PATH.exists():
            config.LOG_PATH.touch()

        subprocess.Popen([
            "explorer",
            "/select,",
            str(config.LOG_PATH),
        ])

    except Exception:
        log.exception(
            "Could not open log file."
        )


# ============================================================
# QUIT
# ============================================================

def quit_app(icon=None, item=None):
    """Stop JARVIS."""

    log.info(
        "Quit requested."
    )

    STOP.set()
    PAUSED.set()

    if icon is not None:

        try:
            icon.stop()
        except Exception:
            pass


# ============================================================
# TRAY THREAD
# ============================================================

def tray_thread():
    """Run the system tray icon."""

    global TRAY

    try:

        menu = pystray.Menu(

            pystray.MenuItem(
                lambda text:
                    "Resume listening"
                    if PAUSED.is_set()
                    else "Pause listening",
                toggle_pause,
            ),

            pystray.MenuItem(
                "Open log file",
                open_log,
            ),

            pystray.MenuItem(
                "Quit",
                quit_app,
            ),
        )

        TRAY = pystray.Icon(
            "Jarvis",
            make_icon(False),
            "Jarvis — Listening",
            menu,
        )

        TRAY.run()

    except Exception:

        log.exception(
            "System tray thread failed."
        )


# ============================================================
# YES / NO CONFIRMATION
# ============================================================

def is_yes(text):
    """Return True when text represents confirmation."""

    text = text.lower().strip()

    yes_patterns = (
        r"\byes\b",
        r"\byep\b",
        r"\byeah\b",
        r"\byup\b",
        r"\bsure\b",
        r"\bof course\b",
        r"\bdo it\b",
        r"\bgo ahead\b",
        r"\bplease do\b",
    )

    return any(
        re.search(
            pattern,
            text,
        )
        for pattern in yes_patterns
    )


def is_no(text):
    """Return True when text represents rejection."""

    text = text.lower().strip()

    no_patterns = (
        r"\bno\b",
        r"\bnope\b",
        r"\bnah\b",
        r"\bnot now\b",
        r"\bcancel\b",
        r"\bdon't\b",
        r"\bdo not\b",
    )

    return any(
        re.search(
            pattern,
            text,
        )
        for pattern in no_patterns
    )


def listen_for_confirmation():
    """Listen for a yes/no response."""

    if STOP.is_set() or PAUSED.is_set():
        return None

    time.sleep(0.08)

    log.info(
        "Listening for confirmation."
    )

    try:

        answer = stt.record_and_transcribe(
            min(
                getattr(
                    config,
                    "COMMAND_RECORD_SECONDS",
                    4,
                ),
                4,
            )
        )

    except KeyboardInterrupt:

        log.info(
            "Confirmation listening interrupted."
        )

        return None

    except Exception:

        log.exception(
            "Confirmation recording failed."
        )

        return None

    if not answer:
        return None

    log.info(
        "Confirmation recognized: %r",
        answer,
    )

    if is_yes(answer):
        return True

    if is_no(answer):
        return False

    return None


# ============================================================
# TTS HELPER
# ============================================================

def speak_response(text):
    """
    Centralized TTS wrapper.

    Keeps speech failures from breaking the assistant loop.
    """

    if not text:
        return

    log.info(
        "TTS START: %s",
        text,
    )

    try:

        tts.speak(text)

        log.info(
            "TTS END."
        )

    except Exception:

        log.exception(
            "TTS failed."
        )


# ============================================================
# COMMAND EXECUTION
# ============================================================

def execute_command(command):
    """
    Execute a command using the existing launcher.

    Returns:
        action_type
    """

    action_type = get_action_type(
        command
    )

    log.info(
        "Action type: %s",
        action_type,
    )

    launch_from_command(
        command
    )

    return action_type


# ============================================================
# COMMAND SESSION
# ============================================================

def command_session():
    """
    Stay in command mode after the wake word.

    Every successfully recognized command resets the timeout.

    Flow:

        Wake
          ↓
        Command
          ↓
        Response
          ↓
        More commands
          ↓
        Timeout
          ↓
        Wake mode
    """

    session_timeout = float(
        getattr(
            config,
            "COMMAND_SESSION_SECONDS",
            8,
        )
    )

    last_command_time = time.monotonic()

    while not STOP.is_set():

        # ----------------------------------------------------
        # PAUSE
        # ----------------------------------------------------

        if PAUSED.is_set():

            time.sleep(0.1)

            continue

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        elapsed = (
            time.monotonic()
            - last_command_time
        )

        if elapsed >= session_timeout:

            log.info(
                "Command session timed out after %.1f seconds.",
                elapsed,
            )

            break

        # ----------------------------------------------------
        # LISTEN
        # ----------------------------------------------------

        log.info(
            "Listening for command..."
        )

        try:

            command = (
                stt.record_and_transcribe(
                    seconds=5
                )
            )

        except KeyboardInterrupt:

            log.info(
                "Command listening interrupted."
            )

            STOP.set()

            break

        except Exception:

            log.exception(
                "Speech recognition failed."
            )

            # Don't kill the whole assistant because of one
            # recording failure.
            time.sleep(0.2)

            continue

        if STOP.is_set():
            break

        # ----------------------------------------------------
        # NOTHING HEARD
        # ----------------------------------------------------

        if not command:

            log.info(
                "No command detected."
            )

            continue

        command = command.strip()

        if not command:
            continue

        # ----------------------------------------------------
        # COMMAND RECEIVED
        # ----------------------------------------------------

        log.info(
            "COMMAND RECOGNIZED: %s",
            command,
        )

        # Reset session timeout immediately after a command
        # is successfully recognized.
        last_command_time = time.monotonic()

        # ----------------------------------------------------
        # EXECUTE
        # ----------------------------------------------------

        try:

            action_type = execute_command(
                command
            )

            log.info(
                "Command execution completed."
            )

        except Exception:

            log.exception(
                "Command execution failed: %s",
                command,
            )

            speak_response(
                "I'm afraid something went wrong, sir."
            )

            last_command_time = time.monotonic()

            continue

        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        response = natural_response(
            action_type
        )

        log.info(
            "RESPONSE: %s",
            response,
        )

        speak_response(
            response
        )

        # Give SAPI5/audio hardware a moment to finish releasing
        # the output device before STT opens the microphone.
        time.sleep(0.15)

        # Response time should NOT consume the user's next-command
        # timeout. Start a fresh session timer after speaking.
        last_command_time = time.monotonic()

    log.info(
        "Command session ended."
    )


# ============================================================
# ASSISTANT LOOP
# ============================================================

def assistant_loop():
    """
    Main J.A.R.V.I.S. state machine.

    Flow:

        Hey Jarvis
             ↓
        Wake detected
             ↓
        "Yes, sir?"
             ↓
        Command session
             ↓
        Multiple commands
             ↓
        Session timeout
             ↓
        Wake-word listening again
    """

    log.info(
        "J.A.R.V.I.S. assistant loop started."
    )

    while not STOP.is_set():

        # ====================================================
        # PAUSED
        # ====================================================

        if PAUSED.is_set():

            time.sleep(0.1)

            continue

        # ====================================================
        # WAKE MODE
        # ====================================================

        log.info(
            "=============================================="
        )

        log.info(
            "WAITING FOR WAKE WORD"
        )

        log.info(
            "Say: Hey Jarvis"
        )

        log.info(
            "=============================================="
        )

        try:

            detected = (
                wake_listener.listen_for_wake_word(
                    stop_event=STOP,
                    pause_event=PAUSED,
                )
            )

        except KeyboardInterrupt:

            log.info(
                "Wake listening interrupted."
            )

            STOP.set()

            break

        except Exception:

            log.exception(
                "Wake listener failed."
            )

            if STOP.wait(0.5):
                break

            continue

        if STOP.is_set():
            break

        if PAUSED.is_set():
            continue

        if not detected:
            continue

        # ====================================================
        # WAKE WORD DETECTED
        # ====================================================

        log.info(
            "=============================================="
        )

        log.info(
            "WAKE WORD DETECTED"
        )

        log.info(
            "=============================================="
        )

        # ----------------------------------------------------
        # WAKE ACKNOWLEDGEMENT
        # ----------------------------------------------------

        log.info(
            "Speaking wake acknowledgement..."
        )

        speak_response("Yes, sir?")

        log.info(
            "Wake acknowledgement finished."
        )

        # ----------------------------------------------------
        # COMMAND SESSION
        # ----------------------------------------------------

        command_session()

        # ----------------------------------------------------
        # RETURN TO WAKE MODE
        # ----------------------------------------------------

        if STOP.is_set():
            break

        log.info(
            "Command session ended."
        )

        log.info(
            "Returning to wake-word listening."
        )

        # ====================================================
        # WAKE DETECTED
        # ====================================================

        log.info(
            "WAKE WORD DETECTED."
        )

        # ----------------------------------------------------
        # WAKE ACKNOWLEDGEMENT
        # ----------------------------------------------------

        speak_response(
            "Yes, sir?"
        )

        if STOP.is_set() or PAUSED.is_set():
            continue

        # ----------------------------------------------------
        # COMMAND SESSION
        # ----------------------------------------------------

        command_session()

        if STOP.is_set():
            break

        # ====================================================
        # RETURN TO WAKE MODE
        # ====================================================

        log.info(
            "Command session finished."
        )

        log.info(
            "Returning to wake-word listening."
        )

        # Small gap prevents immediate re-triggering from
        # residual microphone/audio data.
        time.sleep(0.25)

    log.info(
        "J.A.R.V.I.S. assistant loop stopped."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    global WAKE_MODEL

    # --------------------------------------------------------
    # LOGGING
    # --------------------------------------------------------

    setup_logging()

    log.info(
        "=============================================="
    )

    log.info(
        "J.A.R.V.I.S. starting."
    )

    log.info(
        "Project directory: %s",
        config.BASE_DIR,
    )

    log.info(
        "=============================================="
    )

    # --------------------------------------------------------
    # TTS WARM-UP
    # --------------------------------------------------------

    try:

        log.info(
            "Initializing TTS..."
        )

        tts.warm_up()

        log.info(
            "TTS warmed up successfully."
        )

    except Exception:

        log.exception(
            "TTS warm-up failed."
        )

    # --------------------------------------------------------
    # VOSK
    # --------------------------------------------------------

    try:

        log.info(
            "Loading Vosk model..."
        )

        stt.load_model()

        log.info(
            "Vosk model loaded."
        )

    except Exception:

        log.exception(
            "Vosk model loading failed."
        )

        return

    # --------------------------------------------------------
    # OPENWAKEWORD
    # --------------------------------------------------------

    try:

        log.info(
            "Loading wake-word model..."
        )

        WAKE_MODEL = (
            wake_listener.load_wake_model()
        )

        log.info(
            "Wake-word model loaded."
        )

    except Exception:

        log.exception(
            "Wake-word model loading failed."
        )

        return

    # --------------------------------------------------------
    # SYSTEM TRAY
    # --------------------------------------------------------

    try:

        tray = threading.Thread(
            target=tray_thread,
            name="JarvisTray",
            daemon=True,
        )

        tray.start()

        log.info(
            "System tray started."
        )

    except Exception:

        log.exception(
            "Could not start system tray."
        )

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    log.info(
        "JARVIS READY."
    )

    log.info(
        "Say: Hey Jarvis"
    )

    # --------------------------------------------------------
    # STARTUP GREETING
    # --------------------------------------------------------

    speak_response(
        "Good evening, sir. JARVIS is online and ready."
    )

    # Small delay so the TTS output is fully finished before
    # microphone wake detection begins.
    time.sleep(0.3)

    # --------------------------------------------------------
    # ASSISTANT
    # --------------------------------------------------------

    try:

        assistant_loop()

    except KeyboardInterrupt:

        log.info(
            "J.A.R.V.I.S. interrupted by user."
        )

        STOP.set()

    except Exception:

        log.exception(
            "Fatal assistant-loop error."
        )

    finally:

        STOP.set()

        PAUSED.set()

        log.info(
            "J.A.R.V.I.S. stopped."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
