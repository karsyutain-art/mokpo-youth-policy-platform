import unittest
from datetime import date

from youth_data_collector import (
    Target,
    extract_age_conditions,
    extract_application_dates,
    normalize,
)
from mysql_policy_repository import MySQLPolicyRepository
from policy_matcher import diagnose_eligibility, eligible_for_policy, policy_tags


class NormalizationTests(unittest.TestCase):
    def test_age_and_application_dates_are_structured(self):
        self.assertEqual(extract_age_conditions("만 19세 ~ 34세 이하"), (19, 34))
        self.assertEqual(extract_age_conditions("문의 061-270-8706"), (None, None))
        self.assertEqual(
            extract_application_dates("2026. 7. 1. ~ 2026. 7. 31."),
            ("2026-07-01", "2026-07-31"),
        )

    def test_attachment_text_refreshes_eligibility_fields(self):
        record = normalize(
            Target("목포시청", "청년 지원사업", "test"),
            {
                "title": "주거비 지원",
                "content": "지원내용: 주거비를 지원합니다.",
                "target_condition": "",
                "period": "2026년 7월 1일 ~ 2026년 7월 31일",
            },
        )
        record["attachment_text"] = "신청자격: 목포시 거주 만 19세~34세 청년"
        from youth_data_collector import refresh_structured_fields

        refresh_structured_fields(record)
        self.assertEqual((record["min_age"], record["max_age"]), (19, 34))
        self.assertIn("목포시 거주", record["residency_condition"])
        self.assertEqual(record["application_end_date"], "2026-07-31")
        self.assertTrue(record["content_hash"])

    def test_mysql_record_key_is_stable_for_same_policy(self):
        record = {"source_site": "목포시청", "category": "청년 지원사업", "original_link": "https://example.test/policy/1"}
        self.assertEqual(MySQLPolicyRepository.record_key(record), MySQLPolicyRepository.record_key(record))

    def test_policy_interest_and_age_matching(self):
        policy = {"category": "청년 지원사업", "title": "청년 주거비 지원", "content": "월세 지원", "qualification_text": "", "min_age": 19, "max_age": 34}
        user = {"birth_date": date(2000, 1, 1), "residency_city": "목포"}
        self.assertIn("주거", policy_tags(policy))
        eligible, reason = eligible_for_policy(user, policy, {"주거"})
        self.assertTrue(eligible)
        self.assertIn("주거", reason)

    def test_eligibility_diagnosis_rejects_explicit_age_mismatch(self):
        user = {"birth_date": date(1980, 1, 1), "residency_city": "목포", "residency_months": 36}
        policy = {"min_age": 19, "max_age": 34, "target_region": "목포", "qualification_text": "목포 거주 만 19~34세", "target_condition": "", "residency_condition": "목포 거주"}
        diagnosis = diagnose_eligibility(user, policy)
        self.assertEqual(diagnosis["overall"], "대상 아님")
        self.assertEqual(next(check for check in diagnosis["checks"] if check["key"] == "age")["status"], "ineligible")

    def test_eligibility_diagnosis_keeps_ambiguous_income_for_review(self):
        user = {"birth_date": date(2000, 1, 1), "residency_city": "목포", "residency_months": 36, "income_band": "중위소득 50~100%"}
        policy = {"min_age": 19, "max_age": 34, "target_region": "전국(목포 포함)", "qualification_text": "중위소득 기준 충족자", "target_condition": "", "residency_condition": ""}
        diagnosis = diagnose_eligibility(user, policy)
        self.assertEqual(diagnosis["overall"], "추가 확인 필요")
        self.assertEqual(next(check for check in diagnosis["checks"] if check["key"] == "income")["status"], "review")


if __name__ == "__main__":
    unittest.main()
