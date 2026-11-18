import json
from html import escape
from pathlib import Path
import tempfile
import unittest
from permit_capture.intake import IntakeService
from permit_capture.source_manifest import manifest_from_object, load_manifest
from permit_capture.review import ReviewService

COLUMNS = ('permit_no', 'issued_on', 'status', 'district', 'applicant')


def row(values, key=None):
    attribute = '' if key is None else ' data-row-key="' + escape(key, quote=True) + '"'
    return '<tr' + attribute + '>' + ''.join('<td>' + escape(v) + '</td>' for v in values) + '</tr>'


def table(body, prefix=''):
    head = '<thead><tr>' + ''.join('<th>' + c + '</th>' for c in COLUMNS) + '</tr></thead>'
    return prefix + '<table id="permits">' + head + '<tbody>' + body + '</tbody></table>'


def manifest(batch='day', htmls=None):
    if htmls is None:
        htmls = [table(row(['p-01', '2024-02-29', 'approved', 'NORTH', '甲'], 'key-1') +
                       row(['P-02', '2024-03-01', 'pending', 'EAST', '']))]
    return manifest_from_object({'batch_id': batch, 'table_selector': '#permits',
                                 'pages': [{'page_no': i, 'url': f'https://example.test/p?q={i}',
                                            'html': html} for i, html in enumerate(htmls, 1)]})


def accept_all(service, source):
    service.register(source)
    for page in source.pages:
        service.ingest_page(source.batch_id, page.url, page.page_no, page.html)


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='permit-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class CaptureFlowTests(WorkspaceCase):
    def test_single_page_publish(self):
        source = manifest()
        service = IntakeService(self.root)
        identifier = service.run_manifest(source)
        review = ReviewService(service.archive)
        result = review.read(identifier)
        expected = [['P-01', '2024-02-29', 'approved', 'NORTH', '甲'],
                    ['P-02', '2024-03-01', 'pending', 'EAST', None]]
        actual = [r['fields'] for r in result.records]
        self.assertEqual(actual, expected)
        self.assertEqual([r['row_key'] for r in result.records], ['key-1', 'P-02'])
        self.assertTrue(review.verify(identifier).ok)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'V1', 'input': source.descriptor(), 'expected': expected, 'actual': actual,
            'downstream_expected': {'verified': True, 'count': 2},
            'downstream_actual': {'verified': review.verify(identifier).ok, 'count': len(result.records)},
            'code_paths': ['permit_capture/intake.py', 'permit_capture/grid.py',
                           'permit_capture/record_normalizer.py', 'permit_capture/publication.py',
                           'permit_capture/replay.py'],
            'oracle': 'literal two-row field and identity enumeration independent of normalizer'
        }, ensure_ascii=False))

    def test_ordered_complete_pages(self):
        first = table(row(['B-2', '2024-01-01', 'pending', 'WEST', '']))
        second = table(row(['A-1', '2024-01-02', 'approved', 'EAST', '乙']))
        source = manifest('two-pages', [first, second])
        service = IntakeService(self.root)
        identifier = service.run_manifest(source)
        content, pages = service.archive.load(identifier)
        self.assertEqual([p['page_no'] for p in content['pages']], [1, 2])
        self.assertEqual([r['fields'][0] for r in content['records']], ['B-2', 'A-1'])
        queried = ReviewService(service.archive).read(identifier)
        self.assertEqual([r['fields'][0] for r in queried.records], ['A-1', 'B-2'])
        self.assertEqual(pages[2], second.encode())

    def test_stable_identity_and_batch_scope(self):
        service = IntakeService(self.root)
        first = service.run_manifest(manifest('first'))
        second = service.run_manifest(manifest('second'))
        self.assertNotEqual(first, second)
        self.assertEqual([r['row_key'] for r in ReviewService(service.archive).read(first).records],
                         ['key-1', 'P-02'])
        self.assertTrue(ReviewService(service.archive).verify(second).ok)

    def test_target_table_and_utf8_positions(self):
        decoy = table(row(['FAKE', '2024-01-01', 'approved', 'EAST', ''])).replace(
            'id="permits"', 'id="statistics"')
        body = row(['REAL', '2024-01-01', 'approved', 'WEST', '许'])
        html = table(body, '中文前言' + decoy)
        service = IntakeService(self.root)
        identifier = service.run_manifest(manifest('position', [html]))
        record = ReviewService(service.archive).read(identifier).records[0]
        self.assertEqual(record['fields'][0], 'REAL')
        ref = record['evidence']
        self.assertEqual(ref['start'], html.encode().index(body.encode()))
        self.assertEqual(html.encode()[ref['start']:ref['end']], body.encode())
        self.assertEqual((record['page_no'], record['row_no']), (1, 1))

    def test_public_sample(self):
        source = load_manifest(Path(__file__).resolve().parents[1] / 'examples/normal_day.json',
                               'normal_day')
        service = IntakeService(self.root)
        identifier = service.run_manifest(source)
        self.assertEqual([r['fields'][0] for r in ReviewService(service.archive).read(identifier).records],
                         ['CJ-001', 'CJ-002', 'CJ-003'])
