import unittest
from hashlib import sha256
from permit_capture.html_parser import parse_table
from permit_capture.record_normalizer import normalize_rows
from permit_capture.schema import ParseError
from permit_capture.evidence import bind_row_evidence, validate_evidence_ref, EvidenceError
from permit_capture.page_store import StoredPage
from test_capture_flow import row, table


class ParserTests(unittest.TestCase):
    def test_footnote_and_empty(self):
        body = ('<tr><td>P-1<span class="note footnote">99</span></td>'
                '<td>2024-02-29</td><td>approved</td><td>NORTH</td>'
                '<td><span class="footnote">说明</span></td></tr>')
        html = table('<aside class="footnote">页脚<tr>忽略</tr></aside>' + body)
        parsed = parse_table(html, '#permits')
        result = normalize_rows([(1, parsed.rows)])
        self.assertEqual(result.errors, ())
        self.assertEqual(result.records[0].fields, ('P-1', '2024-02-29', 'approved', 'NORTH', None))
        self.assertEqual(len(parsed.rows[0].cells), 5)
        self.assertEqual([c.column_no for c in parsed.rows[0].cells], [1, 2, 3, 4, 5])
        self.assertEqual(parsed.rows[0].row_no, 1)
        raw = html.encode()
        source = StoredPage(1, 'https://example.test/', sha256(raw).hexdigest(), raw)
        ref = bind_row_evidence(source, parsed.rows[0])
        self.assertEqual(raw[ref.start:ref.end], body.encode())
        self.assertTrue(validate_evidence_ref(ref, raw, source.sha256))
        with self.assertRaises(EvidenceError):
            validate_evidence_ref(ref, raw.replace(b'99', b'98'), source.sha256)

    def test_entities_nfc_and_unit_spans(self):
        html = table(row(['P-1', '2024-01-01', 'approved', 'NORTH', 'placeholder']))
        html = html.replace('placeholder', 'e&#769; &amp;lt;').replace(
            '<td>', '<td rowspan="1" colspan="1">')
        parsed = parse_table(html, '#permits')
        result = normalize_rows([(1, parsed.rows)])
        self.assertEqual(result.records[0].fields[-1], 'é &lt;')
        plain = parse_table(html.replace(' rowspan="1" colspan="1"', ''), '#permits')
        self.assertEqual([c.text for c in parsed.rows[0].cells],
                         [c.text for c in plain.rows[0].cells])

    def test_empty_required_keeps_column(self):
        html = table(row(['P-1', '2024-01-01', '', 'EAST', 'Alice']))
        parsed = parse_table(html, '#permits')
        result = normalize_rows([(1, parsed.rows)])
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].column_no, 3)
        self.assertEqual(result.errors[0].code, 'REQUIRED')
        self.assertEqual(parsed.rows[0].cells[3].text, 'EAST')

    def test_structural_rejections(self):
        good = table(row(['P-1', '2024-01-01', 'approved', 'NORTH', '']))
        cases = [good.replace('id="permits"', 'id="other"'), good + good,
                 good.replace('<td>P-1</td>', '<td><table></table></td>'),
                 good.replace('</td>', '</th>', 1),
                 good.replace('<td>P-1</td>', '<td colspan="0">P-1</td>'),
                 good.replace('<td>P-1</td>', '<td rowspan="2">P-1</td>'),
                 good.replace('<td>P-1</td>', '<td colspan="x">P-1</td>'),
                 good.replace('<td>P-1</td>', ''),
                 good.replace('<th>permit_no</th>', '<th>district</th>')]
        for html in cases:
            with self.subTest(html=html):
                with self.assertRaises(ParseError):
                    parse_table(html, '#permits')

    def test_v1_rejects_body_header_and_nonunit_colspan(self):
        repeated = row(['permit_no', 'issued_on', 'status', 'district', 'applicant'])
        with self.assertRaises(ParseError):
            parse_table(table(repeated), '#permits')
        html = table(row(['P-1', '2024-01-01', 'approved', 'NORTH', '']))
        with self.assertRaises(ParseError):
            parse_table(html.replace('<td>', '<td colspan="2">', 1), '#permits')

    def test_explicit_empty_key_and_large_span(self):
        html = table(row(['P-1', '2024-01-01', 'approved', 'NORTH', '']))
        parsed = parse_table(html.replace('<tr><td>', '<tr data-row-key><td>'), '#permits')
        result = normalize_rows([(1, parsed.rows)])
        self.assertTrue(result.errors)
        self.assertEqual(result.errors[0].code, 'ROW_KEY')
        with self.assertRaises(ParseError):
            parse_table(html.replace('<td>', '<td rowspan="' + '9' * 5000 + '">', 1), '#permits')
