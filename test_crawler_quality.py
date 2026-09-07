import tempfile
import unittest
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from youth_data_collector import (
    detect_attachment_extension,
    extract_hwpx_text,
    extract_labeled_fields,
    extract_policy_ids,
    extract_residency_condition,
    field_value,
    is_attachment_link,
    remove_repeated_page_margins,
    validate_raw_record,
)


class CrawlerQualityTests(unittest.TestCase):
    def test_policy_ids_support_javascript_and_normal_links(self):
        html = """
        <button onclick="policyView('123')">첫 정책</button>
        <a href="/www/50?policyId=456">둘째 정책</a>
        <a href="/www/50?policyId=123">중복</a>
        """
        self.assertEqual(extract_policy_ids(html), ["123", "456"])

    def test_labeled_fields_fall_back_to_table_rows(self):
        soup = BeautifulSoup(
            "<table><tr><th>신청 기간</th><td>2026. 9. 1. ~ 9. 30.</td></tr>"
            "<tr><th>지원대상</th><td>목포 거주 청년</td></tr></table>",
            "html.parser",
        )
        fields = extract_labeled_fields(soup)
        self.assertEqual(field_value(fields, "신청기간", "접수기간"), "2026. 9. 1. ~ 9. 30.")
        self.assertEqual(field_value(fields, "지원 대상"), "목포 거주 청년")

    def test_opaque_download_url_is_recognized(self):
        self.assertTrue(is_attachment_link("https://example.test/file_download?fileId=7", "붙임 1"))
        self.assertTrue(is_attachment_link("https://example.test/get?filename=notice.hwpx"))
        self.assertFalse(is_attachment_link("https://example.test/file_download?file_type=all&idx=7", "전체 다운로드"))
        self.assertFalse(is_attachment_link("https://example.test/viewer/viewer.php?url=notice.hwpx", "바로보기"))
        self.assertFalse(is_attachment_link("https://example.test/policy/7", "정책 상세"))

    def test_pdf_signature_wins_when_url_has_no_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "download"
            path.write_bytes(b"%PDF-1.7\nmock")
            self.assertEqual(
                detect_attachment_extension(path, "https://example.test/download?id=1", "application/octet-stream"),
                ".pdf",
            )

    def test_hwpx_paragraphs_keep_boundaries_and_section_number_order(self):
        namespace = "http://www.hancom.co.kr/hwpml/2011/paragraph"
        section_two = f'<hp:section xmlns:hp="{namespace}"><hp:p><hp:run><hp:t>둘째 문단</hp:t></hp:run></hp:p></hp:section>'
        section_ten = f'<hp:section xmlns:hp="{namespace}"><hp:p><hp:run><hp:t>열째 문단</hp:t></hp:run></hp:p></hp:section>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.hwpx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("mimetype", "application/hwp+zip")
                archive.writestr("Contents/section10.xml", section_ten)
                archive.writestr("Contents/section2.xml", section_two)
            self.assertEqual(extract_hwpx_text(path), "둘째 문단\n열째 문단")

    def test_repeated_pdf_headers_and_footers_are_removed(self):
        pages = [
            "목포시 청년정책 공고\n1쪽 본문 첫 줄\n1쪽 본문 둘째 줄\n- 1 -",
            "목포시 청년정책 공고\n2쪽 본문 첫 줄\n2쪽 본문 둘째 줄\n- 1 -",
        ]
        cleaned = remove_repeated_page_margins(pages)
        self.assertNotIn("목포시 청년정책 공고", "\n".join(cleaned))
        self.assertNotIn("- 1 -", "\n".join(cleaned))
        self.assertIn("1쪽 본문 첫 줄", cleaned[0])
        self.assertIn("2쪽 본문 둘째 줄", cleaned[1])

    def test_incomplete_record_is_rejected_before_database_sync(self):
        self.assertEqual(
            validate_raw_record({"title": "", "content": "짧음", "original_link": ""}),
            ["제목 누락", "상세 내용 부족", "원문 URL 누락"],
        )

    def test_residency_summary_prioritizes_eligibility_over_form_noise(self):
        text = (
            "지원대상: 목포시에 계속 거주하는 만 18세~45세 청년\n"
            "제출서류: 주민등록초본 과거 주소 이력 포함\n"
            "신청서 성명 주민등록번호 주소지 연락처"
        )
        result = extract_residency_condition(text)
        self.assertTrue(result.startswith("지원대상: 목포시에 계속 거주"))
        self.assertNotIn("주민등록번호", result)


if __name__ == "__main__":
    unittest.main()
