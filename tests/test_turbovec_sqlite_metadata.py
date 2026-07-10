import os
import sys
import tempfile
import types
import unittest


class FakeIdMapIndex:
    saved = None

    def __init__(self, dim=384, bit_width=4):
        self.dim = dim
        self.bit_width = bit_width
        self.ids = []
        self.vectors = []

    def __len__(self):
        return len(self.ids)

    def add_with_ids(self, vectors, ids):
        self.vectors.extend(vectors.tolist())
        self.ids.extend([int(value) for value in ids.tolist()])

    def search(self, query, k=5):
        import numpy as np

        ids = list(reversed(self.ids))[:k]
        scores = [1.0 - (index * 0.1) for index, _ in enumerate(ids)]
        return np.array([scores], dtype=np.float32), np.array([ids], dtype=np.uint64)

    def write(self, path):
        FakeIdMapIndex.saved = self
        with open(path, "wb") as f:
            f.write(b"fake")

    @classmethod
    def load(cls, path):
        if cls.saved is None:
            raise RuntimeError("no saved fake index")
        return cls.saved


class TurboVecSQLiteMetadataTests(unittest.TestCase):
    def test_add_search_and_load_use_sqlite_metadata_without_jsonl(self):
        import faiss_store
        import sqlite_index

        original_module = sys.modules.get("turbovec")
        fake_module = types.SimpleNamespace(IdMapIndex=FakeIdMapIndex)
        sys.modules["turbovec"] = fake_module
        original_db = os.environ.get("SQLITE_INDEX_PATH")

        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["SQLITE_INDEX_PATH"] = os.path.join(tmpdir, "metadata.sqlite3")
            backend = faiss_store.TurboVecBackend()
            backend.index_dir = tmpdir
            backend.index_file = os.path.join(tmpdir, "index.tvim")
            backend.meta_file = os.path.join(tmpdir, "metadata.jsonl")
            docs = [
                {"content": "alpha pump", "source": os.path.join(tmpdir, "a.txt"), "metadata": {"n": 1}, "vector": [1.0] * faiss_store.VECTOR_DIM},
                {"content": "bravo alarm", "source": os.path.join(tmpdir, "b.txt"), "metadata": {"n": 2}, "vector": [0.5] * faiss_store.VECTOR_DIM},
            ]

            backend.add_documents(docs)
            backend.save_index()
            results = backend.search_similar([1.0] * faiss_store.VECTOR_DIM, k=2)
            reloaded = faiss_store.TurboVecBackend()
            reloaded.index_dir = tmpdir
            reloaded.index_file = backend.index_file
            reloaded.meta_file = backend.meta_file
            reload_results = reloaded.search_similar([1.0] * faiss_store.VECTOR_DIM, k=2)
            stats = sqlite_index.get_stats(os.environ["SQLITE_INDEX_PATH"])
            metadata_jsonl_exists = os.path.exists(os.path.join(tmpdir, "metadata.jsonl"))

        if original_module is None:
            sys.modules.pop("turbovec", None)
        else:
            sys.modules["turbovec"] = original_module
        if original_db is None:
            os.environ.pop("SQLITE_INDEX_PATH", None)
        else:
            os.environ["SQLITE_INDEX_PATH"] = original_db

        self.assertFalse(metadata_jsonl_exists)
        self.assertEqual(stats["chunks"], 2)
        self.assertEqual([item["content"] for item in results], ["bravo alarm", "alpha pump"])
        self.assertEqual([item["content"] for item in reload_results], ["bravo alarm", "alpha pump"])

    def test_legacy_jsonl_without_sqlite_metadata_fails_closed(self):
        import faiss_store

        original_module = sys.modules.get("turbovec")
        fake_module = types.SimpleNamespace(IdMapIndex=FakeIdMapIndex)
        sys.modules["turbovec"] = fake_module
        original_db = os.environ.get("SQLITE_INDEX_PATH")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["SQLITE_INDEX_PATH"] = os.path.join(tmpdir, "metadata.sqlite3")
                backend = faiss_store.TurboVecBackend()
                backend.index_dir = tmpdir
                backend.index_file = os.path.join(tmpdir, "index.tvim")
                backend.meta_file = os.path.join(tmpdir, "metadata.jsonl")
                with open(backend.index_file, "wb") as f:
                    f.write(b"fake")
                with open(backend.meta_file, "w", encoding="utf-8") as f:
                    f.write('{"id": 1, "content": "legacy"}\n')

                with self.assertRaisesRegex(RuntimeError, "full reindex"):
                    backend.load_index()
        finally:
            if original_module is None:
                sys.modules.pop("turbovec", None)
            else:
                sys.modules["turbovec"] = original_module
            if original_db is None:
                os.environ.pop("SQLITE_INDEX_PATH", None)
            else:
                os.environ["SQLITE_INDEX_PATH"] = original_db

    def test_existing_index_without_any_sqlite_metadata_fails_closed(self):
        import faiss_store

        original_module = sys.modules.get("turbovec")
        fake_module = types.SimpleNamespace(IdMapIndex=FakeIdMapIndex)
        sys.modules["turbovec"] = fake_module
        original_db = os.environ.get("SQLITE_INDEX_PATH")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["SQLITE_INDEX_PATH"] = os.path.join(tmpdir, "metadata.sqlite3")
                fake = FakeIdMapIndex(dim=faiss_store.VECTOR_DIM)
                fake.ids = [1]
                FakeIdMapIndex.saved = fake
                backend = faiss_store.TurboVecBackend()
                backend.index_dir = tmpdir
                backend.index_file = os.path.join(tmpdir, "index.tvim")
                backend.meta_file = os.path.join(tmpdir, "metadata.jsonl")
                with open(backend.index_file, "wb") as f:
                    f.write(b"fake")

                with self.assertRaisesRegex(RuntimeError, "no SQLite vector metadata"):
                    backend.load_index()
        finally:
            if original_module is None:
                sys.modules.pop("turbovec", None)
            else:
                sys.modules["turbovec"] = original_module
            if original_db is None:
                os.environ.pop("SQLITE_INDEX_PATH", None)
            else:
                os.environ["SQLITE_INDEX_PATH"] = original_db


if __name__ == "__main__":
    unittest.main()
