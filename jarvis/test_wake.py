import sounddevice as sd
import numpy as np
from openwakeword.model import Model

MODEL_PATH = r"C:\Users\naren\AppData\Local\Programs\Python\Python312\Lib\site-packages\openwakeword\resources\models\hey_jarvis_v0.1.onnx"

SAMPLE_RATE = 16000
CHUNK = 1280
THRESHOLD = 0.5

model = Model(
    wakeword_models=[MODEL_PATH],
    inference_framework="onnx"
)

print()
print("===================================")
print("       JARVIS WAKE TEST")
print("===================================")
print("Model : Hey Jarvis")
print("Input : Microphone Array")
print("Rate  : 16000 Hz")
print("Chunk : 1280 samples")
print("Threshold:", THRESHOLD)
print()
print("Listening...")
print("Say: Hey Jarvis")
print("Press Ctrl+C to stop.")
print()


def audio_callback(indata, frames, time, status):
    if status:
        print("\nAudio status:", status)

    audio = indata[:, 0].copy()

    # Convert float32 [-1, 1] to int16
    audio = np.clip(audio * 32767, -32768, 32767).astype(np.int16)

    prediction = model.predict(audio)

    for name, score in prediction.items():
        print(
            f"\r{name}: {score:.3f}",
            end="",
            flush=True
        )

        if score >= THRESHOLD:
            print()
            print(f">>> HEY JARVIS DETECTED! Score: {score:.3f}")
            print()

            model.reset()


try:
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK,
        callback=audio_callback
    ):
        while True:
            sd.sleep(1000)

except KeyboardInterrupt:
    print("\n\nStopping...")

except Exception as e:
    print("\n\nERROR:", repr(e))