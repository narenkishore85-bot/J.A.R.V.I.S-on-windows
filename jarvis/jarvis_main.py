"""J.A.R.V.I.S. - Windows voice assistant main controller.

Flow:
    Wake word
        ↓
    Wake sound
        ↓
    Greeting
        ↓
    Listen for command
        ↓
    Execute command
        ↓
    Natural response

Designed for fast local/offline operation.
"""

import logging
from logging.handlers import RotatingFileHandler
import random
import re
import subprocess
import threading
import time
import winsound

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
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        handler = RotatingFileHandler(
            config.LOG_PATH,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )

        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )

        root.addHandler(handler)


log = logging.getLogger("jarvis")


# ============================================================
# RANDOM RESPONSE HELPER
# ============================================================

def choose(options):
    """Return a random response from a list."""
    return random.choice(options)


# ============================================================
# GREETING
# ============================================================

def get_greeting():
    """Return a short natural greeting."""

    hour = time.localtime().tm_hour

    if 5 <= hour < 12:
        return choose([
            "Good morning, sir.",
            "Morning, sir.",
            "Good morning, sir. What can I do for you?",
            "Morning, sir. I'm listening.",
        ])

    if 12 <= hour < 17:
        return choose([
            "Good afternoon, sir.",
            "Afternoon, sir.",
            "Good afternoon, sir. What can I do for you?",
            "Good afternoon, sir. I'm listening.",
        ])

    if 17 <= hour < 22:
        return choose([
            "Good evening, sir.",
            "Evening, sir.",
            "Good evening, sir. What can I do for you?",
            "Evening, sir. I'm listening.",
        ])

    return choose([
        "Good evening, sir.",
        "Evening, sir.",
        "Good evening, sir. What can I do for you?",
        "Evening, sir. I'm listening.",
    ])


# ============================================================
# NATURAL COMMAND RESPONSE
# ============================================================

def natural_response(action_type="general"):
    responses = {

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
            "At once, sir. The application is launching."
        ],

        "close": [
            "Certainly, sir. I'll take care of that.",
            "Right away, sir. Closing it now.",
            "Of course, sir. That's being dealt with.",
            "Done, sir. One less distraction.",
            "As you wish, sir. It's closed.",
            "Consider it handled, sir.",
            "And that's taken care of, sir.",
            "Certainly, sir. We've cleared that from the screen."
        ],

        "search": [
            "Searching now, sir. Let's see what we can find.",
            "Certainly, sir. I'll have a look.",
            "On it, sir. Searching your files now.",
            "Right away, sir. Let's track it down.",
            "Searching, sir. Hopefully it hasn't decided to disappear.",
            "Of course, sir. I'll interrogate the filesystem.",
            "I'm on it, sir. Give me a moment.",
            "Searching now, sir. I have a feeling we'll find it."
        ],

        "not_found": [
            "I'm afraid I couldn't locate it, sir.",
            "Nothing turned up, sir. It seems to be hiding.",
            "I couldn't find that file, sir. Perhaps we should try another approach.",
            "No luck, sir. The file appears to be playing hard to get.",
            "I'm afraid that's eluding me, sir.",
            "Nothing useful came up, sir. I suspect the file has gone into hiding."
        ],

        "unclear": [
            "I'm sorry, sir. I didn't quite catch that.",
            "Could you repeat that, sir?",
            "I'm afraid I missed that, sir.",
            "One more time, sir. I wasn't quite able to make that out.",
            "You'll have to repeat that, sir. I seem to have missed an important detail.",
            "I'm listening, sir. Please try that once more."
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
            "Naturally, sir."
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
            "Naturally, sir. What else would I be doing?"
        ]
    }

    pool = responses.get(action_type, responses["general"])

    # Occasionally use a humorous response
    if random.random() < 0.18:
        pool = responses["humor"]

    return random.choice(pool)


    # --------------------------------------------------------
    # GOOGLE
    # --------------------------------------------------------

    if "search google" in text or "google search" in text:
        return choose([
            "There we go, sir.",
            "Google is on it, sir.",
            "Search opened, sir.",
            "Done, sir. Your search is underway.",
        ])

    # --------------------------------------------------------
    # CLAUDE
    # --------------------------------------------------------

    if "claude" in text:
        return choose([
            "Claude is opening, sir.",
            "There you go, sir. Claude is ready.",
            "Claude is on its way, sir.",
        ])

    # --------------------------------------------------------
    # CLOSE / EXIT / QUIT
    # --------------------------------------------------------

    if text.startswith("close "):
        app = text[6:].strip()

        return choose([
            f"Closing {app}, sir.",
            f"{app} is closed, sir.",
            f"Done, sir. {app} is out of the way.",
            f"Consider {app} closed, sir.",
        ])

    if text.startswith("exit "):
        app = text[5:].strip()

        return choose([
            f"Closing {app}, sir.",
            f"{app} is closed, sir.",
            f"Done, sir. {app} is out of the way.",
        ])

    if text.startswith("quit "):
        app = text[5:].strip()

        return choose([
            f"Closing {app}, sir.",
            f"{app} is closed, sir.",
            f"Done, sir.",
        ])

    # --------------------------------------------------------
    # OPEN / LAUNCH / START
    # --------------------------------------------------------

    if (
        text.startswith("open ")
        or text.startswith("launch ")
        or text.startswith("start ")
        or text.startswith("run ")
    ):

        if text.startswith("open "):
            target = text[5:].strip()
        elif text.startswith("launch "):
            target = text[7:].strip()
        elif text.startswith("start "):
            target = text[6:].strip()
        else:
            target = text[4:].strip()

        return choose([
            f"{target} is open, sir.",
            f"There you go, sir. {target} is ready.",
            f"{target} is up and running, sir.",
            f"Done, sir. {target} is on its way.",
            f"Consider it handled, sir. {target} is open.",
        ])

    # --------------------------------------------------------
    # FIND / LOCATE
    # --------------------------------------------------------

    if (
        text.startswith("find ")
        or text.startswith("locate ")
        or text.startswith("where is ")
        or text.startswith("where are ")
    ):
        return choose([
            "I found it, sir.",
            "Search complete, sir.",
            "There we go, sir.",
            "Found what you were looking for, sir.",
        ])

    # --------------------------------------------------------
    # GENERIC SUCCESS
    # --------------------------------------------------------

    return choose([
        "Done, sir.",
        "Consider it handled, sir.",
        "Right away, sir.",
        "All set, sir.",
        "Done and dusted, sir.",
        "Handled, sir.",
        "There we go, sir.",
    ])


# ============================================================
# TRAY ICON
# ============================================================

def make_icon(paused=False):
    image = Image.new("RGB", (64, 64), "black")
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
    if TRAY is not None:
        TRAY.icon = make_icon(PAUSED.is_set())

        TRAY.title = (
            "Jarvis — Paused"
            if PAUSED.is_set()
            else "Jarvis — Listening"
        )

        TRAY.update_menu()


def toggle_pause(icon=None, item=None):

    if PAUSED.is_set():
        PAUSED.clear()

        log.info("Listening resumed from tray.")

        tts.speak(
            choose([
                "Listening resumed, sir.",
                "I'm listening again, sir.",
                "Back online and listening, sir.",
            ])
        )

    else:
        PAUSED.set()

        log.info(
            "Listening paused; microphone capture will be released."
        )

        tts.speak(
            choose([
                "Listening paused, sir.",
                "I'll wait here, sir.",
                "Paused, sir.",
            ])
        )

    update_tray()


def open_log(icon=None, item=None):

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
        log.exception("Could not open log file.")


def quit_app(icon=None, item=None):

    log.info("Quit requested.")

    STOP.set()
    PAUSED.set()

    if icon is not None:
        icon.stop()


def tray_thread():

    global TRAY

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


# ============================================================
# YES / NO CONFIRMATION
# ============================================================

def is_yes(text):

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
        re.search(pattern, text)
        for pattern in yes_patterns
    )


def is_no(text):

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
        re.search(pattern, text)
        for pattern in no_patterns
    )


def listen_for_confirmation():

    if STOP.is_set() or PAUSED.is_set():
        return None

    time.sleep(0.08)

    log.info("Listening for confirmation.")

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
# FILE SEARCH / GOOGLE FOLLOW-UP
# ============================================================

def extract_search_subject(command):

    text = command.lower().strip()

    patterns = (
        r"^(?:find|locate|search for|where is|where are)\s+(.+)$",

        r"^(?:find|locate)\s+(.+?)\s+on my "
        r"(?:pc|computer)$",
    )

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
        )

        if match:
            return match.group(1).strip()

    return text


def should_offer_google_followup(command):

    text = command.lower().strip()

    return (
        text.startswith("find ")
        or text.startswith("locate ")
        or text.startswith("where is ")
        or text.startswith("where are ")
    )


def ask_google_followup(command):

    subject = extract_search_subject(command)

    tts.speak(
        f"I've completed the search for {subject}. "
        "Would you like me to search Google for it?"
    )

    answer = listen_for_confirmation()

    if answer is True:

        tts.speak(
            f"Certainly, sir. "
            f"Searching Google for {subject}."
        )

        google_command = (
            f"search google for {subject}"
        )

        try:

            success = launch_from_command(
                google_command
            )

            if not success:
                tts.speak(
                    "I couldn't open the Google search, sir."
                )

        except Exception:

            log.exception(
                "Google follow-up failed."
            )

            tts.speak(
                "I couldn't open the Google search, sir."
            )

    elif answer is False:

        tts.speak(
            choose([
                "Alright, sir.",
                "No problem, sir.",
                "Fair enough, sir.",
            ])
        )

    else:

        tts.speak(
            "I didn't catch that, sir. "
            "I'll leave it there."
        )


# ============================================================
# MAIN ASSISTANT LOOP
# ============================================================

def assistant_loop():
    """
    Main J.A.R.V.I.S. loop.

    Flow:

        Hey Jarvis
             ↓
        Wake detected
             ↓
        Listen for command
             ↓
        Execute command
             ↓
        Speak response
             ↓
        Keep listening for more commands
             ↓
        Silence timeout
             ↓
        Return to wake-word listening
    """

    log.info("J.A.R.V.I.S. assistant loop started.")

    # How long JARVIS stays in command mode when you stop speaking.
    SESSION_TIMEOUT = 8.0

    while not STOP.is_set():

        # =========================================================
        # WAKE MODE
        # =========================================================

        if PAUSED.is_set():
            time.sleep(0.1)
            continue

        try:
            detected = wake_listener.listen_for_wake_word(
                stop_event=STOP,
                pause_event=PAUSED
            )

        except Exception:
            log.exception("Wake listener failed.")
            time.sleep(0.5)
            continue

        if STOP.is_set():
            break

        if not detected:
            continue

        log.info("Wake word detected.")

        # =========================================================
        # COMMAND MODE
        #
        # Once awakened, DO NOT listen for "Hey Jarvis" again.
        # Listen directly for commands.
        # =========================================================

        last_command_time = time.time()

        while not STOP.is_set():

            if PAUSED.is_set():
                time.sleep(0.1)
                continue

            # -----------------------------------------------------
            # Check session timeout
            # -----------------------------------------------------

            if time.time() - last_command_time > SESSION_TIMEOUT:
                log.info(
                    "Command session timed out. "
                    "Returning to wake-word mode."
                )
                break

            # -----------------------------------------------------
            # Listen for command
            # -----------------------------------------------------

            try:
                command = stt.record_and_transcribe(seconds=5)

            except KeyboardInterrupt:
                log.info("Command listening interrupted.")
                break

            except Exception:
                log.exception("Speech recognition failed.")
                break

            if STOP.is_set():
                break

            # -----------------------------------------------------
            # Nothing heard
            # -----------------------------------------------------

            if not command:
                continue

            command = command.strip()

            if not command:
                continue

            log.info("Command recognized: %s", command)

            # Reset timeout because we received a command.
            last_command_time = time.time()

            # =====================================================
            # EXECUTE COMMAND
            # =====================================================

            try:

                launch_from_command(command)

                log.info(
                    "Command execution completed: %s",
                    command
                )

            except Exception:
                log.exception(
                    "Command execution failed: %s",
                    command
                )

                try:
                    tts.speak(
                        "I'm afraid something went wrong, sir."
                    )
                except Exception:
                    log.exception(
                        "Failed to speak error response."
                    )

                # Continue listening for another command.
                last_command_time = time.time()
                continue

            # =====================================================
            # SPEAK RESPONSE
            # =====================================================

            try:

                response = natural_response("general")

                log.info(
                    "J.A.R.V.I.S. response: %s",
                    response
                )

                tts.speak(response)

            except Exception:
                log.exception(
                    "J.A.R.V.I.S. response/TTS failed."
                )

            # -----------------------------------------------------
            # Give the microphone a tiny moment to settle after
            # JARVIS finishes speaking.
            # -----------------------------------------------------

            time.sleep(0.15)

            # Keep command mode alive.
            last_command_time = time.time()

        # =========================================================
        # COMMAND MODE ENDED
        #
        # Now go back to waiting for "Hey Jarvis".
        # =========================================================

        log.info(
            "Returning to wake-word listening."
        )

        time.sleep(0.2)

    log.info("J.A.R.V.I.S. assistant loop stopped.")


# ============================================================
# MAIN
# ============================================================

def main():

    global WAKE_MODEL

    setup_logging()

    log.info(
        "Jarvis starting."
    )

    log.info(
        "Project directory: %s",
        config.BASE_DIR,
    )

    # --------------------------------------------------------
    # WARM UP TTS
    # --------------------------------------------------------

    try:

        tts.warm_up()

        log.info(
            "TTS warmed up successfully."
        )

    except Exception:

        log.exception(
            "TTS warm-up failed."
        )

    # --------------------------------------------------------
    # LOAD VOSK
    # --------------------------------------------------------

    log.info(
        "Loading Vosk model."
    )

    stt.load_model()

    log.info(
        "Vosk model loaded."
    )

    # --------------------------------------------------------
    # LOAD WAKE MODEL
    # --------------------------------------------------------

    log.info(
        "Loading wake-word model."
    )

    WAKE_MODEL = (
        wake_listener.load_wake_model()
    )

    log.info(
        "Wake-word model loaded."
    )

    # --------------------------------------------------------
    # SYSTEM TRAY
    # --------------------------------------------------------

    tray = threading.Thread(
        target=tray_thread,
        name="JarvisTray",
        daemon=True,
    )

    tray.start()

    log.info(
        "Jarvis ready. Waiting for wake word."
    )

    # IMPORTANT:
    # No startup voice here.
    assistant_loop()

    log.info(
        "Jarvis stopped."
    )


if __name__ == "__main__":
    main()
