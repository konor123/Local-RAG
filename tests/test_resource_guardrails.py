import os
import tempfile
import unittest


class ResourceGuardrailTests(unittest.TestCase):
    def test_config_defaults_keep_recycle_bin_included(self):
        from config_manager import DEFAULT_CONFIG

        self.assertIn("embedding", DEFAULT_CONFIG)
        self.assertTrue(DEFAULT_CONFIG["embedding"]["enabled"])
        excluded = {name.lower() for name in DEFAULT_CONFIG["search"]["exclude_dirs"]}
        self.assertNotIn("$recycle.bin", excluded)

    def test_dwg_is_processable_embedding_extension(self):
        from background_embedder import BackgroundEmbedder, EMBEDDABLE_EXTENSIONS

        self.assertIn(".dwg", EMBEDDABLE_EXTENSIONS)
        embedder = BackgroundEmbedder()
        with tempfile.NamedTemporaryFile(suffix=".dwg", delete=False) as tmp:
            tmp.write(b"dwg fixture")
            path = tmp.name
        try:
            self.assertTrue(embedder._is_processable_file(path))
        finally:
            os.remove(path)

    def test_large_file_filtered_before_vectorstore_load(self):
        from background_embedder import BackgroundEmbedder

        embedder = BackgroundEmbedder()
        embedder._max_file_size_bytes = 1
        with tempfile.NamedTemporaryFile(suffix=".dwg", delete=False) as tmp:
            tmp.write(b"too large")
            path = tmp.name
        try:
            self.assertFalse(embedder._is_processable_file(path))
        finally:
            os.remove(path)

    def test_no_processable_files_do_not_load_vectorstore(self):
        from background_embedder import BackgroundEmbedder

        embedder = BackgroundEmbedder(idle_sleep=0)
        calls = []

        def fake_load():
            calls.append(True)
            return True

        embedder._lazy_load_vectorstore = fake_load
        embedder._embedding_loop(["C:/fixture/skip.bin"])
        self.assertEqual(calls, [])

    def test_vectorstore_failures_disable_session_after_cap(self):
        import faiss_store
        from background_embedder import BackgroundEmbedder

        original_load_index = faiss_store.load_index
        original_get_backend_name = faiss_store.get_backend_name
        faiss_store.load_index = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        faiss_store.get_backend_name = lambda: "test"
        try:
            embedder = BackgroundEmbedder()
            embedder._max_load_failures = 3
            embedder._retry_backoff_seconds = [1, 1, 1]

            self.assertFalse(embedder._lazy_load_vectorstore())
            embedder._backoff_until = 0
            self.assertFalse(embedder._lazy_load_vectorstore())
            embedder._backoff_until = 0
            self.assertFalse(embedder._lazy_load_vectorstore())

            self.assertTrue(embedder.get_status()["embedding_disabled_for_session"])
            self.assertEqual(embedder.get_status()["consecutive_load_failures"], 3)
        finally:
            faiss_store.load_index = original_load_index
            faiss_store.get_backend_name = original_get_backend_name

    def test_vectorstore_size_guardrail_rejects_oversized_index_before_load(self):
        import faiss_store

        original_load_config = faiss_store.load_config
        faiss_store.load_config = lambda: {
            "embedding": {
                "max_index_mb_for_eager_load": 1,
                "max_metadata_mb_for_eager_load": 1,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            index_file = os.path.join(tmpdir, "index.faiss")
            meta_file = os.path.join(tmpdir, "metadata.jsonl")
            with open(index_file, "wb") as f:
                f.write(b"0" * (2 * 1024 * 1024))
            with open(meta_file, "w", encoding="utf-8") as f:
                f.write("{}\n")
            try:
                with self.assertRaisesRegex(RuntimeError, "too large for eager load"):
                    faiss_store._guard_existing_store_size(index_file, meta_file, "faiss")
            finally:
                faiss_store.load_config = original_load_config


if __name__ == "__main__":
    unittest.main()
