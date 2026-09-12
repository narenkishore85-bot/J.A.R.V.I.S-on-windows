"""Central configuration for the offline Windows Jarvis assistant."""

from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent


# =========================================================
# Wake word
# =========================================================

WAKE_WORD_SENSITIVITY = 0.90

JARVIS_WAKEWORD_MODEL = Path(
    os.environ.get(
        "JARVIS_WAKEWORD_MODEL",
        str(
            Path.home()
            / "AppData"
            / "Local"
            / "Programs"
            / "Python"
            / "Python312"
            / "Lib"
            / "site-packages"
            / "openwakeword"
            / "resources"
            / "models"
            / "hey_jarvis_v0.1.onnx"
        ),
    )
)

WAKE_FRAME_SAMPLES = 1280


# =========================================================
# Speech-to-text
# =========================================================

SAMPLE_RATE = 16000
STT_CHANNELS = 1
STT_BLOCKSIZE = 800

MIC_DEVICE = 1

COMMAND_RECORD_SECONDS = 5


# =========================================================
# Vosk model
# =========================================================

VOSK_MODEL_PATH = Path(
    os.environ.get(
        "VOSK_MODEL_PATH",
        str(BASE_DIR / "vosk-model-small-en-us"),
    )
)


# =========================================================
# Wake confirmation sound
# =========================================================

WAKE_SOUND_PATH = Path(
    os.environ.get(
        "WAKE_SOUND_PATH",
        str(BASE_DIR / "wake.wav"),
    )
)


# =========================================================
# Logging
# =========================================================

LOG_PATH = Path(
    os.environ.get(
        "JARVIS_LOG_PATH",
        str(BASE_DIR / "logs" / "jarvis.log"),
    )
)

LOG_MAX_BYTES = 1 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# ---------------------------------------------------------
# ACTIVE JARVIS SESSION
# ---------------------------------------------------------

# After wake-word detection, Jarvis remains active for this
# many seconds without requiring the wake word again.

COMMAND_SESSION_SECONDS = 15