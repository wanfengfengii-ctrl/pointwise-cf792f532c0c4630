"""Business regressions for the V2 cross-page capture contract."""
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from permit_capture.intake import IntakeService
from permit_capture.review import ReviewService
from permit_capture.source_manifest import manifest_from_object
from tablecapture import Capture, CaptureError
from test_capture_flow import table as v1_table

COLUMNS = ('permit_no', 'issued_on', 'status', 'district', 'applicant')
HEADER = '<tr>' + ''.join('<th>' + c + '</th>' for c in COLUMNS) + '</tr>'


def page(body):
    return '<p>许可公示</p><table id="permits"><thead>' + HEADER + \
        '</thead><tbody>' + body + '</tbody></table>'


def declaration(batch_id, bodies, prefix='https://permits.example.test/span'):
    return {'batch_id': batch_id, 'table_selector': '#permits',
            'pages': [{'page_no': i, 'url': prefix + '?p=' + str(i), 'html': page(body)}
                      for i, body in enumerate(bodies, 1)]}


class CrossPageCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crosspage-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def capture(self, obj, workdir=None):
        source = manifest_from_object(obj)
        cap = Capture(workdir or self.root, source)
        for item in source.pages:
            cap.ingest_page(item.url, item.html, item.page_no)
        return cap, source

    def publish(self, obj, workdir=None):
        cap, _ = self.capture(obj, workdir)
        identifier = cap.finish()
        return Capture(workdir or self.root).read(obj['batch_id']), identifier


class GridTests(CrossPageCase):
    def test_rowspan_crosses_two_pages_with_headers_and_footnote(self):
        first = ('<tr data-row-key="K"><td rowspan="3">p-1</td><td rowspan="3">2024-02-29</td>'
                 '<td rowspan="3">approved</td><td rowspan="3" colspan="2">NORTH</td></tr>')
        second = (HEADER + '<aside class="footnote"><tr><td>忽略</td></tr></aside>'
                  + '<tr data-row-key="K"></tr><tr data-row-key="K"></tr>')
        result, _ = self.publish(declaration('cross-three', [first, second]))
        self.assertEqual(len(result['records']), 1)
        record = result['records'][0]
        self.assertEqual(record['fields'], ['P-1', '2024-02-29', 'approved', 'NORTH', 'NORTH'])
        self.assertEqual([(r['page_no'], r['row_no']) for r in record['member_rows']],
                         [(1, 1), (2, 1), (2, 2)])
        for refs in record['field_sources']:
            self.assertEqual([(r['page_no'], r['row_no']) for r in refs], [(1, 1)])

    def test_empty_page_does_not_consume_rowspan(self):
        first = ('<tr data-row-key="K"><td rowspan="2">P-2</td><td rowspan="2">2024-02-29</td>'
                 '<td>approved</td><td>NORTH</td><td>甲</td></tr>')
        empty = HEADER
        continuation = ('<tr data-row-key="K"><td>approved</td><td>NORTH</td>'
                        '<td>甲</td></tr>')
        result, _ = self.publish(declaration('empty-middle', [first, empty, continuation]))
        record = result['records'][0]
        self.assertEqual([(r['page_no'], r['row_no']) for r in record['member_rows']],
                         [(1, 1), (3, 1)])
        self.assertEqual([ref['page_no'] for ref in record['field_sources'][0]], [1])

    def test_fully_inherited_empty_tr_is_a_member(self):
        body = ('<tr data-row-key="K"><td rowspan="2">P-2</td><td rowspan="2">2024-02-29</td>'
                '<td rowspan="2">approved</td><td rowspan="2">NORTH</td>'
                '<td rowspan="2">甲</td></tr><tr data-row-key="K"></tr>')
        result, _ = self.publish(declaration('empty-tr', [body]))
        record = result['records'][0]
        self.assertEqual([(r['page_no'], r['row_no']) for r in record['member_rows']],
                         [(1, 1), (1, 2)])
        empty = record['member_rows'][1]
        self.assertEqual(empty['start'] < empty['end'], True)
        raw = page(body).encode()
        self.assertEqual(raw[empty['start']:empty['end']], b'<tr data-row-key="K"></tr>')

    def test_colspan_is_interpreted_per_column(self):
        body = ('<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td>'
                '<td>approved</td><td colspan="2">NORTH</td></tr>')
        result, _ = self.publish(declaration('colspan', [body]))
        record = result['records'][0]
        self.assertEqual(record['fields'], ['P-1', '2024-02-29', 'approved', 'NORTH', 'NORTH'])
        self.assertEqual(len(record['field_sources'][3]), 1)
        self.assertEqual(record['field_sources'][3], record['field_sources'][4])
        self.assertEqual(record['field_sources'][3][0]['column_no'], 4)

    def test_same_value_in_distinct_cells_keeps_both_sources(self):
        body = ('<tr data-row-key="A"><td>A-1</td><td>2024-02-29</td><td>approved</td>'
                '<td>NORTH</td><td>王</td></tr>'
                '<tr data-row-key="B"><td>B-1</td><td>2024-03-01</td><td>pending</td>'
                '<td>NORTH</td><td>李</td></tr>')
        result, _ = self.publish(declaration('dup-value', [body]))
        self.assertEqual(len(result['records']), 2)
        first, second = result['records']
        self.assertNotEqual(first['field_sources'][3], second['field_sources'][3])
        self.assertEqual(first['field_sources'][3][0]['fragment_sha256'],
                         second['field_sources'][3][0]['fragment_sha256'])
        self.assertNotEqual(first['field_sources'][3][0]['start'],
                            second['field_sources'][3][0]['start'])

    def test_shared_cell_different_keys_stay_separate(self):
        first = ('<tr data-row-key="A"><td>A-1</td><td>2024-02-29</td><td>approved</td>'
                 '<td rowspan="2" colspan="2">NORTH</td></tr>')
        second = ('<tr data-row-key="B"><td>B-1</td><td>2024-03-01</td>'
                  '<td>pending</td></tr>')
        result, _ = self.publish(declaration('shared-two', [first, second]))
        self.assertEqual([r['row_key'] for r in result['records']], ['A', 'B'])
        for record in result['records']:
            for column in (3, 4):
                refs = record['field_sources'][column]
                self.assertEqual([(r['page_no'], r['row_no'], r['column_no']) for r in refs],
                                 [(1, 1, 4)])
        self.assertNotEqual(result['records'][0]['field_sources'][0],
                            result['records'][1]['field_sources'][0])

    def test_colspan_text_uses_each_column_rule(self):
        # One physical cell spans permit_no and issued_on; it is normalized
        # twice, yielding a valid permit and a date error at column 2.
        body = ('<tr data-row-key="K"><td colspan="2">p-1</td><td>approved</td>'
                '<td>NORTH</td><td>甲</td></tr>')
        cap, _ = self.capture(declaration('colspan-rules', [body]))
        with self.assertRaises(CaptureError) as raised:
            cap.finish()
        self.assertIn('column 2', str(raised.exception))
        self.assertNotIn('column 1', str(raised.exception))
        # The same cell across status/district is a valid status but not a
        # district value; the error names only column 4.
        body = ('<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td>'
                '<td colspan="2">approved</td><td>甲</td></tr>')
        cap, _ = self.capture(declaration('colspan-district', [body]))
        with self.assertRaises(CaptureError) as raised:
            cap.finish()
        self.assertIn('column 4', str(raised.exception))
        self.assertNotIn('column 3', str(raised.exception))

    def test_records_order_by_first_member(self):
        first = ('<tr data-row-key="Z"><td>Z-1</td><td>2024-02-29</td><td>approved</td>'
                 '<td>NORTH</td><td>甲</td></tr>')
        second = ('<tr data-row-key="A"><td rowspan="2">A-1</td>'
                  '<td rowspan="2">2024-03-01</td><td rowspan="2">pending</td>'
                  '<td rowspan="2">EAST</td><td rowspan="2">乙</td></tr>'
                  '<tr data-row-key="A"></tr>')
        result, _ = self.publish(declaration('ordering', [first, second]))
        self.assertEqual([r['row_key'] for r in result['records']], ['Z', 'A'])
        self.assertEqual([(r['page_no'], r['row_no'])
                          for r in result['records'][1]['member_rows']],
                         [(2, 1), (2, 2)])

    def test_byte_coordinates_and_fragment_digests(self):
        first = ('<tr data-row-key="K"><td rowspan="2">p-1</td>'
                 '<td rowspan="2">2024-02-29</td><td>approved</td>'
                 '<td rowspan="2">NORTH</td><td>李明</td></tr>')
        second = ('<tr data-row-key="K"><td> approved </td>'
                  '<td><span class="footnote">注</span>李明</td></tr>')
        obj = declaration('bytes', [first, second])
        result, _ = self.publish(obj)
        record = result['records'][0]
        raw = [p['html'].encode('utf-8') for p in obj['pages']]
        for ref in record['member_rows']:
            data = raw[ref['page_no'] - 1]
            fragment = data[ref['start']:ref['end']]
            self.assertTrue(fragment.startswith(b'<tr data-row-key="K">'))
            self.assertTrue(fragment.endswith(b'</tr>'))
            self.assertEqual(ref['page_sha256'], sha256(data).hexdigest())
            self.assertEqual(ref['fragment_sha256'], sha256(fragment).hexdigest())
        for refs in record['field_sources']:
            for ref in refs:
                data = raw[ref['page_no'] - 1]
                fragment = data[ref['start']:ref['end']]
                self.assertIn(fragment[:3], (b'<td', b'<th'))
                self.assertTrue(fragment.endswith(b'</td>') or fragment.endswith(b'</th>'))
                self.assertEqual(ref['page_sha256'], sha256(data).hexdigest())
                self.assertEqual(ref['fragment_sha256'], sha256(fragment).hexdigest())
        status_sources = record['field_sources'][2]
        self.assertEqual([(r['page_no'], r['row_no']) for r in status_sources],
                         [(1, 1), (2, 1)])
        applicant_source = record['field_sources'][4][1]
        applicant_raw = raw[1][applicant_source['start']:applicant_source['end']]
        self.assertIn(b'footnote', applicant_raw)
        self.assertEqual(applicant_source['fragment_sha256'],
                         sha256(applicant_raw).hexdigest())


class RejectionTests(CrossPageCase):
    def assert_rejected(self, obj, workdir=None):
        root = workdir or (self.root / obj['batch_id'])
        cap, _ = self.capture(obj, root)
        with self.assertRaises(CaptureError):
            cap.finish()
        self.assertIsNone(Capture(root).archive.find_batch(obj['batch_id']))
        with self.assertRaises(CaptureError):
            Capture(root).read(obj['batch_id'])
        self.assertFalse(Capture(root).verify(obj['batch_id']).ok)
        return root

    def test_unclosed_rowspan_beyond_last_page(self):
        body = ('<tr data-row-key="K"><td rowspan="2">p-1</td><td rowspan="2">2024-02-29</td>'
                '<td rowspan="2">approved</td><td rowspan="2" colspan="2">NORTH</td></tr>')
        self.assert_rejected(declaration('unclosed', [body]))

    def test_unclosed_rowspan_with_complete_text(self):
        first = ('<tr data-row-key="K"><td rowspan="2">p-1</td>'
                 '<td>2024-02-29</td><td>approved</td><td>NORTH</td><td>甲</td></tr>')
        second = ('<tr data-row-key="T"><td>p-2</td><td>2024-03-01</td><td>pending</td>'
                  '<td>EAST</td><td>乙</td></tr>')
        self.assert_rejected(declaration('unclosed-text', [first, second]))

    def test_span_overlap_is_not_relocated(self):
        body = ('<tr data-row-key="K"><td rowspan="2">p-1</td><td>b</td>'
                '<td rowspan="2">approved</td><td>NORTH</td><td>甲</td></tr>'
                '<tr data-row-key="L"><td colspan="2">x</td><td>y</td><td>z</td></tr>')
        self.assert_rejected(declaration('overlap', [body]))

    def test_colspan_beyond_five_columns(self):
        body = ('<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
                '<td>NORTH</td><td colspan="2">甲</td></tr>')
        self.assert_rejected(declaration('wide', [body]))

    def test_row_does_not_cover_five_columns(self):
        body = ('<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td>'
                '<td>approved</td><td>NORTH</td></tr>')
        self.assert_rejected(declaration('short-row', [body]))

    def test_repeated_key_without_inheritance(self):
        body = ('<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
                '<td>NORTH</td><td>甲</td></tr>'
                '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
                '<td>NORTH</td><td>甲</td></tr>')
        self.assert_rejected(declaration('rekey', [body]))

    def test_non_contiguous_repeated_key(self):
        first = ('<tr data-row-key="K"><td rowspan="3">p-1</td><td rowspan="3">2024-02-29</td>'
                 '<td>approved</td><td rowspan="3">NORTH</td><td>甲</td></tr>')
        body = (first +
                '<tr data-row-key="X"><td>pending</td><td>乙</td></tr>'
                '<tr data-row-key="K"><td>approved</td><td>甲</td></tr>')
        self.assert_rejected(declaration('noncontig', [body]))

    def test_continuation_field_change(self):
        first = ('<tr data-row-key="K"><td rowspan="2">p-1</td>'
                 '<td rowspan="2">2024-02-29</td><td>approved</td>'
                 '<td rowspan="2">NORTH</td><td>甲</td></tr>')
        second = ('<tr data-row-key="K"><td>revoked</td><td>甲</td></tr>')
        self.assert_rejected(declaration('changed', [first, second]))

    def test_bad_structure_and_field_errors(self):
        self.assert_rejected(declaration('bad-structure', ['<tr><td><table></table></td></tr>']))
        body = ('<tr data-row-key="K"><td>p-1</td><td>2025-02-29</td><td>approved</td>'
                '<td>NORTH</td><td>甲</td></tr>')
        self.assert_rejected(declaration('bad-date', [body]))

    def test_invalid_span_spellings(self):
        for spelling in ('0', '+1', ' 2', '2 ', 'x', '2.0', ''):
            with self.subTest(spelling=spelling):
                body = ('<tr data-row-key="K"><td rowspan="' + spelling + '">p-1</td>'
                        '<td>2024-02-29</td><td>approved</td><td>NORTH</td><td>甲</td></tr>')
                self.assert_rejected(declaration('span-' + repr(spelling), [body]))

    def test_huge_rowspan_spelling_is_not_expanded(self):
        body = ('<tr data-row-key="K"><td rowspan="' + '9' * 5000 + '">p-1</td>'
                '<td>2024-02-29</td><td>approved</td><td>NORTH</td><td>甲</td></tr>')
        self.assert_rejected(declaration('huge', [body]))

    def test_real_multiline_rowspan_across_three_pages(self):
        first = ('<tr data-row-key="K"><td rowspan="3">p-1</td>'
                 '<td rowspan="3">2024-02-29</td><td rowspan="3">approved</td>'
                 '<td rowspan="3" colspan="2">NORTH</td></tr>')
        second = HEADER + '<tr data-row-key="K"></tr>'
        third = HEADER + '<aside class="footnote">脚注</aside><tr data-row-key="K"></tr>'
        result, _ = self.publish(declaration('three-pages', [first, second, third]))
        record = result['records'][0]
        self.assertEqual([(r['page_no'], r['row_no']) for r in record['member_rows']],
                         [(1, 1), (2, 1), (3, 1)])
        self.assertEqual(record['field_sources'][0][0]['page_no'], 1)
        self.assertEqual(record['field_sources'][0][0]['row_no'], 1)

    def test_missing_page_then_complete(self):
        obj = declaration('missing', [
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
            '<td>NORTH</td><td>甲</td></tr>',
            '<tr data-row-key="L"><td>p-2</td><td>2024-03-01</td><td>pending</td>'
            '<td>EAST</td><td>乙</td></tr>'])
        source = manifest_from_object(obj)
        cap = Capture(self.root, source)
        cap.ingest_page(source.pages[0].url, source.pages[0].html, 1)
        with self.assertRaises(CaptureError):
            cap.finish()
        with self.assertRaises(CaptureError):
            Capture(self.root).read('missing')
        cap.ingest_page(source.pages[1].url, source.pages[1].html, 2)
        identifier = cap.finish()
        self.assertEqual(Capture(self.root).read('missing')['publication_id'], identifier)

    def test_failure_preserves_old_publications(self):
        old = IntakeService(self.root).run_manifest(self._v1_manifest())
        old_bytes = (self.root / 'archive/publications' / old / 'snapshot.json').read_bytes()
        self.assert_rejected(declaration('bad', [
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
            '<td>NORTH</td><td rowspan="2">甲</td></tr>']))
        self.assertTrue(ReviewService(IntakeService(self.root).archive).verify(old).ok)
        self.assertEqual((self.root / 'archive/publications' / old / 'snapshot.json').read_bytes(),
                         old_bytes)

    def _v1_manifest(self):
        html = v1_table('<tr><td>P-1</td><td>2024-01-01</td><td>approved</td>'
                        '<td>NORTH</td><td>甲</td></tr>')
        return manifest_from_object({'batch_id': 'legacy', 'table_selector': '#permits',
                                     'pages': [{'page_no': 1,
                                                'url': 'https://legacy.example.test/p',
                                                'html': html}]})


class ReplayTests(CrossPageCase):
    def test_retry_finish_and_reopen(self):
        obj = declaration('stable', [
            '<tr data-row-key="K"><td rowspan="2">p-1</td><td rowspan="2">2024-02-29</td>'
            '<td>approved</td><td rowspan="2">NORTH</td><td>甲</td></tr>',
            '<tr data-row-key="K"><td>approved</td><td>甲</td></tr>'])
        source = manifest_from_object(obj)
        cap = Capture(self.root, source)
        for item in source.pages:
            first = cap.ingest_page(item.url, item.html, item.page_no)
            second = cap.ingest_page(item.url, item.html, item.page_no)
            self.assertEqual(first['status'], 'accepted')
            self.assertEqual(second['status'], 'duplicate')
        identifier = cap.finish()
        self.assertEqual(cap.finish(), identifier)
        for item in source.pages:
            self.assertEqual(cap.ingest_page(item.url, item.html, item.page_no)['status'],
                             'duplicate')
        reopened = Capture(self.root)
        self.assertEqual(reopened.read('stable')['publication_id'], identifier)
        self.assertTrue(reopened.verify('stable').ok)
        self.assertEqual(reopened.verify('stable').checked_records, 1)

    def test_changed_declaration_conflicts(self):
        obj = declaration('declared', [
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
            '<td>NORTH</td><td>甲</td></tr>'])
        self.capture(obj)
        changed = deepcopy(obj)
        changed['pages'][0]['html'] = page(
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>revoked</td>'
            '<td>NORTH</td><td>甲</td></tr>')
        with self.assertRaises(CaptureError):
            Capture(self.root, manifest_from_object(changed))

    def test_detached_read_and_tampered_pages(self):
        obj = declaration('detached', [
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
            '<td>NORTH</td><td>甲</td></tr>'])
        result, identifier = self.publish(obj)
        result['records'][0]['fields'][0] = 'FORGED'
        result['records'][0]['field_sources'][0][0]['start'] = 0
        self.assertEqual(Capture(self.root).read('detached')['records'][0]['fields'][0], 'P-1')
        folder = self.root / 'archive/publications' / identifier
        page_file = folder / 'pages/000001.html'
        original = page_file.read_bytes()
        page_file.write_bytes(original.replace(b'approved', b'ApproVed'))
        self.assertFalse(Capture(self.root).verify('detached').ok)
        with self.assertRaises(CaptureError):
            Capture(self.root).read('detached')
        page_file.write_bytes(original)

    def test_tampered_snapshot_sources_are_rejected(self):
        obj = declaration('tamper', [
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
            '<td>NORTH</td><td>甲</td></tr>'])
        _, identifier = self.publish(obj)
        folder = self.root / 'archive/publications' / identifier
        path = folder / 'snapshot.json'
        content = json.loads(path.read_bytes())
        content['records'][0]['field_sources'][0][0]['start'] += 1
        from permit_capture.archive_codec import canonical_snapshot_bytes
        path.write_bytes(canonical_snapshot_bytes(content))
        self.assertFalse(Capture(self.root).verify('tamper').ok)
        with self.assertRaises(CaptureError):
            Capture(self.root).read('tamper')

    def test_unknown_batch(self):
        with self.assertRaises(CaptureError):
            Capture(self.root).read('nothing')
        self.assertFalse(Capture(self.root).verify('nothing').ok)


class JointRegressionTests(CrossPageCase):
    def test_v1_and_v2_coexist_without_changing_v1(self):
        html = v1_table('<tr><td>P-1</td><td>2024-01-01</td><td>approved</td>'
                        '<td>NORTH</td><td>甲</td></tr>')
        legacy = manifest_from_object({'batch_id': 'legacy', 'table_selector': '#permits',
                                       'pages': [{'page_no': 1,
                                                  'url': 'https://legacy.example.test/p',
                                                  'html': html}]})
        service = IntakeService(self.root)
        old = service.run_manifest(legacy)
        old_bytes = (self.root / 'archive/publications' / old / 'snapshot.json').read_bytes()
        obj = declaration('modern', [
            '<tr data-row-key="K"><td rowspan="2">p-1</td><td rowspan="2">2024-02-29</td>'
            '<td>approved</td><td rowspan="2">NORTH</td><td>甲</td></tr>',
            '<tr data-row-key="K"><td>approved</td><td>甲</td></tr>'])
        result, new = self.publish(obj)
        self.assertNotEqual(old, new)
        self.assertEqual((self.root / 'archive/publications' / old / 'snapshot.json').read_bytes(),
                         old_bytes)
        self.assertTrue(ReviewService(IntakeService(self.root).archive).verify(old).ok)
        self.assertEqual(len(ReviewService(IntakeService(self.root).archive).read(old).records), 1)
        self.assertEqual(Capture(self.root).read('modern')['publication_id'], new)
        self.assertTrue(Capture(self.root).verify('modern').ok)
        # V1 publication ids and V2 publication ids share the archive.
        folders = {p.name for p in (self.root / 'archive/publications').iterdir()}
        self.assertEqual(folders, {old, new})

    def test_batch_ids_cannot_cross_versions(self):
        html = v1_table('<tr><td>P-1</td><td>2024-01-01</td><td>approved</td>'
                       '<td>NORTH</td><td>甲</td></tr>')
        legacy = manifest_from_object({'batch_id': 'shared-id', 'table_selector': '#permits',
                                       'pages': [{'page_no': 1,
                                                  'url': 'https://legacy.example.test/p',
                                                  'html': html}]})
        service = IntakeService(self.root)
        old = service.run_manifest(legacy)
        old_bytes = (self.root / 'archive/publications' / old / 'snapshot.json').read_bytes()
        with self.assertRaises(CaptureError):
            self.publish(declaration('shared-id', [
                '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
                '<td>NORTH</td><td>甲</td></tr>']))
        self.assertEqual((self.root / 'archive/publications' / old / 'snapshot.json').read_bytes(),
                         old_bytes)
        modern = declaration('modern-id', [
            '<tr data-row-key="K"><td>p-1</td><td>2024-02-29</td><td>approved</td>'
            '<td>NORTH</td><td>甲</td></tr>'])
        _, new = self.publish(modern)
        conflicting = manifest_from_object({'batch_id': 'modern-id',
                                            'table_selector': '#permits',
                                            'pages': [{'page_no': 1,
                                                       'url': 'https://other.example.test/x',
                                                       'html': html}]})
        with self.assertRaises(Exception):
            IntakeService(self.root).run_manifest(conflicting)
        self.assertEqual(Capture(self.root).read('modern-id')['publication_id'], new)
        self.assertTrue(ReviewService(IntakeService(self.root).archive).verify(old).ok)
