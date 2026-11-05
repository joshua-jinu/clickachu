import pytest

from clickachu.llm.provider import Done, LLMError, TextDelta
from clickachu.llm.synthesis import passages, synthesize
from tests.fakes import FakeProvider


def test_short_factual_sentences_are_not_dropped_from_model_evidence():
    text = (
        "Luma Institute opened in 2018. It teaches robotics and electronics to adults. "
        "Its campus is in Pune."
    )
    evidence = passages(text, "Describe Luma Institute, including location and opening year.")
    assert "2018" in evidence and "Pune" in evidence and "robotics" in evidence


async def test_unobserved_number_cannot_be_written_as_a_sourced_fact():
    provider = FakeProvider(
        [TextDelta('{"answer":"The institute opened in 1988.","source_ids":[1]}'), Done()]
    )
    with pytest.raises(LLMError, match="date or number"):
        _ = [
            e
            async for e in synthesize(
                provider,
                "local",
                "When did it open?",
                [{"url": "https://example.com", "title": "Luma", "text": "Luma opened in 2018."}],
            )
        ]
