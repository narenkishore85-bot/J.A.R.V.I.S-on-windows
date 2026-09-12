import logging
import time

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

from . import config


log = logging.getLogger("jarvis.wake_listener")


# ============================================================
# WAKE WORD SETTINGS
# ============================================================

WAKE_THRESHOLD = 0.90

# Number of strong consecutive detections required
REQUIRED_HITS = 3

# Very strong single-frame detection
PEAK_THRESHOLD = 0.97

# Maximum time allowed between detection hits
HIT_WINDOW_SECONDS = 1.0

# Ignore another wake detection for this long
WAKE_COOLDOWN_SECONDS = 1.5

# Ignore extremely quiet microphone frames
MIN_RMS = 180.0

# Flush old/stale microphone frames when starting
STARTUP_FLUSH_FRAMES = 8


# ============================================================
# MODEL
# ============================================================

_MODEL = None


def load_wake_model():
    """
    Load the OpenWakeWord model only once.
    """

    global _MODEL

    if _MODEL is not None:
        return _MODEL

    log.info("Loading wake-word model...")

    try:
        _MODEL = Model(
            wakeword_models=[str(config.JARVIS_WAKEWORD_MODEL)],
            inference_framework="onnx",
        )

        log.info(
            "Wake-word model loaded: %s",
            config.JARVIS_WAKEWORD_MODEL,
        )

        return _MODEL

    except Exception:
        log.exception("Failed to load wake-word model.")
        raise


# ============================================================
# DETECTION STATE
# ============================================================

def reset_detection_state():
    """
    Reset multi-hit detection state.
    """

    return 0, None, 0.0


# ============================================================
# AUDIO UTILITIES
# ============================================================

def calculate_rms(audio):
    """
    Calculate RMS level of an int16 audio frame.
    """

    if audio is None:
        return 0.0

    audio = np.asarray(audio)

    if audio.size == 0:
        return 0.0

    audio_float = audio.astype(np.float32)

    return float(
        np.sqrt(
            np.mean(
                np.square(audio_float)
            )
        )
    )


def flush_stream(stream, frame_samples):
    """
    Read and discard a few frames.

    This prevents stale audio from the previous command
    or microphone buffer from triggering the wake detector.
    """

    for _ in range(STARTUP_FLUSH_FRAMES):
        try:
            stream.read(frame_samples)
        except Exception:
            break


# ============================================================
# SAFE STREAM CREATION
# ============================================================

def create_input_stream(frame_samples):
    """
    Create and start the microphone input stream.

    Returns:
        sounddevice.InputStream
    """

    log.debug(
        "Opening microphone: device=%s samplerate=%s blocksize=%s",
        config.MIC_DEVICE,
        config.SAMPLE_RATE,
        frame_samples,
    )

    stream = sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
        device=config.MIC_DEVICE,
    )

    stream.start()

    log.debug("Microphone stream started.")

    return stream


# ============================================================
# WAKE LISTENER
# ============================================================

def listen_for_wake_word(
    stop_event=None,
    pause_event=None,
):
    """
    Continuously listen for the configured wake word.

    Returns:
        True  -> wake word detected
        False -> stopped/interrupted
    """

    model = load_wake_model()

    frame_samples = int(config.WAKE_FRAME_SAMPLES)

    consecutive_hits = 0
    first_hit_time = None
    peak_score = 0.0

    stream = None

    log.info("Wake-word listener started.")

    try:

        # ----------------------------------------------------
        # OPEN MICROPHONE
        # ----------------------------------------------------

        while not (stop_event and stop_event.is_set()):

            if pause_event and pause_event.is_set():
                time.sleep(0.05)
                continue

            try:
                stream = create_input_stream(frame_samples)

                # Remove stale microphone data
                flush_stream(
                    stream,
                    frame_samples,
                )

                log.info("Listening for wake word...")

                break

            except sd.PortAudioError:
                log.exception(
                    "Could not open microphone stream. Retrying..."
                )

                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass

                    try:
                        stream.close()
                    except Exception:
                        pass

                    stream = None

                time.sleep(0.5)

            except Exception:
                log.exception(
                    "Unexpected microphone initialization error."
                )

                if stream is not None:
                    try:
                        stream.stop()
                    except Exception:
                        pass

                    try:
                        stream.close()
                    except Exception:
                        pass

                    stream = None

                time.sleep(0.5)

        # ----------------------------------------------------
        # MAIN LISTENING LOOP
        # ----------------------------------------------------

        while not (stop_event and stop_event.is_set()):

            # -----------------------------------------------
            # PAUSE HANDLING
            # -----------------------------------------------

            if pause_event and pause_event.is_set():

                log.debug(
                    "Wake listener paused."
                )

                # Close microphone while paused
                if stream is not None:

                    try:
                        stream.stop()
                    except Exception:
                        pass

                    try:
                        stream.close()
                    except Exception:
                        pass

                    stream = None

                # Wait until resumed
                while (
                    pause_event.is_set()
                    and not (
                        stop_event
                        and stop_event.is_set()
                    )
                ):
                    time.sleep(0.05)

                if stop_event and stop_event.is_set():
                    break

                # Re-open microphone
                try:

                    stream = create_input_stream(
                        frame_samples
                    )

                    flush_stream(
                        stream,
                        frame_samples,
                    )

                    (
                        consecutive_hits,
                        first_hit_time,
                        peak_score,
                    ) = reset_detection_state()

                    log.debug(
                        "Wake listener resumed."
                    )

                except sd.PortAudioError:

                    log.exception(
                        "Could not reopen microphone after pause."
                    )

                    time.sleep(0.5)

                    continue

                except Exception:

                    log.exception(
                        "Unexpected error reopening microphone."
                    )

                    time.sleep(0.5)

                    continue

            # -----------------------------------------------
            # MAKE SURE STREAM EXISTS
            # -----------------------------------------------

            if stream is None:

                try:

                    stream = create_input_stream(
                        frame_samples
                    )

                    flush_stream(
                        stream,
                        frame_samples,
                    )

                except Exception:

                    log.exception(
                        "Failed to recreate microphone stream."
                    )

                    time.sleep(0.5)

                    continue

            # -----------------------------------------------
            # READ MICROPHONE FRAME
            # -----------------------------------------------

            try:

                audio, overflowed = stream.read(
                    frame_samples
                )

            except sd.PortAudioError:

                # This is the important protection against
                # the crash you encountered.
                log.exception(
                    "Microphone read failed. "
                    "Reinitializing audio stream..."
                )

                try:
                    stream.stop()
                except Exception:
                    pass

                try:
                    stream.close()
                except Exception:
                    pass

                stream = None

                (
                    consecutive_hits,
                    first_hit_time,
                    peak_score,
                ) = reset_detection_state()

                time.sleep(0.2)

                continue

            except Exception:

                log.exception(
                    "Unexpected microphone read error."
                )

                try:
                    stream.stop()
                except Exception:
                    pass

                try:
                    stream.close()
                except Exception:
                    pass

                stream = None

                (
                    consecutive_hits,
                    first_hit_time,
                    peak_score,
                ) = reset_detection_state()

                time.sleep(0.2)

                continue

            # -----------------------------------------------
            # HANDLE OVERFLOW
            # -----------------------------------------------

            if overflowed:

                log.warning(
                    "Microphone input overflow detected."
                )

                (
                    consecutive_hits,
                    first_hit_time,
                    peak_score,
                ) = reset_detection_state()

                continue

            # -----------------------------------------------
            # STOP CHECK
            # -----------------------------------------------

            if stop_event and stop_event.is_set():
                break

            # -----------------------------------------------
            # RMS CHECK
            # -----------------------------------------------

            rms = calculate_rms(audio)

            if rms < MIN_RMS:

                # Quiet audio is ignored.
                continue

            # -----------------------------------------------
            # PREPARE AUDIO FOR OPENWAKEWORD
            # -----------------------------------------------

            audio = np.asarray(
                audio,
                dtype=np.int16,
            ).flatten()

            # -----------------------------------------------
            # RUN WAKE-WORD MODEL
            # -----------------------------------------------

            try:

                prediction = model.predict(
                    audio
                )

            except Exception:

                log.exception(
                    "Wake-word model inference failed."
                )

                (
                    consecutive_hits,
                    first_hit_time,
                    peak_score,
                ) = reset_detection_state()

                continue

            # -----------------------------------------------
            # GET MODEL SCORE
            # -----------------------------------------------

            score = 0.0

            if isinstance(prediction, dict):

                # Normal OpenWakeWord case
                if config.JARVIS_WAKEWORD_MODEL in prediction:

                    score = float(
                        prediction[
                            config.JARVIS_WAKEWORD_MODEL
                        ]
                    )

                else:

                    # Fallback: use the highest score
                    # if the configured key differs.
                    try:

                        score = max(
                            float(value)
                            for value in prediction.values()
                        )

                    except Exception:

                        score = 0.0

            else:

                try:
                    score = float(prediction)
                except Exception:
                    score = 0.0

            # Keep score within sensible bounds
            score = max(
                0.0,
                min(
                    1.0,
                    score,
                ),
            )

            # -----------------------------------------------
            # TRACK PEAK SCORE
            # -----------------------------------------------

            if score > peak_score:
                peak_score = score

            now = time.monotonic()

            # -----------------------------------------------
            # STRONG SINGLE-FRAME DETECTION
            # -----------------------------------------------

            if score >= PEAK_THRESHOLD:

                log.info(
                    "Strong wake-word detection: %.3f",
                    score,
                )

                # Strong enough to accept immediately.
                consecutive_hits = REQUIRED_HITS
                first_hit_time = now

            # -----------------------------------------------
            # NORMAL MULTI-HIT DETECTION
            # -----------------------------------------------

            elif score >= WAKE_THRESHOLD:

                if first_hit_time is None:

                    first_hit_time = now
                    consecutive_hits = 1

                elif (
                    now - first_hit_time
                    <= HIT_WINDOW_SECONDS
                ):

                    consecutive_hits += 1

                else:

                    # Previous detection window expired.
                    first_hit_time = now
                    consecutive_hits = 1

            # -----------------------------------------------
            # SCORE BELOW THRESHOLD
            # -----------------------------------------------

            else:

                # If the detection window has expired,
                # clear the state.
                if (
                    first_hit_time is not None
                    and now - first_hit_time
                    > HIT_WINDOW_SECONDS
                ):

                    (
                        consecutive_hits,
                        first_hit_time,
                        peak_score,
                    ) = reset_detection_state()

                    continue

            # -----------------------------------------------
            # DEBUG LOGGING
            # -----------------------------------------------

            if score >= WAKE_THRESHOLD:

                log.debug(
                    "Wake score=%.3f hits=%d/%d rms=%.1f",
                    score,
                    consecutive_hits,
                    REQUIRED_HITS,
                    rms,
                )

            # -----------------------------------------------
            # CONFIRM WAKE WORD
            # -----------------------------------------------

            if (
                consecutive_hits >= REQUIRED_HITS
                or score >= PEAK_THRESHOLD
            ):

                log.info(
                    "Wake word detected! "
                    "score=%.3f peak=%.3f",
                    score,
                    peak_score,
                )

                # -------------------------------------------
                # RESET DETECTION STATE
                # -------------------------------------------

                (
                    consecutive_hits,
                    first_hit_time,
                    peak_score,
                ) = reset_detection_state()

                # -------------------------------------------
                # CLOSE MICROPHONE
                # -------------------------------------------

                if stream is not None:

                    try:
                        stream.stop()
                    except Exception:
                        pass

                    try:
                        stream.close()
                    except Exception:
                        pass

                    stream = None

                # -------------------------------------------
                # COOLDOWN
                # -------------------------------------------

                time.sleep(
                    WAKE_COOLDOWN_SECONDS
                )

                return True

        return False

    except KeyboardInterrupt:

        log.info(
            "Wake-word listener interrupted."
        )

        return False

    except Exception:

        log.exception(
            "Unexpected wake listener failure."
        )

        return False

    finally:

        # ====================================================
        # ALWAYS CLEAN UP MICROPHONE
        # ====================================================

        if stream is not None:

            try:
                stream.stop()
            except Exception:
                pass

            try:
                stream.close()
            except Exception:
                pass

            stream = None

        log.debug(
            "Wake-word microphone stream closed."
        )