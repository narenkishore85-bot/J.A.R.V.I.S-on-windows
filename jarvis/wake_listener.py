"""
Offline wake-word listener using openWakeWord + sounddevice.

Designed to reduce false wake-word detections by requiring
multiple strong detections instead of triggering from a single
audio frame.
"""

from __future__ import annotations

import time

import numpy as np
from openwakeword.model import Model
import sounddevice as sd

import config


# ============================================================
# WAKE DETECTION SETTINGS
# ============================================================

# Your current sensitivity.
WAKE_THRESHOLD = 0.90

# Number of strong consecutive detections required.
#
# 2 = more responsive
# 3 = safer against false triggers
#
# Start with 3.
REQUIRED_HITS = 3

# Maximum allowed time between qualifying detections.
#
# If the next strong frame doesn't arrive within this time,
# the detection sequence is reset.
HIT_WINDOW_SECONDS = 1.0

# After a successful wake detection, don't immediately
# trigger again from the same audio.
WAKE_COOLDOWN_SECONDS = 1.5


# ============================================================
# LOAD MODEL
# ============================================================

def load_wake_model():
    """Load the configured openWakeWord model."""

    model_path = str(
        config.JARVIS_WAKEWORD_MODEL
    )

    if not config.JARVIS_WAKEWORD_MODEL.exists():

        raise FileNotFoundError(
            f"Wake-word model not found:\n"
            f"{model_path}"
        )

    print(
        "Loading wake-word model..."
    )

    model = Model(
        wakeword_models=[model_path],
        inference_framework="onnx",
    )

    print(
        "Wake-word model loaded."
    )

    return model


# ============================================================
# WAKE LISTENER
# ============================================================

def listen_for_wake_word(
    *args,
    model=None,
    stop_event=None,
    pause_event=None,
    **kwargs,
):
    """
    Listen continuously for "Hey Jarvis".

    The function requires multiple strong wake-word
    detections before returning True.

    This reduces false activations caused by:
        - background speech
        - keyboard noise
        - TV/audio
        - random microphone noise
        - a single high model score
    """

    # --------------------------------------------------------
    # Backward-compatible argument handling
    # --------------------------------------------------------

    if args:

        if model is None:
            model = args[0]

        if (
            len(args) > 1
            and stop_event is None
        ):
            stop_event = args[1]

        if (
            len(args) > 2
            and pause_event is None
        ):
            pause_event = args[2]

    if stop_event is None:

        stop_event = kwargs.get(
            "stop_event"
        )

    if pause_event is None:

        pause_event = kwargs.get(
            "pause_event"
        )

    if model is None:

        model = kwargs.get(
            "wake_model"
        )

    if model is None:

        model = load_wake_model()

    frame_samples = (
        config.WAKE_FRAME_SAMPLES
    )

    print(
        "Listening for 'Hey Jarvis'..."
    )

    stream = sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
        device=config.MIC_DEVICE,
    )

    stream.start()

    # --------------------------------------------------------
    # Detection state
    # --------------------------------------------------------

    consecutive_hits = 0
    first_hit_time = None

    try:

        while True:

            # =================================================
            # STOP
            # =================================================

            if (
                stop_event is not None
                and stop_event.is_set()
            ):

                return False

            # =================================================
            # PAUSE
            # =================================================

            if (
                pause_event is not None
                and pause_event.is_set()
            ):

                print(
                    "Microphone paused."
                )

                try:
                    stream.stop()
                except Exception:
                    pass

                try:
                    stream.close()
                except Exception:
                    pass

                while pause_event.is_set():

                    if (
                        stop_event is not None
                        and stop_event.is_set()
                    ):

                        return False

                    time.sleep(
                        0.1
                    )

                print(
                    "Microphone resumed."
                )

                stream = sd.InputStream(
                    samplerate=config.SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=frame_samples,
                    device=config.MIC_DEVICE,
                )

                stream.start()

                consecutive_hits = 0
                first_hit_time = None

            # =================================================
            # READ AUDIO
            # =================================================

            audio, overflowed = (
                stream.read(
                    frame_samples
                )
            )

            if overflowed:

                consecutive_hits = 0
                first_hit_time = None

                continue

            pcm = np.asarray(
                audio[:, 0],
                dtype=np.int16,
            )

            # =================================================
            # MODEL PREDICTION
            # =================================================

            predictions = model.predict(
                pcm
            )

            score = predictions.get(
                "hey_jarvis_v0.1",
                0.0,
            )

            now = time.monotonic()

            # =================================================
            # STRONG DETECTION
            # =================================================

            if score >= WAKE_THRESHOLD:

                # Start a new detection sequence.
                if (
                    first_hit_time is None
                    or (
                        now - first_hit_time
                        > HIT_WINDOW_SECONDS
                    )
                ):

                    first_hit_time = now
                    consecutive_hits = 1

                else:

                    consecutive_hits += 1

                print(
                    f"Wake candidate: "
                    f"{score:.3f} "
                    f""
                    f"({consecutive_hits}/"
                    f"{REQUIRED_HITS})"
                )

                # =================================================
                # CONFIRMED WAKE WORD
                # =================================================

                if (
                    consecutive_hits
                    >= REQUIRED_HITS
                ):

                    print(
                        f"Wake word detected! "
                        f"Confidence: "
                        f"{score:.3f}"
                    )

                    # Reset detector state.
                    consecutive_hits = 0
                    first_hit_time = None

                    # Prevent immediate retriggering from
                    # the same spoken phrase.
                    time.sleep(
                        WAKE_COOLDOWN_SECONDS
                    )

                    return True

            # =================================================
            # SCORE BELOW THRESHOLD
            # =================================================

            else:

                # Don't immediately reset everything.
                #
                # A wake word naturally contains frames with
                # varying confidence, so allow the sequence
                # to survive briefly.
                if (
                    first_hit_time is not None
                    and (
                        now - first_hit_time
                        > HIT_WINDOW_SECONDS
                    )
                ):

                    consecutive_hits = 0
                    first_hit_time = None

    finally:

        try:
            stream.stop()
        except Exception:
            pass

        try:
            stream.close()
        except Exception:
            pass