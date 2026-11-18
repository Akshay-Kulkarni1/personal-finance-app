import sys
import wave
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main  # noqa: E402
import voice  # noqa: E402

c = TestClient(main.app)


@pytest.fixture(autouse=True)
def authenticated_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("AHEAD_DEMO_MODE", "true")
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "voice-auth.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)
    session = c.post("/auth/register", json={"email":"voice@example.com", "password":"VoiceAccountPassword_123"})
    assert session.status_code == 201
    c.headers["X-CSRF-Token"] = session.json()["csrf_token"]
    yield
    c.cookies.clear()


def test_safe_to_speak():
    assert "4111" not in voice.safe_to_speak("Card 4111 1111 1111 1111 is fine.")
    assert voice.safe_to_speak("One. Two. Three.") == "One. Two."


def test_pcm_to_wav():
    w = wave.open(io.BytesIO(voice.pcm_to_wav(b"\x00\x00" * 100)))
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, voice.TTS_RATE, 100)


def test_routes_fall_back_without_keys(monkeypatch):
    for k in ("NVIDIA_API_KEY", "ASR_FUNCTION_ID", "TTS_FUNCTION_ID"):
        monkeypatch.delenv(k, raising=False)
    assert c.get("/voice/status").json()["asr"] is False
    assert c.post("/tts", json={"text": "hi"}).status_code == 503
    r = c.post("/voice", files={"audio": ("a.webm", b"x" * 200, "audio/webm")})
    assert r.status_code == 503
    assert c.post("/voice", files={"audio": ("a.webm", b"", "audio/webm")}).status_code == 400


def test_retry_gives_up(monkeypatch):
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    calls = []
    with pytest.raises(voice.VoiceUnavailable):
        voice._retry(lambda: calls.append(1) or (_ for _ in ()).throw(TimeoutError()))
    assert len(calls) == 3
