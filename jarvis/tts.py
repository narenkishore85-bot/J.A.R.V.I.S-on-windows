import logging
import pyttsx3

log = logging.getLogger("jarvis.tts")


def speak(text: str):
    """Speak a response using Windows SAPI."""
    try:
        engine = pyttsx3.init("sapi5")
        engine.setProperty("rate", 175)
        engine.setProperty("volume", 1.0)

        voices = engine.getProperty("voices")

        # Prefer a Windows English voice.
        if voices:
            for voice in voices:
                voice_name = str(getattr(voice, "name", "")).lower()
                if "english" in voice_name or "david" in voice_name or "zira" in voice_name:
                    engine.setProperty("voice", voice.id)
                    break

        engine.say(text)
        engine.runAndWait()
        engine.stop()

    except Exception:
        log.exception("TTS failed.")