import os
import sys
import tempfile
import types
import unittest

import atom_catalog
import atom_index
import runtime_paths
from atomizer import assign_parent_chunk_ids


class AtomIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog_path = os.path.join(self.temp.name, "atom_catalog.sqlite")
        self.original_catalog = os.environ.get("ATOM_CATALOG_PATH")
        self.original_enabled = atom_index.is_enabled
        self.original_config = atom_index._config
        self.original_faiss_store = sys.modules.get("faiss_store")
        os.environ["ATOM_CATALOG_PATH"] = self.catalog_path
        atom_index.is_enabled = lambda: True
        atom_index._config = lambda: {"candidate_k": 10, "parent_k": 5}
        self.written = []
        module = types.ModuleType("faiss_store")
        module.add_atom_documents = lambda docs: self.written.extend(docs)
        module.save_atom_index = lambda: None
        module.search_atoms = lambda vector, query, k=5: [
            {"score": 0.91, "metadata": {"atom_id": self.written[0]["metadata"]["atom_id"]}}
        ]
        sys.modules["faiss_store"] = module

    def tearDown(self):
        atom_index.is_enabled = self.original_enabled
        atom_index._config = self.original_config
        if self.original_catalog is None:
            os.environ.pop("ATOM_CATALOG_PATH", None)
        else:
            os.environ["ATOM_CATALOG_PATH"] = self.original_catalog
        if self.original_faiss_store is None:
            sys.modules.pop("faiss_store", None)
        else:
            sys.modules["faiss_store"] = self.original_faiss_store
        self.temp.cleanup()

    def test_indexes_atoms_then_returns_parent_chunks(self):
        docs = [{
            "source": "spec.tsv",
            "content": "제품\t전압\nA\t220V\n정격전압: 220V",
            "metadata": {},
        }]
        assign_parent_chunk_ids(docs)

        count = atom_index.index_documents(docs, lambda texts: [[1.0] * 384 for _ in texts])
        parents = atom_index.search_parent_chunks([1.0] * 384, "A 전압", k=3)

        self.assertGreater(count, 0)
        self.assertEqual(len(self.written), count)
        self.assertEqual(parents[0]["content"], docs[0]["content"])
        self.assertEqual(parents[0]["metadata"]["parent_chunk_id"], docs[0]["metadata"]["parent_chunk_id"])

    def test_reindexing_active_atoms_does_not_append_duplicate_vectors(self):
        docs = [{
            "source": "spec.tsv",
            "content": "제품\t전압\nA\t220V",
            "metadata": {},
        }]
        assign_parent_chunk_ids(docs)

        first_count = atom_index.index_documents(docs, lambda texts: [[1.0] * 384 for _ in texts])
        second_count = atom_index.index_documents(docs, lambda texts: [[1.0] * 384 for _ in texts])

        self.assertEqual(first_count, second_count)
        self.assertEqual(len(self.written), first_count)

    def test_atom_index_paths_are_isolated_from_main_faiss_path(self):
        self.assertNotEqual(
            runtime_paths.runtime_path("atom_index"),
            runtime_paths.runtime_path("faiss_index"),
        )
        self.assertNotEqual(atom_catalog.get_catalog_path(), os.environ.get("SQLITE_INDEX_PATH"))


if __name__ == "__main__":
    unittest.main()
