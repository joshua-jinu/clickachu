"""Speech-to-text with faster-whisper, sounddevice, and adaptive auto-stop (PLAN §17)."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK_SIZE = 1024  # 1024 / 16000 = 64 ms (~15.6 Hz)
AUTO_STOP_SILENCE_S = 1.2
MAX_RECORDING_S = 30.0


class VoiceService:
    """Manages Whisper STT model lazy-loading and microphone recording."""

    def __init__(
        self,
        models_dir: Path,
        emit: Callable[[dict[str, Any]], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.models_dir = models_dir
        self.emit = emit
        self._clock = clock

        self._model: Any = None
        self._model_status = "unloaded"  # unloaded | loading | ready | error
        self._model_error: str | None = None
        self._load_lock = threading.Lock()

        self._recording = False
        self._record_lock = threading.Lock()
        self._audio_chunks: list[np.ndarray] = []
        self._stream: Any = None
        self._start_time: float = 0.0
        self._speech_detected = False
        self._silence_start: float | None = None
        self._ambient_rms = 0.01
        self._last_transcript = ""
        self._auto_stop_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def model_status(self) -> str:
        return self._model_status

    @property
    def is_recording(self) -> bool:
        return self._recording

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def load_model_async(self) -> None:
        """Start loading faster-whisper on a background thread."""
        with self._load_lock:
            if self._model_status in ("loading", "ready"):
                return
            self._model_status = "loading"
            self._model_error = None

        thread = threading.Thread(
            target=self._load_model_worker,
            name="whisper-loader",
            daemon=True,
        )
        thread.start()

    def _load_model_worker(self) -> None:
        try:
            self.models_dir.mkdir(parents=True, exist_ok=True)
            from faster_whisper import WhisperModel

            log.info("loading faster-whisper base.en int8 into %s...", self.models_dir)
            model = WhisperModel(
                "base.en",
                device="cpu",
                compute_type="int8",
                download_root=str(self.models_dir),
            )
            with self._load_lock:
                self._model = model
                self._model_status = "ready"
            log.info("faster-whisper base.en loaded successfully")
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to load faster-whisper: %s", exc)
            with self._load_lock:
                self._model_status = "error"
                self._model_error = str(exc)

    def get_input_devices(self) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        try:
            import sounddevice as sd

            all_devs = sd.query_devices()
            default_in = sd.default.device[0] if sd.default.device else None
            for idx, dev in enumerate(all_devs):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append({
                        "id": idx,
                        "name": dev.get("name", f"Microphone {idx}"),
                        "is_default": idx == default_in,
                    })
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to query sound devices: %s", exc)
        return devices

    def start_recording(self, device: int | str | None = None) -> bool:
        with self._record_lock:
            if self._recording:
                return True
            try:
                import sounddevice as sd
            except ImportError as exc:
                log.error("sounddevice not available: %s", exc)
                return False

            dev_arg = None
            if device is not None and str(device).strip():
                try:
                    dev_arg = int(device)
                except ValueError:
                    dev_arg = str(device)

            self._audio_chunks.clear()
            self._speech_detected = False
            self._silence_start = None
            self._ambient_rms = 0.01
            self._start_time = self._clock()
            self._recording = True
            self._last_transcript = ""

            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=BLOCK_SIZE,
                    device=dev_arg,
                    callback=self._audio_callback,
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                log.error("failed to open sounddevice stream: %s", exc)
                self._recording = False
                self._stream = None
                return False

            self.emit({"type": "orb.state", "state": "listening"})
            log.info("started voice recording on device %r", dev_arg)
            return True

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        if not self._recording:
            return

        chunk = indata[:, 0].copy()
        self._audio_chunks.append(chunk)

        # Compute RMS
        rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0
        # Emit level event ~15 Hz (since blocksize 1024 at 16 kHz is ~64 ms)
        try:
            self.emit({"type": "voice.level", "rms": min(1.0, rms * 5.0)})
        except Exception as exc:  # noqa: BLE001
            log.debug("error emitting voice level: %s", exc)

        now = self._clock()
        # Adaptive threshold
        if not self._speech_detected:
            # Baseline tracking
            self._ambient_rms = 0.9 * self._ambient_rms + 0.1 * rms
            threshold = max(0.018, self._ambient_rms * 2.2)
            if rms > threshold:
                self._speech_detected = True
                self._silence_start = None
        else:
            threshold = max(0.015, self._ambient_rms * 1.8)
            if rms < threshold:
                if self._silence_start is None:
                    self._silence_start = now
                elif (now - self._silence_start) >= AUTO_STOP_SILENCE_S:
                    # Auto-stop trigger!
                    self._trigger_auto_stop()
            else:
                self._silence_start = None

        if (now - self._start_time) >= MAX_RECORDING_S:
            self._trigger_auto_stop()

    def _trigger_auto_stop(self) -> None:
        with self._record_lock:
            if not self._recording:
                return
            self._recording = False

        def _do_stop() -> None:
            text = self._finalize_recording()
            try:
                self.emit({"type": "voice.done", "text": text})
            except Exception as exc:  # noqa: BLE001
                log.warning("error emitting voice.done: %s", exc)

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(_do_stop)
        else:
            threading.Thread(target=_do_stop, daemon=True).start()

    def stop_recording(self) -> str:
        """Stop recording and return transcribed text."""
        with self._record_lock:
            if not self._recording:
                return self._last_transcript
            self._recording = False

        return self._finalize_recording()

    def _finalize_recording(self) -> str:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("error closing audio stream: %s", exc)
            self._stream = None

        self.emit({"type": "orb.state", "state": "idle"})

        if not self._audio_chunks:
            self._last_transcript = ""
            return ""

        audio = np.concatenate(self._audio_chunks, axis=0)
        self._audio_chunks.clear()

        # Transcribe audio
        text = self.transcribe(audio)
        self._last_transcript = text
        log.info("transcription result: %r", text)
        return text

    def transcribe(self, audio: np.ndarray) -> str:
        # Wait up to 5 seconds if model is still loading
        for _ in range(50):
            with self._load_lock:
                if self._model is not None or self._model_status in ("error", "unloaded"):
                    break
            time.sleep(0.1)

        with self._load_lock:
            model = self._model

        if model is None:
            log.warning(
                "cannot transcribe: Whisper model not ready (status=%s)",
                self._model_status,
            )
            return ""

        try:
            segments, _info = model.transcribe(audio, language="en", beam_size=5)
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:  # noqa: BLE001
            log.error("whisper transcription failed: %s", exc)
            return ""
