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

    def test_tools_metadata_search_is_disabled_by_default(self):
        import tools

        result = tools.search_metadata_content("alarm", k=3)

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


if __name__ == "__main__":
    unittest.main()
