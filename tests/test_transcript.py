from datetime import datetime

from capture_reunion.transcript import (
    assign_segments,
    notes_by_slide,
    offset_from_clock,
    parse_timestamp,
    parse_transcript,
)

SRT = """1
00:00:01,000 --> 00:00:04,000
Intervenant 1: Bonjour à tous.

2
00:00:10,500 --> 00:00:12,000
On passe à la suite.
"""

PLAUD_TXT = """Intervenant 1  00:00:02
Bonjour, on commence.
Deuxième ligne.

Intervenant 2  00:01:05
Une question sur la diapo.
"""

BRACKETS = """[00:00:03] Dr Martin: Premier point
[01:02:03] Dr Durand: Conclusion à 14:30
"""


def test_parse_timestamp():
    assert parse_timestamp("01:02:03,5") == 3723.5
    assert parse_timestamp("02:03") == 123


def test_parse_srt():
    segs = parse_transcript(SRT)
    assert len(segs) == 2
    assert segs[0].speaker == "Intervenant 1"
    assert segs[0].text == "Bonjour à tous."
    assert segs[1].start == 10.5 and segs[1].end == 12.0


def test_parse_plaud_txt():
    segs = parse_transcript(PLAUD_TXT)
    assert [(s.start, s.speaker) for s in segs] == [(2, "Intervenant 1"), (65, "Intervenant 2")]
    assert segs[0].text == "Bonjour, on commence. Deuxième ligne."


def test_parse_brackets():
    segs = parse_transcript(BRACKETS)
    assert segs[0].speaker == "Dr Martin" and segs[0].text == "Premier point"
    assert segs[1].start == 3723 and segs[1].text == "Conclusion à 14:30"


def test_assign_with_offset():
    timeline = [(5.0, 1), (60.0, 2), (120.0, 1)]
    segs = parse_transcript(PLAUD_TXT + "\nIntervenant 1  00:01:50\nRetour au début.\n")
    # Le Plaud a été lancé 10 s après le début de l'enregistrement vidéo.
    offset = offset_from_clock(datetime(2026, 1, 1, 9, 0, 0), datetime(2026, 1, 1, 9, 0, 10))
    assert offset == 10
    sections = assign_segments(timeline, segs, offset, duration=200)
    assert [s.slide_index for s in sections] == [1, 2, 1]
    assert [len(s.segments) for s in sections] == [1, 1, 1]
    notes = notes_by_slide(sections, offset)
    assert "Retour au début." in notes[1] and "Bonjour" in notes[1]
    assert "[00:01:15]" in notes[2]  # horodatage ramené au temps de la vidéo


def test_assign_before_first_slide():
    segs = parse_transcript("[00:00:01] Avant\n[00:00:30] Pendant\n")
    sections = assign_segments([(20.0, 1)], segs)
    assert sections[0].slide_index is None and sections[0].segments[0].text == "Avant"
    assert sections[1].slide_index == 1
