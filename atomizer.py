# -*- coding: utf-8 -*-
"""Deterministic, source-linked knowledge atom extraction."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Dict, Iterable, List


_SPEC_RE = re.compile(r"(?:전압|정격|규격|치수|크기|가격|단가|수량|납기|재질|인증)\s*[:：]\s*.+", re.IGNORECASE)


def make_parent_chunk_id(source: str, content: str, ordinal: int) -> str:
    """Create a deterministic ID independent of vector-backend row IDs."""
    payload = f"{source or ''}\x1f{ordinal}\x1f{content or ''}".encode("utf-8", "ignore")
    return hashlib.sha256(payload).hexdigest()


def assign_parent_chunk_ids(vector_docs: List[Dict]) -> List[Dict]:
    """Attach stable parent IDs while preserving existing document dictionaries."""
    ordinals = defaultdict(int)
    for doc in vector_docs or []:
        source = str(doc.get("source") or doc.get("metadata", {}).get("source") or "Unknown")
        metadata = dict(doc.get("metadata") or {})
        ordinal = metadata.get("chunk_ordinal")
        if ordinal is None:
            ordinal = ordinals[source]
        try:
            ordinal = int(ordinal)
        except (TypeError, ValueError):
            ordinal = ordinals[source]
        ordinals[source] = max(ordinals[source], ordinal + 1)
        metadata["parent_chunk_id"] = make_parent_chunk_id(source, str(doc.get("content") or ""), ordinal)
        metadata["chunk_ordinal"] = ordinal
        doc["metadata"] = metadata
    return vector_docs


def extract_atoms(parent_chunk_id: str, content: str, source: str) -> List[Dict]:
    """Extract bounded table/spec atoms without any LLM inference."""
    atoms = []
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    headers = None
    for line in lines:
        if "\t" in line:
            cells = [cell.strip() for cell in line.split("\t")]
            if headers is None and len(cells) >= 2:
                headers = cells
                continue
            if headers and len(cells) >= 2:
                fields = [f"{headers[index] if index < len(headers) else f'열{index + 1}'}: {value}" for index, value in enumerate(cells) if value]
                if fields:
                    atoms.append({"content": " | ".join(fields), "type": "table_row"})
            continue
        if _SPEC_RE.search(line):
            atoms.append({"content": line, "type": "spec_field"})

    deduped = []
    seen = set()
    for index, atom in enumerate(atoms[:20]):
        atom_content = atom["content"][:1000]
        if atom_content in seen:
            continue
        seen.add(atom_content)
        atom_id = hashlib.sha256(f"{parent_chunk_id}\x1f{index}\x1f{atom_content}".encode("utf-8")).hexdigest()
        deduped.append({
            "atom_id": atom_id,
            "parent_chunk_id": parent_chunk_id,
            "atom_index": len(deduped),
            "atom_type": atom["type"],
            "content": atom_content,
            "source": source,
        })
    return deduped


def extract_atoms_for_documents(vector_docs: Iterable[Dict]) -> List[Dict]:
    atoms = []
    for doc in vector_docs or []:
        metadata = doc.get("metadata") or {}
        parent_chunk_id = metadata.get("parent_chunk_id")
        if parent_chunk_id:
            atoms.extend(extract_atoms(parent_chunk_id, doc.get("content", ""), doc.get("source", "Unknown")))
    return atoms
