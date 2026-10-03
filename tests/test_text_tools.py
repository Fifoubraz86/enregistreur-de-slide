import numpy as np
import pytest

from capture_reunion.summarize import split_sentences, summarize
from capture_reunion.transcript import Segment, assign_segments, merge_segments
from capture_reunion.translate import Translator, target_language


def test_merge_segments_paragraphs():
    segs = [Segment(0, "Bonjour à tous, nous allons", "A", 4),
            Segment(4.2, "parler du foie.", "A", 7),
            Segment(7.5, "Une question ?", "B", 9),
            Segment(9.2, "Oui ?", "A", 10),
            Segment(14, "Après une longue pause.", "A", 16)]
    merged = merge_segments(segs)
    assert [(m.speaker, m.text) for m in merged] == [
        ("A", "Bonjour à tous, nous allons parler du foie."),
        ("B", "Une question ?"),
        ("A", "Oui ?"),
        ("A", "Après une longue pause."),
    ]
    assert merged[0].end == 7


def test_merge_respects_boundaries_and_length():
    segs = [Segment(t, f"Phrase {t}.", "A", t + 5) for t in range(0, 200, 5)]
    merged = merge_segments(segs, boundaries=[50])
    assert merged[1].start == 50  # coupure au changement de diapo
    assert all(m.end - m.start <= 125 for m in merged)
    assert len(merged) >= 4


def test_short_intro_is_folded_into_first_slide():
    segs = [Segment(0.0, "début", "A", 2)]
    sections = assign_segments([(1.0, 1), (30.0, 2)], segs)
    assert sections[0].slide_index == 1 and sections[0].start == 0.0
    assert sections[0].segments[0].text == "début"


def test_summarize_picks_central_sentences():
    text = (
        "Neuroendocrine tumours of the liver are frequent in metastatic patients. "
        "The weather was nice in Paris yesterday afternoon for the congress dinner. "
        "Liver-directed therapy can control neuroendocrine liver metastases for years. "
        "Sequencing treatments for neuroendocrine tumours improves patient survival and quality of life. "
        "Peptide receptor radionuclide therapy is another option for neuroendocrine tumours. "
        "Somatostatin analogues remain the first line for well differentiated neuroendocrine tumours."
    )
    summary = summarize([text], "en", max_sentences=3)
    assert len(summary.sentences) == 3
    assert not any("weather" in s for s in summary.sentences)
    assert "neuroendocrine" in summary.keywords
    assert summary.sentences == sorted(summary.sentences, key=text.index)  # ordre chronologique


def test_summarize_empty():
    assert summarize(["Oui.", "Bon."], "fr").sentences == []


def test_split_sentences():
    assert split_sentences("Bonjour. Comment ça va ? Très bien!") == ["Bonjour.", "Comment ça va ?", "Très bien!"]


def test_target_language():
    assert target_language("en") == "fr" and target_language("fr") == "en" and target_language("de") == "fr"


def test_translator_with_real_sentencepiece(tmp_path):
    """Chaîne tokenisation → traduction → détokenisation (moteur de traduction simulé)."""
    spm = pytest.importorskip("sentencepiece")
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("\n".join(["the liver is a very important organ in the body",
                                 "neuroendocrine tumours often have a long metastatic course",
                                 "we look at quality of life while treating our patients"] * 50))
    spm.SentencePieceTrainer.train(input=str(corpus), model_prefix=str(tmp_path / "sp"),
                                   vocab_size=50, minloglevel=2)
    sp = spm.SentencePieceProcessor(model_file=str(tmp_path / "sp.model"))

    class Engine:
        def __init__(self):
            self.batches = []

        def translate_batch(self, batch, **kwargs):
            self.batches.append(batch)
            from types import SimpleNamespace
            return [SimpleNamespace(hypotheses=[list(reversed(tokens))]) for tokens in batch]

    engine = Engine()
    tr = Translator("en", "fr", translator=engine, tokenizer=sp)
    out = tr.translate(["The liver is important. We look at quality of life."])
    assert len(engine.batches) == 1 and len(engine.batches[0]) == 2  # une entrée par phrase
    assert isinstance(out[0], str) and out[0]
    assert np.all([isinstance(t, str) for t in engine.batches[0][0]])


def test_merge_text_export_without_end_times():
    """Export texte (heure de début seulement) : les morceaux qui se suivent sont fusionnés."""
    segs = [Segment(0, "of the disease. It will be a", "P"), Segment(6, "slower disease.", "P"),
            Segment(13, "Another point.", "P"), Segment(40, "After a long silence.", "P")]
    merged = merge_segments(segs)
    assert [m.text for m in merged] == [
        "of the disease. It will be a slower disease. Another point.", "After a long silence."]
