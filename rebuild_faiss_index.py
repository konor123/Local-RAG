"""Rebuild a corrupted FAISS index from its intact metadata.jsonl sidecar.

Run with the application's Python environment, for example:
    py -3.12 rebuild_faiss_index.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

from background_embedder import EMBEDDING_MODEL_NAME
from faiss_store import FAISS_INDEX_DIR, VECTOR_DIM, _backup_corrupt_file, _normalize_vectors


def _metadata_count(metadata_file: str) -> int:
    """Validate the JSONL sidecar and return its non-empty record count."""
    count = 0
    with open(metadata_file, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"Invalid metadata JSON on line {line_number}: {error}") from error
            count += 1
    return count


def _resolve_metadata_file(index_dir: str, requested_path: str | None = None) -> str:
    """Return metadata.jsonl, or the newest timestamped backup if it was moved aside."""
    if requested_path:
        if not os.path.isfile(requested_path):
            raise FileNotFoundError(f"metadata file not found: {requested_path}")
        return os.path.abspath(requested_path)

    metadata_file = os.path.join(index_dir, "metadata.jsonl")
    if os.path.isfile(metadata_file):
        return metadata_file

    backups = sorted(Path(index_dir).glob("metadata.jsonl.corrupt.*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if backups:
        fallback = str(backups[0])
        print(f"[FAISS Rebuild] metadata.jsonl not found; using newest backup: {fallback}")
        return fallback
    raise FileNotFoundError(f"metadata.jsonl not found in {index_dir}")


def _metadata_batches(metadata_file: str, batch_size: int) -> Iterator[list[dict]]:
    return _metadata_batches_from(metadata_file, batch_size, skip=0)


def _metadata_batches_from(metadata_file: str, batch_size: int, skip: int = 0) -> Iterator[list[dict]]:
    batch: list[dict] = []
    seen = 0
    with open(metadata_file, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if seen < skip:
                seen += 1
                continue
            batch.append(json.loads(line))
            seen += 1
            if len(batch) == batch_size:
                yield batch
                batch = []
    if batch:
        yield batch


def _metadata_signature(metadata_file: str) -> dict:
    stat = os.stat(metadata_file)
    hasher = hashlib.sha256()
    with open(metadata_file, "rb") as handle:
        hasher.update(handle.read(8192))
        handle.seek(max(0, stat.st_size - 8192))
        hasher.update(handle.read(8192))
    return {
        "path": os.path.abspath(metadata_file),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "sample_hash": hasher.hexdigest()[:16],
    }


def _write_json_atomic(path: str, payload: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_encoder():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")

    def encode(texts: list[str], batch_size: int):
        return model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

    return encode


def rebuild(metadata_file: str, index_file: str, batch_size: int, checkpoint_every: int = 50_000) -> int:
    """Embed sidecar content in batches and atomically replace the FAISS index."""
    import faiss

    total = _metadata_count(metadata_file)
    if total == 0:
        raise RuntimeError(f"No metadata records found: {metadata_file}")

    print(f"[FAISS Rebuild] Validated {total:,} metadata chunks", flush=True)
    signature = _metadata_signature(metadata_file)
    checkpoint_key = hashlib.sha256(
        f"{signature['path']}|{signature['size']}|{signature['sample_hash']}".encode("utf-8")
    ).hexdigest()[:12]
    checkpoint_file = f"{index_file}.rebuild.{checkpoint_key}.partial"
    checkpoint_state = f"{checkpoint_file}.json"
    index = None
    processed = 0
    if os.path.exists(checkpoint_file) and os.path.exists(checkpoint_state):
        try:
            with open(checkpoint_state, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            if state.get("metadata") == signature:
                index = faiss.read_index(checkpoint_file)
                processed = int(state.get("processed", 0))
                if index.ntotal != processed:
                    raise RuntimeError(f"checkpoint mismatch: index={index.ntotal}, state={processed}")
                print(f"[FAISS Rebuild] Resuming checkpoint at {processed:,}/{total:,}", flush=True)
        except Exception as error:
            print(f"[FAISS Rebuild] Ignoring invalid checkpoint: {error}", flush=True)
            for checkpoint_path in (checkpoint_file, checkpoint_state):
                try:
                    if os.path.exists(checkpoint_path):
                        os.remove(checkpoint_path)
                except OSError:
                    pass
            index = None
            processed = 0
    if index is None:
        index = faiss.IndexFlatIP(VECTOR_DIM)

    print("[FAISS Rebuild] Loading embedding model...", flush=True)
    encode = _load_encoder()
    last_checkpoint = processed
    for batch in _metadata_batches_from(metadata_file, batch_size, skip=processed):
        texts = [str(item.get("content", "")) for item in batch]
        index.add(_normalize_vectors(encode(texts, batch_size=batch_size)))
        processed += len(batch)
        if processed == total or (processed - len(batch)) // 10_000 != processed // 10_000:
            print(f"[FAISS Rebuild] {processed:,}/{total:,} chunks embedded", flush=True)
        if checkpoint_every and processed - last_checkpoint >= checkpoint_every:
            checkpoint_tmp = f"{checkpoint_file}.tmp"
            faiss.write_index(index, checkpoint_tmp)
            os.replace(checkpoint_tmp, checkpoint_file)
            _write_json_atomic(checkpoint_state, {"metadata": signature, "processed": processed})
            last_checkpoint = processed
            print(f"[FAISS Rebuild] Checkpoint saved at {processed:,}", flush=True)

    if index.ntotal != total:
        raise RuntimeError(f"Rebuild count mismatch: index={index.ntotal:,}, metadata={total:,}")

    tmp_index_file = f"{index_file}.rebuild.tmp"
    try:
        faiss.write_index(index, tmp_index_file)
        if os.path.exists(index_file):
            backup_path = _backup_corrupt_file(index_file)
            print(f"[FAISS Rebuild] Backed up existing index: {backup_path}")
        os.replace(tmp_index_file, index_file)
    finally:
        if os.path.exists(tmp_index_file):
            os.remove(tmp_index_file)
    for checkpoint_path in (checkpoint_file, checkpoint_state):
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)

    print(f"[FAISS Rebuild] Complete: {index.ntotal:,} vectors written to {index_file}", flush=True)
    return index.ntotal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", default=FAISS_INDEX_DIR, help="Directory containing index.faiss and metadata.jsonl")
    parser.add_argument("--metadata-file", default=None, help="Explicit metadata JSONL path to rebuild from")
    parser.add_argument("--batch-size", type=int, default=512, help="Embedding batch size")
    parser.add_argument("--checkpoint-every", type=int, default=50_000, help="Save resumable partial index every N chunks")
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    index_dir = os.path.abspath(args.index_dir)
    index_file = os.path.join(index_dir, "index.faiss")
    try:
        metadata_file = _resolve_metadata_file(index_dir, args.metadata_file)
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error
    rebuild(metadata_file, index_file, args.batch_size, checkpoint_every=args.checkpoint_every)


if __name__ == "__main__":
    main()
