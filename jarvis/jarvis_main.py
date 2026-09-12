"""Entry point for J.A.R.V.I.S Basic v1.

Basic v1 adds:
- spoken response after commands
- simple yes/no confirmation
- continuous voice interaction
- keeps the existing wake-word, Vosk STT, tray and launcher architecture
- does NOT use an LLM yet
"""

import logging
from logging.handlers import RotatingFileHandler
import re
import subprocess
import threading
import time
import winsound

import pystray
from PIL import Image, ImageDraw
import pyttsx3

import config
import stt
import wake_listener
from jarvis_launcher import launch_from_command


STOP = threading.Event()
PAUSED = threading.Event()
TRAY = None
WAKE_MODEL = None

# TTS is created once and used from the assistant thread.
TTS = None


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


def setup_tts():
    """Initialize the offline Windows speech engine."""
    global TTS

    try:
        TTS = pyttsx3.init()

        # Keep the voice reasonably natural and not too slow.
        TTS.setProperty("rate", 175)
        TTS.setProperty("volume", 1.0)

        log.info("Text-to-speech initialized.")

    except Exception:
        TTS = None
        log.exception("Could not initialize text-to-speech.")


def speak(text):
    """Speak a response and also log it."""
    if not text:
        return

    text = str(text).strip()
    log.info("JARVIS: %s", text)

    if TTS is None:
        return

    try:
        TTS.say(text)
        TTS.runAndWait()
    except Exception:
        log.exception("Speech failed.")


def make_icon(paused=False):
    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)

    if paused:
        draw.rectangle((17, 17, 47, 47), outline="white", width=4)
        draw.line((24, 24, 40, 40), fill="white", width=4)
        draw.line((40, 24, 24, 40), fill="white", width=4)
    else:
        draw.ellipse((15, 15, 49, 49), outline="white", width=4)
        draw.ellipse((27, 27, 37, 37), fill="white")

    return image


def play_wake_sound():
    """
    Play the wake confirmation sound synchronously.

    Synchronous playback prevents the command recorder
    from accidentally recording the wake sound.
    """
    try:
        if config.WAKE_SOUND_PATH.is_file():
            winsound.PlaySound(
                str(config.WAKE_SOUND_PATH),
                winsound.SND_FILENAME,
            )
        else:
            winsound.Beep(880, 100)
            winsound.Beep(1175, 100)

    except Exception:
        log.exception("Wake sound failed.")


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
        speak("Listening resumed.")
    else:
        PAUSED.set()
        log.info("Listening paused; microphone capture will be released.")
        speak("Listening paused.")

    update_tray()


def open_log(icon=None, item=None):
    try:
        config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        if not config.LOG_PATH.exists():
            config.LOG_PATH.touch()

        subprocess.Popen(
            ["explorer", "/select,", str(config.LOG_PATH)]
        )

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

    return any(re.search(pattern, text) for pattern in yes_patterns)


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

    return any(re.search(pattern, text) for pattern in no_patterns)


def listen_for_confirmation():
    """
    Listen for a short yes/no answer.

    The normal wake-word detector is not running during this state.
    """
    if STOP.is_set() or PAUSED.is_set():
        return None

    time.sleep(0.15)

    log.info("Listening for confirmation.")

    try:
        answer = stt.record_and_transcribe(
            min(getattr(config, "COMMAND_RECORD_SECONDS", 4), 4)
        )
    except Exception:
        log.exception("Confirmation recording failed.")
        return None

    if not answer:
        return None

    log.info("Confirmation recognized: %r", answer)

    if is_yes(answer):
        return True

    if is_no(answer):
        return False

    return None


def command_response(command, success):
    """
    Basic v1 response generator.

    This deliberately stays deterministic. In the next version this
    function can be replaced by an LLM-based intent/response layer.
    """
    text = command.lower().strip()

    if not success:
        if text.startswith(("find ", "locate ", "where is ", "where are ")):
            return "I couldn't complete that search."
        return "I couldn't complete that command."

    if text.startswith(("search google", "google search", "search the web",
                        "search web")) or " on google" in text:
        return "Searching Google."

    if text.startswith(("ask claude", "tell claude", "command claude",
                         "give claude", "claude ")):
        return "Certainly. Sending that to Claude."

    if text.startswith(("find ", "locate ", "where is ", "where are ")):
        return "I've completed the search."

    if text.startswith(("close ", "exit ", "quit ")):
        target = re.sub(
            r"^(close|exit|quit)\s+",
            "",
            text,
            count=1,
        ).strip()

        if target:
            return f"Closing {target}."
        return "Closing the application."

    if text.startswith(("open ", "launch ", "start ", "run ")):
        target = re.sub(
            r"^(open|launch|start|run)\s+",
            "",
            text,
            count=1,
        ).strip()

        if target:
            return f"Opening {target}."
        return "Opening the application."

    return "Certainly. The command has been completed."


def extract_search_subject(command):
    """Return a useful phrase for a follow-up Google-search question."""
    text = command.lower().strip()

    patterns = (
        r"^(?:find|locate|search for|where is|where are)\s+(.+)$",
        r"^(?:find|locate)\s+(.+?)\s+on my (?:pc|computer)$",
    )

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    return text


def should_offer_google_followup(command):
    """
    Only file/PC-location requests get the confirmation in Basic v1.

    This prevents JARVIS from asking unnecessary questions after every
    normal command.
    """
    text = command.lower().strip()

    return (
        text.startswith("find ")
        or text.startswith("locate ")
        or text.startswith("where is ")
        or text.startswith("where are ")
    )


def ask_google_followup(command):
    subject = extract_search_subject(command)

    speak(
        f"I've completed the search for {subject}. "
        f"Would you like me to search Google for it?"
    )

    answer = listen_for_confirmation()

    if answer is True:
        speak(f"Certainly. Searching Google for {subject}.")

        google_command = f"search google for {subject}"

        try:
            success = launch_from_command(google_command)
            if not success:
                speak("I couldn't open the Google search.")
        except Exception:
            log.exception("Google follow-up failed.")
            speak("I couldn't open the Google search.")

    elif answer is False:
        speak("Alright.")

    else:
        speak("I didn't catch that. I'll leave it there.")


def assistant_loop():
    global WAKE_MODEL

    while not STOP.is_set():

        # ---------------------------------------------------------
        # STATE 1: WAITING FOR WAKE WORD
        # ---------------------------------------------------------

        if PAUSED.is_set():
            time.sleep(0.2)
            continue

        try:
            log.info("Waiting for wake word.")

            detected = wake_listener.listen_for_wake_word(
                STOP,
                PAUSED,
                model=WAKE_MODEL,
            )

            update_tray()

            if STOP.is_set():
                break

            if PAUSED.is_set():
                continue

            if not detected:
                continue

            # -----------------------------------------------------
            # STATE 2: WAKE WORD DETECTED
            # -----------------------------------------------------

            log.info("Wake word detected.")

            time.sleep(0.25)

            play_wake_sound()

            time.sleep(0.15)

            # -----------------------------------------------------
            # STATE 3: LISTEN FOR COMMAND
            # -----------------------------------------------------

            log.info("Listening for command.")

            command = stt.record_and_transcribe(
                config.COMMAND_RECORD_SECONDS
            )

            if not command:
                log.info("No command recognized.")
                speak("I didn't catch that.")
                time.sleep(0.5)
                continue

            log.info("Command recognized: %r", command)

            # -----------------------------------------------------
            # STATE 4: EXECUTE COMMAND
            # -----------------------------------------------------

            try:
                success = launch_from_command(command)

                log.info(
                    "Command routing result: success=%s | command=%r",
                    success,
                    command,
                )

                # Speak after every command.
                speak(command_response(command, success))

                # -------------------------------------------------
                # STATE 5: OPTIONAL FOLLOW-UP
                # -------------------------------------------------

                if success and should_offer_google_followup(command):
                    ask_google_followup(command)

            except Exception:
                log.exception(
                    "Launcher failed for command %r",
                    command,
                )
                speak("Something went wrong while executing that command.")

            # -----------------------------------------------------
            # STATE 6: COOLDOWN
            # -----------------------------------------------------

            time.sleep(0.6)

        except Exception:
            log.exception("Assistant cycle failed; continuing.")
            time.sleep(0.5)


def main():
    global WAKE_MODEL

    setup_logging()

    log.info("Jarvis starting.")
    log.info("Project directory: %s", config.BASE_DIR)

    setup_tts()

    log.info("Loading Vosk model.")
    stt.load_model()

    log.info("Loading wake-word model.")
    WAKE_MODEL = wake_listener.load_wake_model()

    tray = threading.Thread(
        target=tray_thread,
        name="JarvisTray",
        daemon=True,
    )

    tray.start()

    # Startup response.
    speak("JARVIS is online.")

    assistant_loop()

    log.info("Jarvis stopped.")


if __name__ == "__main__":
    main()
