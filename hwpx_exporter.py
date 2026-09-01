"""Create a small, standards-shaped HWPX package without a proprietary SDK."""

from __future__ import annotations

from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


def _paragraph(value: str) -> str:
    safe = escape(value or "")
    return f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0"><hp:t>{safe}</hp:t></hp:run></hp:p>'


def build_hwpx(preparation: dict) -> bytes:
    """Return a text-focused HWPX archive containing the user's current draft."""
    lines = [
        preparation.get("current_policy_title") or preparation.get("policy_title_snapshot") or "신청 준비 문서",
        "신청 준비 요약 (공식 제출용 문서는 아닙니다)",
        f"담당 기관: {preparation.get('organization') or '확인 필요'}",
        "",
        "[신청서 문항]",
    ]
    lines.extend(f"{field['label']}: {field.get('value_text') or ''}" for field in preparation.get("form_fields", []))
    lines.extend(["", "[준비 서류]"])
    lines.extend(f"- {item['title']} ({'확인함' if item.get('user_confirmed') else '확인 필요'})" for item in preparation.get("requirements", []))
    lines.extend(["", "제출 전에는 공식 공고와 원본 신청 양식을 반드시 다시 확인하세요."])
    section = "".join(_paragraph(line) for line in lines)
    section_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section">{section}</hp:sec>'''
    content_hpf = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" version="1.0"><opf:manifest><opf:item id="section0" href="section0.xml" media-type="application/xml"/></opf:manifest><opf:spine><opf:itemref idref="section0"/></opf:spine></opf:package>'''
    container = '''<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="Contents/content.hpf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip", compress_type=ZIP_STORED)
        archive.writestr("version.xml", '<?xml version="1.0" encoding="UTF-8"?><version appVersion="1.0"/>', compress_type=ZIP_DEFLATED)
        archive.writestr("META-INF/container.xml", container, compress_type=ZIP_DEFLATED)
        archive.writestr("Contents/content.hpf", content_hpf, compress_type=ZIP_DEFLATED)
        archive.writestr("Contents/section0.xml", section_xml, compress_type=ZIP_DEFLATED)
    return output.getvalue()
