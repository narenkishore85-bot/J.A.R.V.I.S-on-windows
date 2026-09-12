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

# Keep this reasonably sensitive so "Hey Jarvis" is easy to say.
WAKE_THRESHOLD = 0.90

# Number of strong detections required within the time window.
REQUIRED_HITS = 3

# A single spike at this level is NOT enough anymore.
# It is only used as a very strong supporting score.
PEAK_THRESHOLD = 0.985

# Strong detections must happen close together.
HIT_WINDOW_SECONDS = 0.8

# Ignore another wake detection for this long.
WAKE_COOLDOWN_SECONDS = 1.5

# Ignore extremely quiet microphone frames.
MIN_RMS = 180.0

# Flush old/stale microphone frames when starting.
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
    Reset wake-word detection state.
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

    Wake confirmation requires multiple strong detections
    close together in time.

    Returns:
        True  -> wake word detected
        False -> stopped/interrupted
    """

    model = load_wake_model()

    frame_samples = int(config.WAKE_FRAME_SAMPLES)

    # --------------------------------------------------------
    # Detection state
    # --------------------------------------------------------

    consecutive_hits = 0
    first_hit_time = None
    last_hit_time = None
    peak_score = 0.0

    stream = None

    log.info("Wake-word listener started.")

    try:

        # ====================================================
        # OPEN MICROPHONE
        # ====================================================

        while not (
            stop_event
            and stop_event.is_set()
        ):

            if (
                pause_event
                and pause_event.is_set()
            ):
                time.sleep(0.05)
                continue

            try:

                stream = create_input_stream(
                    frame_samples
                )

                # Remove stale microphone data.
                flush_stream(
                    stream,
                    frame_samples,
                )

                log.info(
                    "Listening for wake word..."
                )

                break

            except sd.PortAudioError:

                log.exception(
                    "Could not open microphone stream. "
                    "Retrying..."
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

        # ====================================================
        # MAIN LISTENING LOOP
        # ====================================================

        while not (
            stop_event
            and stop_event.is_set()
        ):

            # =================================================
            # PAUSE HANDLING
            # =================================================

            if (
                pause_event
                and pause_event.is_set()
            ):

                log.debug(
                    "Wake listener paused."
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

                # Wait until resumed.
                while (
                    pause_event.is_set()
                    and not (
                        stop_event
                        and stop_event.is_set()
                    )
                ):
                    time.sleep(0.05)

                if (
                    stop_event
                    and stop_event.is_set()
                ):
                    break

                # Re-open microphone.
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
                        last_hit_time,
                        peak_score,
                    ) = (
                        0,
                        None,
                        None,
                        0.0,
                    )

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

            # =================================================
            # MAKE SURE STREAM EXISTS
            # =================================================

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

            # =================================================
            # READ MICROPHONE FRAME
            # =================================================

            try:

                audio, overflowed = stream.read(
                    frame_samples
                )

            except sd.PortAudioError:

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
                    last_hit_time,
                    peak_score,
                ) = (
                    0,
                    None,
                    None,
                    0.0,
                )

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
                    last_hit_time,
                    peak_score,
                ) = (
                    0,
                    None,
                    None,
                    0.0,
                )

                time.sleep(0.2)

                continue

            # =================================================
            # HANDLE OVERFLOW
            # =================================================

            if overflowed:

                log.warning(
                    "Microphone input overflow detected."
                )

                (
                    consecutive_hits,
                    first_hit_time,
                    last_hit_time,
                    peak_score,
                ) = (
                    0,
                    None,
                    None,
                    0.0,
                )

                continue

            # =================================================
            # STOP CHECK
            # =================================================

            if (
                stop_event
                and stop_event.is_set()
            ):
                break

            # =================================================
            # RMS CHECK
            # =================================================

            rms = calculate_rms(audio)

            if rms < MIN_RMS:
                continue

            # =================================================
            # PREPARE AUDIO
            # =================================================

            audio = np.asarray(
                audio,
                dtype=np.int16,
            ).flatten()

            # =================================================
            # RUN OPENWAKEWORD
            # =================================================

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
                    last_hit_time,
                    peak_score,
                ) = (
                    0,
                    None,
                    None,
                    0.0,
                )

                continue

            # =================================================
            # GET MODEL SCORE
            # =================================================

            score = 0.0

            if isinstance(
                prediction,
                dict,
            ):

                # Normal OpenWakeWord case.
                model_key = str(
                    config.JARVIS_WAKEWORD_MODEL
                )

                if model_key in prediction:

                    score = float(
                        prediction[
                            model_key
                        ]
                    )

                else:

                    # Fallback: use highest score.
                    try:

                        score = max(
                            float(value)
                            for value
                            in prediction.values()
                        )

                    except Exception:

                        score = 0.0

            else:

                try:
                    score = float(
                        prediction
                    )

                except Exception:
                    score = 0.0

            # Keep score within sensible bounds.
            score = max(
                0.0,
                min(
                    1.0,
                    score,
                ),
            )

            now = time.monotonic()

            # =================================================
            # EXPIRED DETECTION WINDOW
            # =================================================

            if (
                first_hit_time is not None
                and (
                    now - first_hit_time
                    > HIT_WINDOW_SECONDS
                )
            ):

                log.debug(
                    "Wake detection window expired. "
                    "Resetting."
                )

                (
                    consecutive_hits,
                    first_hit_time,
                    last_hit_time,
                    peak_score,
                ) = (
                    0,
                    None,
                    None,
                    0.0,
                )

            # =================================================
            # STRONG SCORE
            # =================================================

            if score >= WAKE_THRESHOLD:

                # ------------------------------------------------
                # First strong hit
                # ------------------------------------------------

                if first_hit_time is None:

                    first_hit_time = now
                    last_hit_time = now
                    consecutive_hits = 1
                    peak_score = score

                    log.debug(
                        "Wake candidate started: "
                        "score=%.3f hits=%d/%d rms=%.1f",
                        score,
                        consecutive_hits,
                        REQUIRED_HITS,
                        rms,
                    )

                # ------------------------------------------------
                # Another strong hit inside the window
                # ------------------------------------------------

                elif (
                    last_hit_time is not None
                    and (
                        now - last_hit_time
                        <= HIT_WINDOW_SECONDS
                    )
                ):

                    consecutive_hits += 1
                    last_hit_time = now

                    if score > peak_score:
                        peak_score = score

                    log.debug(
                        "Wake candidate hit: "
                        "score=%.3f hits=%d/%d peak=%.3f rms=%.1f",
                        score,
                        consecutive_hits,
                        REQUIRED_HITS,
                        peak_score,
                        rms,
                    )

                # ------------------------------------------------
                # Gap was too long
                # ------------------------------------------------

                else:

                    first_hit_time = now
                    last_hit_time = now
                    consecutive_hits = 1
                    peak_score = score

                    log.debug(
                        "Wake candidate restarted: "
                        "score=%.3f rms=%.1f",
                        score,
                        rms,
                    )

            # =================================================
            # SCORE BELOW THRESHOLD
            # =================================================

            else:

                # ------------------------------------------------
                # Important:
                #
                # We do NOT immediately erase the candidate.
                #
                # A genuine spoken wake word can contain frames
                # where the score temporarily drops.
                #
                # However, if the window expires, the candidate
                # is discarded.
                # ------------------------------------------------

                if (
                    first_hit_time is not None
                    and (
                        now - first_hit_time
                        > HIT_WINDOW_SECONDS
                    )
                ):

                    (
                        consecutive_hits,
                        first_hit_time,
                        last_hit_time,
                        peak_score,
                    ) = (
                        0,
                        None,
                        None,
                        0.0,
                    )

            # =================================================
            # PEAK SCORE INFORMATION
            # =================================================

            if score >= PEAK_THRESHOLD:

                log.debug(
                    "Very strong wake score observed: %.3f",
                    score,
                )

            # =================================================
            # FINAL WAKE CONFIRMATION
            # =================================================

            # IMPORTANT:
            #
            # A single 0.97 / 0.98 / 0.99 spike is NOT enough.
            #
            # We require REQUIRED_HITS strong detections.
            #

            if (
                consecutive_hits
                >= REQUIRED_HITS
            ):

                log.info(
                    "Wake word confirmed! "
                    "score=%.3f peak=%.3f hits=%d/%d",
                    score,
                    peak_score,
                    consecutive_hits,
                    REQUIRED_HITS,
                )

                # Reset state.
                (
                    consecutive_hits,
                    first_hit_time,
                    last_hit_time,
                    peak_score,
                ) = (
                    0,
                    None,
                    None,
                    0.0,
                )

                # =================================================
                # CLOSE MICROPHONE
                # =================================================

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

                # =================================================
                # COOLDOWN
                # =================================================

                time.sleep(
                    WAKE_COOLDOWN_SECONDS
                )

                return True

        return False

    # ========================================================
    # KEYBOARD INTERRUPT
    # ========================================================

    except KeyboardInterrupt:

        log.info(
            "Wake-word listener interrupted."
        )

        return False

    # ========================================================
    # UNEXPECTED ERROR
    # ========================================================

    except Exception:

        log.exception(
            "Unexpected wake listener failure."
        )

        return False

    # ========================================================
    # ALWAYS CLEAN UP
    # ========================================================

    finally:

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
