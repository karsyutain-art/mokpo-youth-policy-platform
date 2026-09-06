import unittest

from backend.main import validate_preparation


class PreparationValidationTests(unittest.TestCase):
    def test_required_items_and_formats_block_export(self):
        result = validate_preparation({
            "policy_changed": False,
            "source_confirmed": False,
            "requirements": [{"id": 1, "title": "주민등록초본", "is_required": True, "preparation_status": "not_started", "user_confirmed": False}],
            "form_fields": [
                {"id": 2, "label": "연락처", "is_required": True, "value_text": "010-12", "max_length": 20, "field_type": "text", "user_confirmed": False},
                {"id": 3, "label": "지원동기", "is_required": True, "value_text": "", "max_length": 100, "field_type": "textarea", "user_confirmed": False},
            ],
        })
        self.assertFalse(result["ready"])
        self.assertGreaterEqual(len(result["errors"]), 4)

    def test_completed_confirmed_payload_is_ready(self):
        result = validate_preparation({
            "policy_changed": False,
            "source_confirmed": True,
            "requirements": [{"id": 1, "title": "신분증", "is_required": True, "preparation_status": "completed", "user_confirmed": True}],
            "form_fields": [{"id": 2, "label": "지원동기", "is_required": True, "value_text": "성실히 준비했습니다.", "max_length": 100, "field_type": "textarea", "user_confirmed": True}],
        })
        self.assertTrue(result["ready"])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
