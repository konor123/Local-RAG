import unittest

from complexity_router import classify_complexity


class ComplexityRouterTests(unittest.TestCase):
    def test_product_property_question_is_factual(self):
        self.assertEqual(
            classify_complexity("OSL-FD-IR3X의 정격전압은?"),
            "factual",
        )

    def test_single_document_fact_question_is_factual(self):
        self.assertEqual(
            classify_complexity("중소기업확인서의 유효기간은?"),
            "factual",
        )

    def test_contextual_product_question_with_history_is_complex(self):
        self.assertEqual(
            classify_complexity("그 제품의 가격은?", "user: 제품 설명\n"),
            "complex",
        )

    def test_contextual_document_question_with_history_is_complex(self):
        self.assertEqual(
            classify_complexity("저 문서의 가격은?", "user: 문서 설명\n"),
            "complex",
        )

    def test_comparison_question_is_complex(self):
        self.assertEqual(
            classify_complexity("두 견적서의 가격과 납기를 비교해줘"),
            "complex",
        )

    def test_file_lookup_is_not_factual_fast_path(self):
        self.assertEqual(
            classify_complexity("불꽃감지기 카탈로그 파일 찾아줘"),
            "complex",
        )

    def test_ambiguous_input_defaults_to_complex(self):
        self.assertEqual(classify_complexity("뭐야"), "complex")
        self.assertEqual(classify_complexity(None), "complex")


if __name__ == "__main__":
    unittest.main()
