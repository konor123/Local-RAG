import os
import tempfile
import unittest
from contextlib import closing


class SQLiteIndexTests(unittest.TestCase):
    def test_init_upsert_and_fts_search(self):
        import sqlite_index

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metadata.sqlite3")
            source = os.path.join(tmpdir, "manual.txt")
            with open(source, "w", encoding="utf-8") as f:
                f.write("fixture")

            sqlite_index.init_db(db_path)
            count = sqlite_index.upsert_chunks(
                source,
                [
                    {"content": "fire alarm installation guide", "metadata": {"page": 1}},
                    {"content": "pump maintenance checklist", "metadata": {"page": 2}},
                ],
                db_path=db_path,
            )
            results = sqlite_index.search_fts("alarm", k=5, db_path=db_path)

        self.assertEqual(count, 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], source)
        self.assertEqual(results[0]["metadata"]["page"], 1)
        self.assertEqual(results[0]["source_engine"], "sqlite_fts5")

    def test_record_status_is_queryable_document_state(self):
        import sqlite3
        import sqlite_index

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metadata.sqlite3")
            source = os.path.join(tmpdir, "locked.xlsx")
            sqlite_index.record_status(source, "empty_or_encrypted", "password", db_path=db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT status, error_detail FROM documents WHERE source_path = ?",
                    (source,),
                ).fetchone()

        self.assertEqual(row[0], "empty_or_encrypted")
        self.assertEqual(row[1], "password")

    def test_metadata_index_defaults_to_enabled(self):
        import config_manager

        metadata = config_manager.DEFAULT_CONFIG["metadata_index"]

        self.assertTrue(metadata["enabled"])
        self.assertTrue(metadata["fts_search_enabled"])

    def test_metadata_index_disabled_config_is_migrated_to_enabled(self):
        import config_manager

        config, changed = config_manager._enforce_internal_defaults({
            "metadata_index": {"enabled": False, "fts_search_enabled": False, "path": "custom.sqlite3"},
            "search": {},
        })

        self.assertTrue(changed)
        self.assertTrue(config["metadata_index"]["enabled"])
        self.assertTrue(config["metadata_index"]["fts_search_enabled"])
        self.assertEqual(config["metadata_index"]["path"], "custom.sqlite3")

    def test_tools_metadata_search_is_disabled_when_key_absent_for_compatibility(self):
        import tools
        import config_manager

        original_load_config = config_manager.load_config
        config_manager.load_config = lambda: {}
        try:
            result = tools.search_metadata_content("alarm", k=3)
        finally:
            config_manager.load_config = original_load_config

        self.assertTrue(result["disabled"])
        self.assertEqual(result["count"], 0)

    def test_tools_metadata_search_uses_configured_fts_sidecar(self):
        import tools
        import sqlite_index
        import config_manager

        original_load_config = config_manager.load_config
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metadata.sqlite3")
            source = os.path.join(tmpdir, "manual.txt")
            with open(source, "w", encoding="utf-8") as f:
                f.write("fixture")
            sqlite_index.upsert_chunks(
                source,
                [{"content": "sprinkler alarm checklist", "metadata": {"page": 4}}],
                db_path=db_path,
            )
            config_manager.load_config = lambda: {
                "metadata_index": {
                    "enabled": True,
                    "fts_search_enabled": True,
                    "path": db_path,
                }
            }
            try:
                result = tools.search_metadata_content("sprinkler", k=3)
            finally:
                config_manager.load_config = original_load_config

        self.assertEqual(result["source"], "sqlite_fts5")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["metadata"]["page"], 4)

    def test_stats_and_wal_checkpoint(self):
        import sqlite_index

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metadata.sqlite3")
            source = os.path.join(tmpdir, "manual.txt")
            with open(source, "w", encoding="utf-8") as f:
                f.write("fixture")
            sqlite_index.upsert_chunks(
                source,
                [{"content": "alarm guide", "metadata": {}}, {"content": "pump guide", "metadata": {}}],
                db_path=db_path,
            )

            stats = sqlite_index.get_stats(db_path)
            checkpoint = sqlite_index.checkpoint_wal(db_path)

        self.assertEqual(stats["documents"], 1)
        self.assertEqual(stats["chunks"], 2)
        self.assertEqual(stats["fts_chunks"], 2)
        self.assertIn("busy", checkpoint)

    def test_vector_id_allocation_and_metadata_lookup(self):
        import sqlite_index

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metadata.sqlite3")
            source = os.path.join(tmpdir, "manual.txt")
            with open(source, "w", encoding="utf-8") as f:
                f.write("fixture")

            ids = sqlite_index.allocate_vector_ids(2, db_path=db_path)
            sqlite_index.upsert_chunks(
                source,
                [
                    {"content": "alpha", "metadata": {"page": 1}, "vector_id": ids[0]},
                    {"content": "bravo", "metadata": {"page": 2}, "vector_id": ids[1]},
                ],
                db_path=db_path,
            )
            rows = sqlite_index.get_vector_metadata_by_ids([ids[1], ids[0]], db_path=db_path)

        self.assertEqual(ids, [1, 2])
        self.assertEqual(rows[ids[0]]["content"], "alpha")
        self.assertEqual(rows[ids[1]]["metadata"]["page"], 2)

    def test_allocator_advances_past_existing_vector_ids(self):
        import sqlite_index

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metadata.sqlite3")
            source = os.path.join(tmpdir, "manual.txt")
            with open(source, "w", encoding="utf-8") as f:
                f.write("fixture")

            sqlite_index.upsert_chunks(
                source,
                [{"content": "legacy", "metadata": {}, "vector_id": 41}],
                db_path=db_path,
            )
            ids = sqlite_index.allocate_vector_ids(2, db_path=db_path)

        self.assertEqual(ids, [42, 43])


if __name__ == "__main__":
    unittest.main()
