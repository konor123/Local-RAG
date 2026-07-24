import unittest

from config_manager import DEFAULT_CONFIG


class ModelConfigTests(unittest.TestCase):
    def test_qwen_is_adviser_and_fallback_without_ax(self):
        local = DEFAULT_CONFIG["ai_provider"]["local"]

        self.assertEqual(local["adviser_model"], "qwen3.5:4b")
        self.assertEqual(local["fallback_agent_model"], "qwen3.5:4b")
        self.assertIn("qwen3.5:4b", local["available_models"])
        self.assertTrue(local["model_capabilities"]["qwen3.5:4b"]["tools"])

    def test_ax_settings_are_migrated_to_qwen(self):
        import config_manager

        retired_model = "cookieshake/a.x-4.0-light-imatrix:q4_k_m"
        config, changed = config_manager._enforce_internal_defaults({
            "ai_provider": {
                "local": {
                    "adviser_model": retired_model,
                    "fallback_agent_model": retired_model,
                    "available_models": ["qwen3.5:4b", retired_model],
                    "model_capabilities": {
                        "qwen3.5:4b": {"tools": True},
                        "cookieshake/a.x-4.0-light-imatrix": {"tools": True},
                    },
                },
            },
        })

        local = config["ai_provider"]["local"]
        self.assertTrue(changed)
        self.assertEqual(local["adviser_model"], "qwen3.5:4b")
        self.assertEqual(local["fallback_agent_model"], "qwen3.5:4b")
        self.assertEqual(local["available_models"], ["qwen3.5:4b"])
        self.assertNotIn("cookieshake/a.x-4.0-light-imatrix", local["model_capabilities"])
        self.assertFalse(any("a.x-4.0-light-imatrix" in key for key in local["available_models"]))
        self.assertFalse(any("a.x-4.0-light-imatrix" in key for key in local["model_capabilities"]))


if __name__ == "__main__":
    unittest.main()
