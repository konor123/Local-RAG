import unittest


class AtomBackendTests(unittest.TestCase):
    def test_failed_atom_backend_load_is_retried(self):
        import faiss_store

        original_backend = faiss_store.FaissBackend
        original_cached = faiss_store._atom_backend
        instances = []

        class FakeBackend:
            def __init__(self, index_dir):
                self.index_dir = index_dir
                self.load_attempt = len(instances)
                instances.append(self)

            def load_index(self):
                if self.load_attempt == 0:
                    raise RuntimeError("initial atom load failed")

        faiss_store.FaissBackend = FakeBackend
        faiss_store._atom_backend = None
        try:
            with self.assertRaisesRegex(RuntimeError, "initial atom load failed"):
                faiss_store._get_atom_backend()

            self.assertIsNone(faiss_store._atom_backend)
            backend = faiss_store._get_atom_backend()
            self.assertIs(backend, instances[1])
            self.assertIs(faiss_store._atom_backend, backend)
        finally:
            faiss_store.FaissBackend = original_backend
            faiss_store._atom_backend = original_cached


if __name__ == "__main__":
    unittest.main()
