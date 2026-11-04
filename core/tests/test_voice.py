"""Tests for Voice STT, RMS events, adaptive threshold auto-stop, and voice API routes."""

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from clickachu.api.http import AppConfig, create_app
from clickachu.db.conn import Database
from clickachu.settings import SettingsStore
from clickachu.voice.stt import AUTO_STOP_SILENCE_S, VoiceService


class MockWhisperModel:
    def __init__(self, transcript: str = "find my latest resume and open it") -> None:
        self.transcript = transcript

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[list[Any], Any]:
        mock_seg = MagicMock()
        mock_seg.text = self.transcript
        return [mock_seg], None


def test_voice_service_transcribe_mock(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    service = VoiceService(models_dir=tmp_path, emit=events.append)
    service._model = MockWhisperModel("test voice output")
    service._model_status = "ready"

    fake_audio = np.zeros(16000, dtype=np.float32)
    res = service.transcribe(fake_audio)
    assert res == "test voice output"


def test_voice_adaptive_threshold_auto_stop(tmp_path: Any) -> None:
    events: list[dict[str, Any]] = []
    curr_time = [1000.0]

    def fake_clock() -> float:
        return curr_time[0]

    service = VoiceService(models_dir=tmp_path, emit=events.append, clock=fake_clock)
    service._model = MockWhisperModel("hello world")
    service._model_status = "ready"

    # Simulate recording started manually
    service._recording = True
    service._speech_detected = False
    service._start_time = curr_time[0]

    # 1. Ambient noise (low RMS, e.g. 0.005)
    noise = np.full((1024, 1), 0.005, dtype=np.float32)
    for _ in range(5):
        curr_time[0] += 0.064
        service._audio_callback(noise, 1024, None, None)

    assert not service._speech_detected
    assert service.is_recording

    # 2. User starts speaking (loud RMS, e.g. 0.15)
    speech = np.full((1024, 1), 0.15, dtype=np.float32)
    curr_time[0] += 0.064
    service._audio_callback(speech, 1024, None, None)

    assert service._speech_detected
    assert service.is_recording

    # 3. User stops speaking (silence) for 0.5s
    curr_time[0] += 0.5
    service._audio_callback(noise, 1024, None, None)
    assert service.is_recording  # Not yet 1.2s

    # 4. Silence exceeds 1.2s
    curr_time[0] += AUTO_STOP_SILENCE_S + 0.1
    service._audio_callback(noise, 1024, None, None)

    # Auto-stop should have triggered (recording becomes False)
    assert not service.is_recording


@pytest.mark.asyncio
async def test_voice_api_routes(tmp_path: Any) -> None:
    db = Database(tmp_path / "test.db")
    settings = SettingsStore(db)

    events: list[dict[str, Any]] = []
    service = VoiceService(models_dir=tmp_path / "models", emit=events.append)
    service._model = MockWhisperModel("find my resume")
    service._model_status = "ready"

    # Mock sounddevice stream
    service.start_recording = MagicMock(return_value=True)  # type: ignore[method-assign]
    service.stop_recording = MagicMock(return_value="find my resume")  # type: ignore[method-assign]
    service.get_input_devices = MagicMock(  # type: ignore[method-assign]
        return_value=[{"id": 0, "name": "Default Mic", "is_default": True}]
    )

    app = create_app(
        AppConfig(port=8765, token=None),
        settings=settings,
        voice_service=service,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        # GET /voice/status
        r_stat = await client.get("/voice/status")
        assert r_stat.status_code == 200
        assert r_stat.json()["model_status"] == "ready"

        # GET /voice/devices
        r_dev = await client.get("/voice/devices")
        assert r_dev.status_code == 200
        devs = r_dev.json()
        assert len(devs) == 1
        assert devs[0]["name"] == "Default Mic"

        # POST /voice/start
        r_start = await client.post("/voice/start")
        assert r_start.status_code == 200
        assert r_start.json() == {"ok": True}
        assert service.start_recording.called

        # POST /voice/stop
        r_stop = await client.post("/voice/stop")
        assert r_stop.status_code == 200
        assert r_stop.json() == {"text": "find my resume"}
        assert service.stop_recording.called
