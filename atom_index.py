# -*- coding: utf-8 -*-
"""Lifecycle-safe isolated atom indexing and parent-chunk retrieval."""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Dict, Iterable, List

from atom_catalog import activate_atoms, get_active_atom_ids, get_active_parents, retire_source_except, stage_documents
from atomizer import extract_atoms_for_documents
from config_manager import load_config


def _config() -> Dict:
    return load_config().get("atomization", {})


def is_enabled() -> bool:
    env = os.environ.get("ATOMIZATION_ENABLED")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "y")
    return bool(_config().get("enabled", False))


def _parents(vector_docs: Iterable[Dict]) -> List[Dict]:
    parents = []
    for doc in vector_docs or []:
        metadata = dict(doc.get("metadata") or {})
        parent_chunk_id = metadata.get("parent_chunk_id")
        if not parent_chunk_id:
            continue
        content = str(doc.get("content") or "")
        parents.append({
            "parent_chunk_id": parent_chunk_id,
            "source": str(doc.get("source") or metadata.get("source") or "Unknown"),
            "content": content,
            "metadata": metadata,
            "content_fingerprint": hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest(),
        })
    return parents


def index_documents(vector_docs: List[Dict], embed_documents: Callable[[List[str]], List[list]]) -> int:
    """Stage catalog rows, persist atom vectors, then activate searchable atoms."""
    if not is_enabled():
        return 0
    parents = _parents(vector_docs)
    atoms = extract_atoms_for_documents(vector_docs)
    if not parents:
        return 0
    if not atoms:
        for source in {parent["source"] for parent in parents}:
            retire_source_except(source, [])
        return 0
    existing_active = get_active_atom_ids([atom["atom_id"] for atom in atoms])
    stage_documents(parents, atoms)
    new_atoms = [atom for atom in atoms if atom["atom_id"] not in existing_active]
    vectors = embed_documents([atom["content"] for atom in new_atoms])
    atom_docs = [{
        "content": atom["content"],
        "source": atom["source"],
        "vector": vector,
        "metadata": {
            "atom_id": atom["atom_id"],
            "parent_chunk_id": atom["parent_chunk_id"],
            "atom_type": atom["atom_type"],
        },
    } for atom, vector in zip(new_atoms, vectors)]
    if len(atom_docs) != len(new_atoms):
        raise RuntimeError("Atom embedding count did not match extracted atoms")
    from faiss_store import add_atom_documents, save_atom_index

    if atom_docs:
        add_atom_documents(atom_docs)
        save_atom_index()
    activate_atoms([atom["atom_id"] for atom in atoms])
    for source in {parent["source"] for parent in parents}:
        retire_source_except(source, [parent["parent_chunk_id"] for parent in parents if parent["source"] == source])
    return len(atoms)


def search_parent_chunks(query_vector: list, query_text: str, k: int = None) -> List[Dict]:
    """Promote atom hits to deduplicated active parent chunks for final retrieval."""
    if not is_enabled():
        return []
    cfg = _config()
    candidate_k = int(cfg.get("candidate_k", 20) or 20)
    parent_k = int(k or cfg.get("parent_k", 5) or 5)
    from faiss_store import search_atoms

    hits = search_atoms(query_vector, query_text, k=candidate_k * 3)
    atom_ids = [hit.get("metadata", {}).get("atom_id") for hit in hits if hit.get("metadata", {}).get("atom_id")]
    parents_by_atom = get_active_parents(atom_ids)
    parents = []
    seen = set()
    for hit in hits:
        atom_id = hit.get("metadata", {}).get("atom_id")
        parent = parents_by_atom.get(atom_id)
        if not parent or parent["parent_chunk_id"] in seen:
            continue
        seen.add(parent["parent_chunk_id"])
        parents.append({
            "content": parent["content"],
            "source": parent["source"],
            "metadata": {**parent["metadata"], "parent_chunk_id": parent["parent_chunk_id"]},
            "score": hit.get("score", 0.0),
            "source_engine": "atom_parent_vector",
        })
        if len(parents) >= parent_k:
            break
    return parents
