"""Transcription locale, avec un faux modèle Whisper (le vrai se télécharge sur le PC)."""

import subprocess
from types import SimpleNamespace

import docx
import numpy as np
import pytest

from capture_reunion import transcribe as tr_mod
from capture_reunion.ffmpeg_utils import find_ffmpeg

from capture_reunion.session import Session, SlideRecord
from capture_reunion.transcribe import Track, remove_echo, transcribe_session, transcribe_tracks
from capture_reunion.transcript import Segment, load_transcript
from capture_reunion.workflow import make_report

SCRIPTS = {
    "piste_participants.m4a": [(1.0, 4.0, " Bonjour à tous, on commence."),
                               (12.0, 15.0, " Voici les résultats.")],
    # Le micro capte sa propre voix, et l'écho des haut-parleurs à 12 s.
    "piste_moi.m4a": [(6.0, 9.0, " Merci, j'ai une question."),
                      (12.1, 15.0, " voici les résultats"),
                      (20.0, 22.0, " ")],
}


class FakeAudio(np.ndarray):
    """Tableau audio qui retient de quel fichier il vient."""


def fake_load_audio(path):
    audio = np.zeros(30 * tr_mod.SAMPLE_RATE, np.float32).view(FakeAudio)
    audio.name = path.name
    return audio


@pytest.fixture(autouse=True)
def no_ffmpeg_decode(monkeypatch, request):
    if "real_decode" not in request.keywords:
        monkeypatch.setattr(tr_mod, "load_audio", fake_load_audio)


class FakeWhisper:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        # Régression : on transmet le son déjà décodé, jamais un chemin (décodage PyAV).
        assert isinstance(audio, np.ndarray)
        self.calls.append((audio.name, kwargs))
        name = audio.name
        segs = [SimpleNamespace(start=a, end=b, text=t) for a, b, t in SCRIPTS[name]]
        return iter(segs), SimpleNamespace(duration=30.0)


def test_remove_echo():
    others = [Segment(10, "Voici les résultats de l'étude", "Participants", 14)]
    mine = [Segment(10.2, "voici les résultats de l'étude", "Moi", 14),
            Segment(10.5, "Tout à fait d'accord", "Moi", 12)]
    assert [s.text for s in remove_echo(mine, others)] == ["Tout à fait d'accord"]


def test_transcribe_tracks_labels_and_progress(tmp_path):
    for name in SCRIPTS:
        (tmp_path / name).write_bytes(b"")
    progress = []
    whisper = FakeWhisper()
    segs = transcribe_tracks(
        [Track(tmp_path / "piste_participants.m4a", "Participants"), Track(tmp_path / "piste_moi.m4a", "Moi")],
        vocabulary="RCP, pembrolizumab", progress=progress.append, whisper=whisper)
    assert [(s.speaker, s.text) for s in segs] == [
        ("Participants", "Bonjour à tous, on commence."),
        ("Moi", "Merci, j'ai une question."),
        ("Participants", "Voici les résultats."),
    ]
    assert progress[-1] == 1.0 and progress == sorted(progress)
    kwargs = whisper.calls[0][1]
    assert kwargs["language"] == "fr" and kwargs["vad_filter"] is True
    assert kwargs["hotwords"] == "RCP, pembrolizumab"


def test_transcribe_session_feeds_report(tmp_path):
    for name in SCRIPTS:
        (tmp_path / name).write_bytes(b"")
    session = Session(folder=tmp_path, started_at="2026-10-03T14:30:00", duration=30,
                      tracks={"participants": "piste_participants.m4a", "moi": "piste_moi.m4a"})
    session.slides = [SlideRecord(1, 0.0, "d1.png"), SlideRecord(2, 10.0, "d2.png")]
    session.timeline = [(0.0, 1), (10.0, 2)]
    srt = transcribe_session(session, whisper=FakeWhisper())

    assert srt.name == "transcription.srt"
    assert Session.load(tmp_path).transcript == "transcription.srt"
    parsed = load_transcript(srt)
    assert [s.speaker for s in parsed] == ["Participants", "Moi", "Participants"]
    assert "[00:00:06] Moi : Merci" in (tmp_path / "transcription.txt").read_text(encoding="utf-8")

    # Sans fichier précisé, le compte-rendu prend la transcription du logiciel, sans décalage,
    # même si un décalage Plaud traîne dans les réglages.
    report = make_report(session, offset=300)
    text = "\n".join(p.text for p in docx.Document(str(report)).paragraphs)
    assert text.index("Merci, j'ai une question") < text.index("Diapo 2")
    assert text.index("Diapo 2") < text.index("Voici les résultats")


@pytest.mark.real_decode
def test_load_audio_with_ffmpeg(tmp_path):
    try:
        ffmpeg = find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("ffmpeg absent")
    path = tmp_path / "piste.m4a"
    subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ac", "2", "-ar", "48000", str(path)], check=True)
    audio = tr_mod.load_audio(path)
    assert audio.dtype == np.float32
    assert abs(audio.size / tr_mod.SAMPLE_RATE - 2.0) < 0.1
    assert 0.05 < np.abs(audio).max() <= 1.0  # sinus lavfi : amplitude 1/8
