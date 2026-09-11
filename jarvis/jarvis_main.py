"""Entry point for the lightweight offline Windows Jarvis assistant."""

import logging
from logging.handlers import RotatingFileHandler
import subprocess
import threading
import time
import winsound

import pystray
from PIL import Image, ImageDraw

import config
import stt
import wake_listener
from jarvis_launcher import launch_from_command


STOP = threading.Event()
PAUSED = threading.Event()
TRAY = None
WAKE_MODEL = None


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

    else:

        PAUSED.set()

        log.info(
            "Listening paused; microphone capture will be released."
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

        subprocess.Popen(
            [
                "explorer",
                "/select,",
                str(config.LOG_PATH),
            ]
        )

    except Exception:

        log.exception(
            "Could not open log file."
        )


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
            # Wake listener has RETURNED and its microphone stream
            # has been closed.
            # -----------------------------------------------------

            log.info("Wake word detected.")

            # Allow the microphone/audio buffer to settle.
            time.sleep(0.25)

            # -----------------------------------------------------
            # STATE 3: PLAY WAKE CONFIRMATION
            # This is synchronous, so Vosk will not hear the sound.
            # -----------------------------------------------------

            play_wake_sound()

            # Small transition delay before command recording.
            time.sleep(0.15)

            # -----------------------------------------------------
            # STATE 4: LISTEN FOR COMMAND
            # Wake-word detection is NOT running here.
            # -----------------------------------------------------

            log.info("Listening for command.")

            command = stt.record_and_transcribe(
                config.COMMAND_RECORD_SECONDS
            )

            if not command:

                log.info("No command recognized.")

                # Prevent immediate re-trigger.
                time.sleep(0.5)

                continue

            log.info(
                "Command recognized: %r",
                command,
            )

            # -----------------------------------------------------
            # STATE 5: EXECUTE COMMAND
            # -----------------------------------------------------

            try:

                success = launch_from_command(command)

                log.info(
                    "Command routing result: success=%s | command=%r",
                    success,
                    command,
                )

            except Exception:

                log.exception(
                    "Launcher failed for command %r",
                    command,
                )

            # -----------------------------------------------------
            # STATE 6: COOLDOWN
            # Prevent the end of the spoken command from immediately
            # triggering another wake-word detection.
            # -----------------------------------------------------

            time.sleep(0.6)

        except Exception:

            log.exception(
                "Assistant cycle failed; continuing."
            )

            time.sleep(0.5)


def main():

    global WAKE_MODEL

    setup_logging()

    log.info("Jarvis starting.")

    log.info(
        "Project directory: %s",
        config.BASE_DIR,
    )

    # -------------------------------------------------------------
    # Load models once.
    # -------------------------------------------------------------

    log.info("Loading Vosk model.")

    stt.load_model()

    log.info("Loading wake-word model.")

    WAKE_MODEL = wake_listener.load_wake_model()

    # -------------------------------------------------------------
    # Start system tray.
    # -------------------------------------------------------------

    tray = threading.Thread(
        target=tray_thread,
        name="JarvisTray",
        daemon=True,
    )

    tray.start()

    # -------------------------------------------------------------
    # Start assistant.
    # -------------------------------------------------------------

    assistant_loop()

    log.info("Jarvis stopped.")


if __name__ == "__main__":
    main()
