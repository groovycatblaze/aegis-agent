"""Document ingestion: parse front matter, split on headings, window long sections."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent / "corpus" / "clean"
WINDOW_WORDS = 120
OVERLAP_WORDS = 30


@dataclass
class Chunk:
    id: str
    doc_id: str
    title: str
    section: str
    version: str
    text: str

    @property
    def search_text(self) -> str:
        return f"{self.title}. {self.section}. {self.text}"

    def to_dict(self) -> dict:
        return asdict(self)


def parse_document(raw: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    body = raw
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = raw[m.end():]
    return meta, body


def split_sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading, buf = "Overview", []
    for line in body.splitlines():
        if line.startswith("## "):
            if "".join(buf).strip():
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        elif line.startswith("# "):
            continue
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((heading, "\n".join(buf).strip()))
    return sections


def window(text: str, size: int = WINDOW_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text]
    out, start = [], 0
    while start < len(words):
        out.append(" ".join(words[start:start + size]))
        if start + size >= len(words):
            break
        start += size - overlap
    return out


def chunk_document(raw: str, fallback_id: str) -> list[Chunk]:
    meta, body = parse_document(raw)
    doc_id = meta.get("id", fallback_id)
    chunks = []
    for s_idx, (heading, text) in enumerate(split_sections(body)):
        for w_idx, piece in enumerate(window(text)):
            chunks.append(Chunk(id=f"{doc_id}#{s_idx}.{w_idx}", doc_id=doc_id,
                                title=meta.get("title", doc_id), section=heading,
                                version=meta.get("version", "1.0"), text=piece))
    return chunks


def load_corpus(directory: Path = CORPUS_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_document(path.read_text(encoding="utf-8"), path.stem))
    return chunks
