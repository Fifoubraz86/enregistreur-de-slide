import threading
import time

from capture_reunion import power
from capture_reunion.power import ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED, KeepAwake


def test_keep_awake_sets_and_releases_from_one_thread(monkeypatch):
    calls = []

    def setter(flags):
        calls.append((flags, threading.get_ident()))
        return 1

    monkeypatch.setattr(power, "REFRESH_SECONDS", 0.05)
    k = KeepAwake(setter=setter)
    k.start()
    assert k.active
    time.sleep(0.2)
    # Arrêt appelé depuis un autre fil (comme la finalisation dans l'interface).
    t = threading.Thread(target=k.stop)
    t.start()
    t.join()
    assert not k.active
    assert calls[0][0] == ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    assert len(calls) >= 3  # demande initiale + rafraîchissements + libération
    assert calls[-1][0] == ES_CONTINUOUS
    assert len({tid for _, tid in calls}) == 1  # tout depuis le même fil, comme l'exige Windows


def test_keep_awake_noop_when_unsupported():
    k = KeepAwake(setter=None)
    if not k.supported:
        k.start()
        k.stop()
        assert not k.active
