# Jarvis — Offline Windows Voice Assistant (No Picovoice)

This version removes Picovoice/Porcupine completely.

Pipeline:

**local wake-word model → short Vosk transcription → deterministic launcher**

The existing `jarvis_launcher.py` is preserved unchanged.

## Important: "Jarvis" model

openWakeWord is an offline/open-source wake-word framework and does not require
a Picovoice AccessKey. Its standard pretrained models do not automatically
include a custom "Jarvis" model.

You therefore have two choices:

### Choice A — test the pipeline first

Use one of openWakeWord's included pretrained models. This proves the
microphone → wake detector → Vosk → launcher pipeline on your PC.

The easiest way is to download the pretrained models:

```powershell
python -c "from openwakeword.utils import download_models; download_models()"
```

Then change `JARVIS_WAKEWORD_MODEL` in `config.py` to one of the downloaded
`.tflite` model paths.

This is only a pipeline test; the wake phrase will be the phrase represented
by that pretrained model, not necessarily "Jarvis".

### Choice B — use the actual "Jarvis" phrase

For the final Jarvis assistant, you need a compatible custom openWakeWord
model trained/fine-tuned for the phrase "Jarvis". Place the resulting local
model in this folder as:

```text
jarvis.tflite
```

or set:

```powershell
[Environment]::SetEnvironmentVariable(
  "JARVIS_WAKEWORD_MODEL",
  "C:\path\to\jarvis.tflite",
  "User"
)
```

Restart PowerShell after changing the environment variable.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Vosk model

Download and extract a small English Vosk model into:

```text
vosk-model-small-en-us
```

or set `VOSK_MODEL_PATH` to the extracted folder.

## Test

```powershell
python jarvis_main.py
```

The program:

1. Loads the local wake-word model.
2. Loads Vosk once.
3. Starts the tray icon.
4. Listens locally.
5. On wake detection, plays a short sound.
6. Records approximately 3 seconds.
7. Sends the transcript to the existing deterministic launcher.
8. Returns to wake-word listening.

## Commands

Examples:

```text
open chrome
open notepad
open resume
locate resume
find my notes
```

## Task Scheduler

After manual testing works:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_task_scheduler.ps1
```

Then:

```text
Win + R
taskschd.msc
```

Find **Jarvis Voice Assistant**.

No Startup-folder entry is used.

## Privacy

The runtime does not need a cloud speech API. Wake-word inference and Vosk
transcription run locally after the required model files are installed.
