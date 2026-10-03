import cv2

from capture_reunion.session import Session
from capture_reunion.slides import DetectorSettings, SlideDetector, crop_zone, ssim
from capture_reunion.workflow import extract_slides
from conftest import H, W, frames, make_slide

ZONE = (0.0, 0.0, 1.0, (H - 80) / H)  # tout sauf la bande de la webcam


def test_ssim():
    a = cv2.cvtColor(make_slide(1), cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(make_slide(2), cv2.COLOR_BGR2GRAY)
    assert ssim(a, a) > 0.999
    assert ssim(a, b) < 0.9


def test_crop_zone():
    img = make_slide(1)
    assert crop_zone(img, (0.5, 0.5, 0.5, 0.5)).shape[:2] == (H // 2, W // 2)
    assert crop_zone(img, None) is img


def test_detector_scenario():
    detector = SlideDetector(DetectorSettings())
    for t, frame in frames(fps=1):
        detector.feed(t, frame)
    # 4 diapos distinctes ; la vidéo n'est pas retenue ; le retour à 1 n'est pas dupliqué
    assert [s.index for s in detector.slides] == [1, 2, 3, 4]
    assert [e.slide_index for e in detector.timeline] == [1, 2, 3, 1, 4]
    times = [e.timestamp for e in detector.timeline]
    assert times == sorted(times)
    assert abs(times[1] - 6) <= 1
    # La diapo 3 a été mise à jour avec sa version complète (3 puces).
    full = cv2.cvtColor(make_slide(3, bullets=3), cv2.COLOR_BGR2GRAY)
    stored = cv2.cvtColor(detector.slides[2].image, cv2.COLOR_BGR2GRAY)
    assert ssim(full, stored) > 0.99


def test_detector_with_webcam_and_zone():
    detector = SlideDetector(DetectorSettings(zone=ZONE))
    for t, frame in frames(fps=2, webcam=True):
        if detector.wants_sample(t):
            detector.feed(t, frame)
    assert len(detector.slides) == 4
    assert detector.slides[0].image.shape[0] == H - 80


def test_extract_from_video(sample_video):
    session = Session.for_video(sample_video)
    extract_slides(session, DetectorSettings(zone=ZONE))
    assert len(session.slides) == 4
    assert all((session.folder / s.file).exists() for s in session.slides)
    assert [i for _, i in session.timeline] == [1, 2, 3, 1, 4]
    reloaded = Session.load(session.folder)
    assert reloaded.zone == ZONE
    assert len(reloaded.slides) == 4
