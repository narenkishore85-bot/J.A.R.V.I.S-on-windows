import json
import logging
import threading
import time

import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer

import config


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
    # OPEN
    "open notepad",
    "open calculator",
    "open explorer",
    "open chrome",
    "open vs code",
    "open arduino",
    "open arduino ide",
    "open kicad",
    "open ki cad",
    "open instagram",
    "open claude",

    "launch notepad",
    "launch calculator",
    "launch explorer",
    "launch chrome",
    "launch vs code",
    "launch arduino",
    "launch arduino ide",
    "launch kicad",

    "start notepad",
    "start calculator",
    "start explorer",
    "start chrome",
    "start vs code",
    "start arduino",
    "start arduino ide",
    "start kicad",

    # CLOSE
    "close notepad",
    "close calculator",
    "close explorer",
    "close chrome",
    "close vs code",
    "close arduino",
    "close arduino ide",
    "close kicad",
    "close instagram",
    "close claude",

    "exit notepad",
    "exit calculator",
    "exit explorer",
    "exit chrome",
    "exit vs code",
    "exit arduino",
    "exit arduino ide",
    "exit kicad",

    "quit notepad",
    "quit calculator",
    "quit explorer",
    "quit chrome",
    "quit vs code",
    "quit arduino",
    "quit arduino ide",
    "quit kicad",

    # FILE SEARCH
    "find my resume",
    "find resume",
    "locate my resume",
    "locate resume",

    "find my notes",
    "find notes",
    "locate my notes",
    "locate notes",

    "find project report",
    "locate project report",
    "search for project report",

    "find assignment",
    "locate assignment",
    "search for assignment",

    "where is my resume",
    "where is my notes",
    "where is project report",

    # GOOGLE
    "search google",
    "search google for",
    "google search",
    "google search for",

    # CONFIRMATION
    "yes",
    "yeah",
    "yep",
    "sure",
    "okay",
    "ok",
    "no",
    "nope",
    "nah",

    "[unk]"
])
# ---------------------------------------------------------
# Record and transcribe
# ---------------------------------------------------------

def record_and_transcribe(seconds=5):
    """
    Record a short Jarvis command and transcribe it with Vosk.

    The function keeps the original interface:
        record_and_transcribe(seconds)

    It also stops early after speech followed by silence.
    """

    model = load_model()

    recognizer = KaldiRecognizer(
        model,
        config.SAMPLE_RATE,
        COMMAND_GRAMMAR
    )

    recognizer.SetWords(True)

    chunks = []

    # Audio settings
    blocksize = 400                 # 25 ms at 16 kHz
    silence_limit = 0.8             # seconds
    start_timeout = 2.0             # wait this long for speech

    # RMS threshold.
    # Your microphone was working, so start moderately low.
    speech_threshold = 180

    speech_started = False
    silence_time = 0.0
    waiting_time = 0.0

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

                data, overflowed = stream.read(blocksize)

                if overflowed:
                    log.warning("Microphone input overflow")

                audio = data[:, 0].copy()

                # Calculate RMS volume
                audio_float = audio.astype(np.float32)

                rms = float(
                    np.sqrt(
                        np.mean(
                            np.square(audio_float)
                        )
                    )
                )

                # Feed audio to Vosk
                recognizer.AcceptWaveform(audio.tobytes())

                chunks.append(audio)

                elapsed = time.monotonic() - start_time

                # -----------------------------------------
                # Detect beginning of speech
                # -----------------------------------------

                if rms >= speech_threshold:

                    if not speech_started:
                        print("Speech detected...")

                    speech_started = True
                    silence_time = 0.0

                else:

                    if speech_started:
                        silence_time += blocksize / config.SAMPLE_RATE
                    else:
                        waiting_time = elapsed

                # -----------------------------------------
                # Stop after speech + silence
                # -----------------------------------------

                if speech_started and silence_time >= silence_limit:
                    break

                # -----------------------------------------
                # Safety timeout
                # -----------------------------------------

                if elapsed >= seconds:
                    break

                if not speech_started and waiting_time >= start_timeout:
                    print("No speech detected.")
                    return ""

    except Exception as exc:
        log.exception("Microphone recording failed")
        print("Microphone error:", exc)
        return ""

    print("Processing...")

    # Get final Vosk result
    result = json.loads(recognizer.FinalResult())

    text = result.get("text", "").strip()

    text = clean_command(text)

    print("RESULT:", text)

    return text
