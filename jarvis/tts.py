import logging
import threading
import pyttsx3

log = logging.getLogger("jarvis.tts")

_engine = None
_tts_lock = threading.Lock()


# ---------------------------------------------------------
# Male voice preference
# ---------------------------------------------------------

MALE_VOICE_KEYWORDS = (
    "david",
    "mark",
    "guy",
    "ryan",
    "george",
    "male",
    "james",
    "michael",
    "richard",
)

FEMALE_VOICE_KEYWORDS = (
    "zira",
    "hazel",
    "susan",
    "female",
    "heera",
)


def _create_engine():
    """Create and configure the Windows SAPI5 speech engine."""

    try:
        engine = pyttsx3.init("sapi5")

        # Faster response.
        engine.setProperty("rate", 185)

        # Full volume.
        engine.setProperty("volume", 1.0)

        voices = engine.getProperty("voices")

        selected_voice = None

        if voices:

            # -------------------------------------------------
            # First priority: known male voices
            # -------------------------------------------------

            for voice in voices:

                name = str(
                    getattr(voice, "name", "")
                ).lower()

                if any(
                    keyword in name
                    for keyword in MALE_VOICE_KEYWORDS
                ):
                    selected_voice = voice
                    break

            # -------------------------------------------------
            # Second priority: English voice that isn't
            # obviously female.
            # -------------------------------------------------

            if selected_voice is None:

                for voice in voices:

                    name = str(
                        getattr(voice, "name", "")
                    ).lower()

                    if (
                        "english" in name
                        and not any(
                            keyword in name
                            for keyword in FEMALE_VOICE_KEYWORDS
                        )
                    ):
                        selected_voice = voice
                        break

            # -------------------------------------------------
            # Last fallback
            # -------------------------------------------------

            if selected_voice is None:
                selected_voice = voices[0]

            engine.setProperty(
                "voice",
                selected_voice.id
            )

            log.info(
                "Selected TTS voice: %s",
                getattr(
                    selected_voice,
                    "name",
                    "Unknown"
                )
            )

        return engine

    except Exception:
        log.exception(
            "Could not initialize SAPI5 TTS."
        )

        return None


def speak(text: str):
    """
    Speak text using the persistent Windows SAPI5 engine.

    The engine stays alive so every response after the first
    one starts speaking much faster.
    """

    global _engine

    if not text:
        return

    text = str(text).strip()

    if not text:
        return

    with _tts_lock:

        try:

            if _engine is None:
                _engine = _create_engine()

            if _engine is None:
                log.error(
                    "TTS engine unavailable."
                )
                return

            log.info(
                "JARVIS: %s",
                text
            )

            _engine.say(text)
            _engine.runAndWait()

        except Exception:

            log.exception(
                "TTS failed. Reinitializing engine."
            )

            try:

                if _engine is not None:
                    _engine.stop()

            except Exception:
                pass

            _engine = None


def warm_up():
    """
    Initialize the TTS engine during startup.

    This prevents the first real response from having
    an initialization delay.
    """

    global _engine

    with _tts_lock:

        if _engine is None:
            _engine = _create_engine()
