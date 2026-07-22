import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class RebuildFaissIndexTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("rebuild_faiss_index", None)
        sys.modules.pop("background_embedder", None)

    def test_resolve_metadata_falls_back_to_newest_backup(self):
        import rebuild_faiss_index

        with tempfile.TemporaryDirectory() as tmpdir:
            older = os.path.join(tmpdir, "metadata.jsonl.corrupt.20260101_000000")
            newer = os.path.join(tmpdir, "metadata.jsonl.corrupt.20260102_000000")
            open(older, "w", encoding="utf-8").write("{}\n")
            open(newer, "w", encoding="utf-8").write("{}\n")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))

            self.assertEqual(rebuild_faiss_index._resolve_metadata_file(tmpdir), newer)

    def test_rebuild_embeds_metadata_and_backs_up_existing_index(self):
        import rebuild_faiss_index

        class FakeIndex:
            def __init__(self, dim):
                self.dim = dim
                self.ntotal = 0

            def add(self, vectors):
                self.ntotal += len(vectors)

        class FakeSentenceTransformer:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def encode(self, texts, **kwargs):
                return [[1.0] * 384 for _ in texts]

        def write_index(index, path):
            with open(path, "wb") as handle:
                handle.write(f"vectors={index.ntotal}".encode("ascii"))

        fake_faiss = types.SimpleNamespace(IndexFlatIP=FakeIndex, write_index=write_index)
        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer

        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = os.path.join(tmpdir, "index.faiss")
            metadata_file = os.path.join(tmpdir, "metadata.jsonl")
            with open(index_file, "wb") as handle:
                handle.write(b"corrupt index")
            with open(metadata_file, "w", encoding="utf-8") as handle:
                for content in ("first chunk", "second chunk"):
                    handle.write(json.dumps({"content": content, "source": "fixture.pdf", "metadata": {}}) + "\n")

            with patch.dict(sys.modules, {
                "faiss": fake_faiss,
                "sentence_transformers": fake_sentence_transformers,
            }):
                count = rebuild_faiss_index.rebuild(metadata_file, index_file, batch_size=1, checkpoint_every=1)

            self.assertEqual(count, 2)
            self.assertEqual(open(index_file, "rb").read(), b"vectors=2")
            self.assertTrue(any(name.startswith("index.faiss.corrupt.") for name in os.listdir(tmpdir)))
            self.assertEqual(sum(1 for _ in open(metadata_file, encoding="utf-8")), 2)


if __name__ == "__main__":
    unittest.main()
