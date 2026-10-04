from pathlib import Path

from capture_reunion.glossary import Glossary, default_path, load, parse

GLOSSARY = Path(__file__).resolve().parent.parent / "glossaires" / "glossaire-endocrinologie-diabetologie.txt"


def test_parse_endocrinology_glossary():
    g = load(GLOSSARY)
    assert len(g) > 400
    assert len(g.sections) == 13
    assert "Tumeurs neuroendocrines (digestives, pulmonaires, thymiques) et NEM" in g.sections
    tsh = next(e for e in g.entries if e.abbr_fr == ["TSH"])
    assert tsh.fr.startswith("Thyréostimuline") and tsh.en.startswith("Thyroid-stimulating hormone")
    assert tsh.section.startswith("Thyroïde")


def test_parse_three_columns_and_english_abbreviation():
    entries = parse("SSP / PFS — Survie sans progression — Progression-free survival\n"
                    "– — Syndrome carcinoïde — Carcinoid syndrome\n"
                    "HPO / HGPO — Hyperglycémie provoquée — Oral glucose tolerance test (OGTT)\n")
    assert entries[0].abbr_fr == ["SSP", "PFS"]
    assert entries[1].abbr_fr == []
    assert entries[2].abbr_en == ["OGTT"]
    assert entries[2].label("en") == "Oral glucose tolerance test (OGTT) (HPO / HGPO)" or \
        entries[2].label("en").startswith("Oral glucose tolerance test")


def test_relevant_handles_plural_spelling_and_abbreviations():
    g = load(GLOSSARY)
    text = "Most neuroendocrine tumors are G1. PRRT and TACE were discussed at the MDT; HbA1c was 7%."
    labels = [e.fr for e in g.relevant(text, "en")]
    assert any(f.startswith("Tumeur neuroendocrine") for f in labels)
    assert any("Radiothérapie interne vectorisée" in f for f in labels)
    assert any("Chimioembolisation" in f for f in labels)
    assert any(f.startswith("Hémoglobine glyquée") for f in labels)
    assert not any("Agranulocytose" in f for f in labels)


def test_french_source():
    g = load(GLOSSARY)
    prompt = g.prompt_for("Le dossier passe en RCP ; la TSH est basse.", "fr", "en")
    assert "Multidisciplinary team meeting" in prompt and "Thyroid-stimulating hormone" in prompt


def test_section_terms_for_speech_recognition():
    g = load(GLOSSARY)
    terms = g.section_terms("Tumeurs neuroendocrines (digestives, pulmonaires, thymiques) et NEM")
    assert "Chromogranin A" in terms and "5-HIAA" in terms
    assert len(terms) <= 700


def test_empty_and_default():
    assert not Glossary([])
    assert default_path() == GLOSSARY
