import pytest

from rag.ingestion import load_corpus
from rag.retriever import HybridRetriever


@pytest.fixture(scope="module")
def retriever():
    return HybridRetriever(load_corpus())


@pytest.mark.parametrize("query,doc", [
    ("laptop replacement eligibility", "it-hardware-policy"),
    ("how many days of annual leave", "hr-leave-policy"),
    ("hotel limit per night travel reimbursement", "travel-policy"),
    ("client entertainment limit per person", "expense-policy"),
    ("software access request GitHub", "it-access-policy"),
])
@pytest.mark.parametrize("mode", ["bm25", "hybrid", "hybrid_rerank"])
def test_top_document(retriever, query, doc, mode):
    assert retriever.unique_docs(retriever.search(query, k=4, mode=mode))[0] == doc


def test_chunks_carry_metadata():
    chunks = load_corpus()
    assert len(chunks) > 40
    assert all(c.doc_id and c.title and c.version and c.section for c in chunks)
