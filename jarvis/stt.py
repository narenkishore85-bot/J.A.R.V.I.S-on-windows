import json
import logging
import threading
import time

import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer

from . import config


log = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


# ---------------------------------------------------------
# Load Vosk once
# ---------------------------------------------------------

def load_model():
    global _model

    with _model_lock:
        if _model is None:
            log.info("Loading Vosk model: %s", config.VOSK_MODEL_PATH)
            _model = Model(str(config.VOSK_MODEL_PATH))
            log.info("Vosk model loaded")

    return _model


# ---------------------------------------------------------
# Clean common recognition mistakes
# ---------------------------------------------------------

def clean_command(text):
    text = text.lower().strip()

    # Optional assistant name inside an active session.
    # "jarvis open arduino" -> "open arduino"
    # "hey jarvis open arduino" -> "open arduino"
    for prefix in (
        "hey jarvis ",
        "hey jarvis",
        "jarvis ",
        "jarvis",
    ):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    replacements = {
        "burn calculator": "open calculator",
        "when calculator": "open calculator",
        "open calculated": "open calculator",
        "been calculator": "open calculator",
        "on calculator": "open calculator",

        "when chrome": "open chrome",
        "burn chrome": "open chrome",

        "open notepad": "open notepad",
        "when notepad": "open notepad",
        "burn notepad": "open notepad",

        "open explorer": "open explorer",
        "when explorer": "open explorer",

        "find my resume": "find my resume",
        "locate my resume": "locate my resume",
    }

    if text in replacements:
        return replacements[text]

    return text


# ---------------------------------------------------------
# Command grammar
#
# This is intentionally small because Jarvis is deterministic.
# ---------------------------------------------------------

COMMAND_GRAMMAR = json.dumps([
    # =========================================================
    # OPEN APPLICATIONS
    # =========================================================

    "open notepad",
    "launch notepad",
    "start notepad",

    "open calculator",
    "launch calculator",
    "start calculator",

    "open explorer",
    "open file explorer",
    "launch explorer",
    "launch file explorer",

    "open chrome",
    "open google chrome",
    "launch chrome",
    "launch google chrome",

    "open vs code",
    "open visual studio code",
    "launch vs code",
    "launch visual studio code",

    "open arduino",
    "open arduino ide",
    "launch arduino",
    "launch arduino ide",

    "open kicad",
    "open ki cad",
    "launch kicad",
    "launch ki cad",

    "open proteus",
    "launch proteus",

    "open keil",
    "open keil uvision",
    "launch keil",
    "launch keil uvision",

    "open blender",
    "launch blender",

    "open vlc",
    "open vlc media player",
    "launch vlc",

    "open davinci",
    "open davinci resolve",
    "launch davinci",
    "launch davinci resolve",

    "open claude",
    "launch claude",

    "open instagram",
    "launch instagram",

    "open spotify",
    "launch spotify",

    "open whatsapp",
    "launch whatsapp",

    "open microsoft store",
    "open store",
    "launch microsoft store",
    "launch store",

    "open terminal",
    "launch terminal",

    "open command prompt",
    "open cmd",
    "launch command prompt",
    "launch cmd",

    "open powershell",
    "launch powershell",

    "open settings",
    "launch settings",

    "open control panel",
    "launch control panel",

    "open word",
    "launch word",

    "open excel",
    "launch excel",

    "open powerpoint",
    "launch powerpoint",

    # =========================================================
    # CLOSE APPLICATIONS
    # =========================================================

    "close notepad",
    "exit notepad",
    "quit notepad",

    "close calculator",
    "exit calculator",
    "quit calculator",

    "close explorer",
    "close file explorer",
    "exit explorer",
    "exit file explorer",

    "close chrome",
    "exit chrome",
    "quit chrome",

    "close vs code",
    "close visual studio code",
    "exit vs code",
    "exit visual studio code",

    "close arduino",
    "close arduino ide",
    "exit arduino",
    "exit arduino ide",

    "close kicad",
    "close ki cad",
    "exit kicad",
    "exit ki cad",

    "close proteus",
    "exit proteus",

    "close keil",
    "exit keil",

    "close blender",
    "exit blender",

    "close vlc",
    "exit vlc",

    "close davinci",
    "close davinci resolve",
    "exit davinci",
    "exit davinci resolve",

    "close claude",
    "exit claude",

    "close instagram",
    "exit instagram",

    "close spotify",
    "exit spotify",

    "close whatsapp",
    "exit whatsapp",

    "close microsoft store",
    "close store",
    "exit microsoft store",
    "exit store",

    "close terminal",
    "exit terminal",

    "close command prompt",
    "close cmd",
    "exit command prompt",
    "exit cmd",

    "close powershell",
    "exit powershell",

    "close settings",
    "exit settings",

    "close control panel",
    "exit control panel",

    "close word",
    "exit word",

    "close excel",
    "exit excel",

    "close powerpoint",
    "exit powerpoint",

    # =========================================================
    # FILE SEARCH
    # =========================================================

    "find my resume",
    "find resume",
    "locate my resume",
    "locate resume",
    "where is my resume",

    "find my notes",
    "find notes",
    "locate my notes",
    "locate notes",
    "where is my notes",

    "find project report",
    "locate project report",
    "search for project report",
    "where is project report",

    "find assignment",
    "locate assignment",
    "search for assignment",

    # =========================================================
    # WEB SEARCH
    # =========================================================

    "search google",
    "search google for",
    "google search",

    # =========================================================
    # OPTIONAL JARVIS PREFIX
    # =========================================================

    "jarvis open arduino",
    "jarvis open kicad",
    "jarvis open vs code",
    "jarvis open chrome",
    "jarvis open notepad",
    "jarvis open calculator",
    "jarvis close arduino",
    "jarvis close kicad",
    "jarvis close vs code",
    "jarvis close chrome",

    "[unk]"
])


# ---------------------------------------------------------
# Record and transcribe
# ---------------------------------------------------------

def record_and_transcribe(seconds=8):
    """
    Record a Jarvis command and transcribe it with Vosk.

    Timing behavior:

        Up to 5 seconds to START speaking.

        Once speech starts:
            wait for 1.2 seconds of silence.

        Maximum recording time:
            8 seconds.

    The original function interface remains compatible:

        record_and_transcribe(seconds)
    """

    model = load_model()

    recognizer = KaldiRecognizer(
        model,
        config.SAMPLE_RATE,
        COMMAND_GRAMMAR
    )

    recognizer.SetWords(True)

    # ---------------------------------------------------------
    # Audio settings
    # ---------------------------------------------------------

    blocksize = 400                  # 25 ms @ 16 kHz

    # Time allowed before the user starts speaking.
    #
    # Increased from 2.0 seconds.
    #
    # This allows:
    #
    # Hey Jarvis
    #     ↓
    # short pause
    #     ↓
    # open chrome
    #
    start_timeout = 5.0

    # Once speech has started, wait this long for silence
    # before considering the command complete.
    #
    # Increased from 0.8 seconds.
    silence_limit = 1.2

    # RMS threshold used to detect actual speech.
    #
    # Keep this moderate because the Realtek microphone
    # was already working correctly with this value.
    speech_threshold = 180

    speech_started = False
    silence_time = 0.0

    print("Listening for command...")

    try:

        with sd.InputStream(
            device=config.MIC_DEVICE,
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=blocksize,
        ) as stream:

            start_time = time.monotonic()

            while True:

                # -------------------------------------------------
                # Read 25 ms of microphone audio
                # -------------------------------------------------

                data, overflowed = stream.read(blocksize)

                if overflowed:
                    log.warning("Microphone input overflow")

                audio = data[:, 0].copy()

                # -------------------------------------------------
                # Calculate RMS volume
                # -------------------------------------------------

                audio_float = audio.astype(np.float32)

                rms = float(
                    np.sqrt(
                        np.mean(
                            np.square(audio_float)
                        )
                    )
                )

                # -------------------------------------------------
                # ALWAYS feed audio to Vosk
                # -------------------------------------------------

                recognizer.AcceptWaveform(
                    audio.tobytes()
                )

                elapsed = (
                    time.monotonic()
                    - start_time
                )

                # -------------------------------------------------
                # Detect speech
                # -------------------------------------------------

                if rms >= speech_threshold:

                    if not speech_started:
                        print(
                            f"Speech detected "
                            f"(RMS: {rms:.0f})"
                        )

                    speech_started = True

                    # User is still speaking.
                    silence_time = 0.0

                else:

                    if speech_started:

                        # 25 ms of silence.
                        silence_time += (
                            blocksize
                            / config.SAMPLE_RATE
                        )

                # -------------------------------------------------
                # Command finished
                #
                # Only stop AFTER speech has started.
                # -------------------------------------------------

                if (
                    speech_started
                    and silence_time >= silence_limit
                ):
                    break

                # -------------------------------------------------
                # Maximum recording duration
                # -------------------------------------------------

                if elapsed >= seconds:
                    break

                # -------------------------------------------------
                # No speech yet
                #
                # Give the user 5 seconds to begin.
                # -------------------------------------------------

                if (
                    not speech_started
                    and elapsed >= start_timeout
                ):

                    print(
                        "No speech detected."
                    )

                    return ""

    except Exception as exc:

        log.exception(
            "Microphone recording failed"
        )

        print(
            "Microphone error:",
            exc
        )

        return ""

    # ---------------------------------------------------------
    # Final Vosk processing
    # ---------------------------------------------------------

    print("Processing...")

    result = json.loads(
        recognizer.FinalResult()
    )

    text = result.get(
        "text",
        ""
    ).strip()

    # ---------------------------------------------------------
    # Clean recognized command
    # ---------------------------------------------------------

    text = clean_command(text)

    print(
        "RESULT:",
        text
    )

    return text
