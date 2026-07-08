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

    def test_embedding_status_distinguishes_idle_monitoring_from_progress_counts(self):
        from background_embedder import BackgroundEmbedder

        embedder = BackgroundEmbedder()
        embedder._update_state(
            state="idle",
            current_file="모니터링 중",
            source_total=10,
            processable_total=0,
            current_index=0,
            remaining_count=0,
        )
        status = embedder.get_status()

        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["source_total"], 10)
        self.assertEqual(status["processable_total"], 0)
        self.assertEqual(status["current_index"], 0)
        self.assertEqual(status["remaining_count"], 0)

    def test_tray_embedding_text_does_not_render_idle_as_zero_progress(self):
        try:
            from native_ui import BackgroundTaskManager
        except ImportError as exc:
            self.skipTest(f"native_ui dependencies unavailable: {exc}")

        class DummyEmbedder:
            def is_running(self):
                return True

            def get_status(self):
                return {
                    "state": "idle",
                    "processed_count": 0,
                    "skip_count": 0,
                    "error_count": 0,
                    "current_file": "모니터링 중",
                    "processable_total": 0,
                    "current_index": 0,
                    "total_processed": 12,
                }

        class ManagerProxy:
            pass

        manager = ManagerProxy()
        manager.embedder = DummyEmbedder()
        text = BackgroundTaskManager.embed_text(manager)

        self.assertIn("대기 파일 없음", text)
        self.assertNotIn("0 OK", text)

    def test_tray_embedding_text_shows_current_index_for_active_work(self):
        try:
            from native_ui import BackgroundTaskManager
        except ImportError as exc:
            self.skipTest(f"native_ui dependencies unavailable: {exc}")

        class DummyEmbedder:
            def is_running(self):
                return True

            def get_status(self):
                return {
                    "state": "embedding",
                    "processed_count": 2,
                    "skip_count": 0,
                    "error_count": 0,
                    "current_file": "manual.pdf",
                    "processable_total": 5,
                    "current_index": 3,
                    "total_processed": 20,
                }

        class ManagerProxy:
            pass

        manager = ManagerProxy()
        manager.embedder = DummyEmbedder()
        text = BackgroundTaskManager.embed_text(manager)

        self.assertIn("3/5", text)
        self.assertIn("manual.pdf", text)

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
