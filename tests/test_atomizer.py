import unittest

from atomizer import assign_parent_chunk_ids, extract_atoms_for_documents, make_parent_chunk_id


class AtomizerTests(unittest.TestCase):
    def test_parent_chunk_id_is_deterministic_and_source_bound(self):
        first = make_parent_chunk_id("C:/docs/spec.pdf", "전압: 220V", 0)
        self.assertEqual(first, make_parent_chunk_id("C:/docs/spec.pdf", "전압: 220V", 0))
        self.assertNotEqual(first, make_parent_chunk_id("C:/docs/other.pdf", "전압: 220V", 0))

    def test_extracts_table_and_spec_atoms_with_parent_link(self):
        docs = [{
            "source": "spec.tsv",
            "content": "제품\t전압\t재질\nA\t220V\t알루미늄\n정격전압: 220V",
            "metadata": {},
        }]
        assign_parent_chunk_ids(docs)

        atoms = extract_atoms_for_documents(docs)

        self.assertGreaterEqual(len(atoms), 2)
        self.assertTrue(all(atom["parent_chunk_id"] == docs[0]["metadata"]["parent_chunk_id"] for atom in atoms))
        self.assertTrue(any("전압: 220V" in atom["content"] for atom in atoms))


if __name__ == "__main__":
    unittest.main()
