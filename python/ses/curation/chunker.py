from typing import List
from pydantic import BaseModel
from ses.core.chunking import chunk_text
from ses.config import CHUNK_SIZE, CHUNK_OVERLAP
from ses.curation.normalize import NormalizedSection

class CurationChunk(BaseModel):
    chunk_id: str
    doc_id: str
    section_id: str
    text: str
    citation_anchor: str
    source_hash: str

def chunk_section(
    section: NormalizedSection,
    doc_id: str,
    source_hash: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[CurationChunk]:
    """
    Segments a single NormalizedSection into CurationChunks.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be strictly less than chunk_size")
        
    text_chunks = chunk_text(section.text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    
    curation_chunks = []
    for idx, text in enumerate(text_chunks, start=1):
        chunk_index = f"{idx:03d}"
        chunk_id = f"{doc_id}:{section.section_id}:{chunk_index}"
        
        curation_chunks.append(CurationChunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            section_id=section.section_id,
            text=text,
            citation_anchor=section.anchor,
            source_hash=source_hash
        ))
    return curation_chunks

def chunk_document(
    sections: List[NormalizedSection],
    doc_id: str,
    source_hash: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[CurationChunk]:
    """
    Segments a list of NormalizedSections into a flat list of CurationChunks.
    """
    all_chunks = []
    for section in sections:
        all_chunks.extend(chunk_section(section, doc_id, source_hash, chunk_size, chunk_overlap))
    return all_chunks
