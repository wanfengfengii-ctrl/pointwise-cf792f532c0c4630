"""Finite public fixtures; no span expansion or capture implementation."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sys
import tempfile

COLUMNS = ('permit_no', 'issued_on', 'status', 'district', 'applicant')
HEADER = '<tr>' + ''.join('<th>' + c + '</th>' for c in COLUMNS) + '</tr>'


def table(body):
    return '<p>许可公示</p><table id="permits"><thead>' + HEADER + '</thead><tbody>' + body + '</tbody></table>'


def declaration(batch_id, bodies):
    return {'batch_id': batch_id, 'table_selector': '#permits',
            'pages': [{'page_no': i, 'url': 'https://permits.example.test/span?p=' + str(i),
                       'html': table(body)} for i, body in enumerate(bodies, 1)]}


def examples():
    whole = declaration('whole', [
        '<tr data-row-key="K"><td rowspan="2">p-1</td><td rowspan="2">2024-02-29</td>'
        '<td rowspan="2">approved</td><td rowspan="2" colspan="2">NORTH</td></tr>',
        HEADER + '<aside class="footnote"><tr><td>不占数据行</td></tr></aside>'
        '<tr data-row-key="K"></tr>'])
    split = declaration('split', [
        '<tr data-row-key="K"><td rowspan="2">p-2</td><td rowspan="2">2024-02-29</td>'
        '<td>approved</td><td rowspan="2">NORTH</td><td>李明</td></tr>',
        HEADER + '<tr data-row-key="K"><td> approved </td>'
        '<td>李<span class="footnote">不入字段</span>明</td></tr>'])
    shared = declaration('shared', [
        '<tr data-row-key="A"><td>A-1</td><td>2024-02-29</td><td>approved</td>'
        '<td rowspan="2" colspan="2">NORTH</td></tr>',
        HEADER + '<tr data-row-key="B"><td>B-1</td><td>2024-03-01</td><td>pending</td></tr>'])
    return whole, split, shared


def cell_ref(obj, page_no, row_no, column_no, literal):
    raw = obj['pages'][page_no - 1]['html'].encode('utf-8')
    piece = literal.encode('utf-8')
    assert raw.count(piece) == 1, literal
    start = raw.index(piece)
    return {'page_no': page_no, 'row_no': row_no, 'column_no': column_no,
            'start': start, 'end': start + len(piece), 'page_sha256': sha256(raw).hexdigest(),
            'fragment_sha256': sha256(piece).hexdigest()}


def expected(obj):
    name = obj['batch_id']
    if name == 'whole':
        texts = ['<td rowspan="2">p-1</td>', '<td rowspan="2">2024-02-29</td>',
                 '<td rowspan="2">approved</td>', '<td rowspan="2" colspan="2">NORTH</td>']
        refs = [cell_ref(obj, 1, 1, i, t) for i, t in enumerate(texts, 1)]
        return [('K', ['P-1', '2024-02-29', 'approved', 'NORTH', 'NORTH'],
                 [[refs[0]], [refs[1]], [refs[2]], [refs[3]], [refs[3]]], [(1, 1), (2, 1)])]
    if name == 'split':
        literals = ['<td rowspan="2">p-2</td>', '<td rowspan="2">2024-02-29</td>',
                    '<td>approved</td>', '<td rowspan="2">NORTH</td>', '<td>李明</td>']
        refs = [cell_ref(obj, 1, 1, i, t) for i, t in enumerate(literals, 1)]
        later_status = cell_ref(obj, 2, 1, 3, '<td> approved </td>')
        later_name = cell_ref(obj, 2, 1, 5, '<td>李<span class="footnote">不入字段</span>明</td>')
        return [('K', ['P-2', '2024-02-29', 'approved', 'NORTH', '李明'],
                 [[refs[0]], [refs[1]], [refs[2], later_status], [refs[3]], [refs[4], later_name]],
                 [(1, 1), (2, 1)])]
    common = cell_ref(obj, 1, 1, 4, '<td rowspan="2" colspan="2">NORTH</td>')
    rows = []
    for page, key, values in [(1, 'A', ['A-1', '2024-02-29', 'approved']),
                              (2, 'B', ['B-1', '2024-03-01', 'pending'])]:
        refs = [[cell_ref(obj, page, 1, col, '<td>' + text + '</td>')]
                for col, text in enumerate(values, 1)]
        rows.append((key, values + ['NORTH', 'NORTH'], refs + [[common], [common]], [(page, 1)]))
    return rows


def check_result(obj, result):
    wanted = expected(obj)
    assert set(result) == {'publication_id', 'records'}
    assert len(result['publication_id']) == 64
    assert len(result['records']) == len(wanted)
    for got, (key, fields, sources, members) in zip(result['records'], wanted):
        assert got['row_key'] == key and got['fields'] == fields, got
        assert got['field_sources'] == sources, got
        assert [(r['page_no'], r['row_no']) for r in got['member_rows']] == members
        for ref in got['member_rows']:
            raw = obj['pages'][ref['page_no'] - 1]['html'].encode()
            fragment = raw[ref['start']:ref['end']]
            assert fragment.startswith(b'<tr data-row-key=') and fragment.endswith(b'</tr>')
            assert ref['page_sha256'] == sha256(raw).hexdigest()
            assert ref['fragment_sha256'] == sha256(fragment).hexdigest()


def materials():
    from permit_capture.source_manifest import manifest_from_object
    from permit_capture.html_parser import parse_table
    from permit_capture.schema import ParseError
    for obj in examples():
        manifest = manifest_from_object(obj)
        assert len(manifest.pages) == 2
        assert expected(obj)
        try:
            parse_table(manifest.pages[0].html, manifest.table_selector)
        except ParseError as exc:
            assert exc.code in ('UNSUPPORTED_SPAN', 'ROW_WIDTH')
        else:
            raise AssertionError('V1 fixture unexpectedly accepted')
    print('MATERIALS_OK: three literal examples; V1 rejects the new structures')


def capture_one(root, obj):
    from tablecapture import Capture
    from permit_capture.source_manifest import manifest_from_object
    source = manifest_from_object(obj)
    cap = Capture(root, source)
    for page in source.pages:
        cap.ingest_page(page.url, page.html, page.page_no)
        retry = cap.ingest_page(page.url, page.html, page.page_no)
        assert retry['status'] == 'duplicate'
    identifier = cap.finish()
    assert cap.finish() == identifier
    result = Capture(root).read(obj['batch_id'])
    assert result['publication_id'] == identifier
    check_result(obj, result)
    report = Capture(root).verify(obj['batch_id'])
    assert report.ok and report.checked_records == len(result['records']), report
    return result


def verify(demo=False):
    from tablecapture import Capture, CaptureError
    from permit_capture.source_manifest import manifest_from_object, load_manifest
    from permit_capture.intake import IntakeService
    from permit_capture.review import ReviewService
    with tempfile.TemporaryDirectory(prefix='crosspage-check-') as directory:
        root = Path(directory)
        old = IntakeService(root).run_manifest(load_manifest(
            Path(__file__).resolve().parent / 'examples/normal_day.json', 'normal_day'))
        old_bytes = (root / 'archive/publications' / old / 'snapshot.json').read_bytes()
        for obj in examples():
            result = capture_one(root, obj)
            if demo:
                print(json.dumps(result, ensure_ascii=False))
        source = manifest_from_object(examples()[0])
        cap = Capture(root / 'missing', source)
        p = source.pages[0]
        cap.ingest_page(p.url, p.html, p.page_no)
        try:
            cap.finish()
        except CaptureError:
            pass
        else:
            raise AssertionError('missing declared continuation published')
        try:
            Capture(root / 'missing').read('whole')
        except CaptureError:
            pass
        else:
            raise AssertionError('partial batch readable')
        p = source.pages[1]
        cap.ingest_page(p.url, p.html, p.page_no)
        cap.finish()
        bad = deepcopy(examples()[1])
        bad['batch_id'] = 'conflict'
        bad['pages'][1]['html'] = bad['pages'][1]['html'].replace(' approved ', ' revoked ')
        cap = Capture(root, manifest_from_object(bad))
        for p in bad['pages']:
            cap.ingest_page(p['url'], p['html'], p['page_no'])
        try:
            cap.finish()
        except CaptureError:
            pass
        else:
            raise AssertionError('changed continuation published')
        assert ReviewService(IntakeService(root).archive).verify(old).ok
        assert (root / 'archive/publications' / old / 'snapshot.json').read_bytes() == old_bytes
        reopened = Capture(root)
        result = reopened.read('whole')
        folder = root / 'archive/publications' / result['publication_id']
        target = folder / 'pages/000002.html'
        target.write_bytes(target.read_bytes() + b' ')
        assert not reopened.verify('whole').ok
        try:
            reopened.read('whole')
        except CaptureError:
            pass
        else:
            raise AssertionError('damaged page read as valid')
    print('CROSSPAGE_OK: bound origins, continuation, shared cells, rejection and reopen')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('materials', 'verify', 'demo'):
        raise SystemExit('usage: python3.11 -B crosspage_cases.py materials|verify|demo')
    if sys.argv[1] == 'materials':
        materials()
    else:
        verify(sys.argv[1] == 'demo')
