import json
import os
import unittest
from base64 import b64encode
from datetime import datetime

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from application_extractor import extract_requirement_candidates
from application_form_extractor import extract_form_field_candidates
from backend.main import app, default_requirements
from hwpx_exporter import build_hwpx
from mysql_policy_repository import MySQLPolicyRepository
from youth_data_collector import load_local_env


class PreparationUnitTests(unittest.TestCase):
    def test_default_checklist_is_conservative_and_uses_policy_evidence(self):
        items = default_requirements(
            {
                "qualification_text": "목포 거주 만 19~34세",
                "application_method": "온라인 신청",
                "attachment_links": "https://example.test/form.pdf",
                "attachment_status": "PDF 확인 가능",
            }
        )
        self.assertGreaterEqual(len(items), 4)
        self.assertIn("목포 거주", items[0]["evidence_text"])
        self.assertTrue(any("첨부파일" in item["title"] for item in items))
        self.assertTrue(any("직접 추가" in item["evidence_text"] for item in items))

    def test_attachment_document_lines_become_evidence_backed_candidates(self):
        candidates = extract_requirement_candidates(
            {
                "attachment_text": """제출서류
                1. 주민등록초본(주소 변동 포함) 1부
                2. 건강보험료 납부확인서(PDF)
                3. 해당자에 한해 재직증명서 제출""",
                "content": "지원 대상과 일정은 공고문을 확인하세요.",
            }
        )
        self.assertEqual(len(candidates), 3)
        self.assertTrue(candidates[0]["is_required"])
        self.assertEqual(candidates[1]["submission_format"], "PDF")
        self.assertFalse(candidates[2]["is_required"])
        self.assertEqual(candidates[0]["source_type"], "extracted")

    def test_form_labels_and_hwpx_are_evidence_based_and_exportable(self):
        candidates = extract_form_field_candidates({"attachment_text": "신청서 작성 내용\n1. 성명: \n2. 지원동기: \n3. 주소: "})
        self.assertEqual([item["label"] for item in candidates], ["성명", "지원동기", "주소"])
        self.assertEqual(candidates[1]["field_type"], "textarea")
        document = build_hwpx({"current_policy_title": "목포 청년 지원", "form_fields": [{"label": "성명", "value_text": "홍길동"}], "requirements": []})
        self.assertTrue(document.startswith(b"PK"))
        self.assertIn(b"Contents/section0.xml", document)


@unittest.skipUnless(os.getenv("RUN_DB_INTEGRATION_TESTS") == "1", "DB 통합 테스트는 명시적으로 실행합니다.")
class PreparationApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_local_env()
        cls.repository = MySQLPolicyRepository()
        cls.connection = cls.repository.connect()
        cls.repository.initialize(cls.connection)
        cursor = cls.connection.cursor()
        cursor.execute("SELECT id FROM policy_records ORDER BY id LIMIT 1")
        policy = cursor.fetchone()
        if policy is None:
            raise unittest.SkipTest("테스트할 정책 데이터가 없습니다.")
        cls.policy_id = policy[0]
        now = datetime.now().replace(microsecond=0)
        cursor.execute(
            "INSERT INTO user_profiles (display_name, legal_name, residency_city, created_at, updated_at) VALUES (%s, %s, '목포', %s, %s)",
            ("신청준비 통합테스트", "테스트 사용자", now, now),
        )
        cls.user_id = cursor.lastrowid
        cls.connection.commit()
        secret = os.getenv("FLASK_SECRET_KEY", "change-me")
        session_data = b64encode(json.dumps({"user_id": cls.user_id}).encode("utf-8"))
        cls.session_cookie = TimestampSigner(str(secret)).sign(session_data).decode("utf-8")

    @classmethod
    def tearDownClass(cls):
        cursor = cls.connection.cursor()
        cursor.execute("DELETE FROM user_profiles WHERE id = %s", (cls.user_id,))
        cls.connection.commit()
        cls.connection.close()

    def test_preparation_checklist_lifecycle_and_ownership(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/preparations").status_code, 401)
            client.cookies.set("session", self.session_cookie)
            created = client.post(f"/api/policies/{self.policy_id}/preparations")
            self.assertEqual(created.status_code, 200, created.text)
            preparation = created.json()
            self.assertGreaterEqual(preparation["total_count"], 3)

            duplicate = client.post(f"/api/policies/{self.policy_id}/preparations")
            self.assertEqual(duplicate.json()["id"], preparation["id"])

            extracted = client.post(f"/api/preparations/{preparation['id']}/extract")
            self.assertEqual(extracted.status_code, 200, extracted.text)
            self.assertIn("added", extracted.json())

            form_field = client.post(
                f"/api/preparations/{preparation['id']}/form-fields",
                json={"label": "성명", "autofill_profile_key": "legal_name", "autofill_consent": True},
            )
            self.assertEqual(form_field.status_code, 200, form_field.text)
            self.assertEqual(form_field.json()["value_text"], "테스트 사용자")

            exported = client.get(f"/api/preparations/{preparation['id']}/export/hwpx")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertTrue(exported.content.startswith(b"PK"))

            added = client.post(
                f"/api/preparations/{preparation['id']}/requirements",
                json={"title": "주민등록초본", "is_required": True},
            )
            self.assertEqual(added.status_code, 200, added.text)
            updated = client.put(
                f"/api/preparations/{preparation['id']}/requirements/{added.json()['id']}",
                json={"preparation_status": "completed", "user_confirmed": True},
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["preparation_status"], "completed")

            deleted = client.delete(f"/api/preparations/{preparation['id']}")
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(client.get(f"/api/preparations/{preparation['id']}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
