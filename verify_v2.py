"""Combined V1+V2 verification and cross-page demonstration.

    python3.11 -B verify_v2.py verify   # full unittest suite + cross-page checks
    python3.11 -B verify_v2.py demo     # annotated cross-page inheritance demo

``verify`` exits non-zero if the original suite or the V2 business checks fail.
``demo`` prints physical members, logical records, inherited origins and shared
source cells for the three literal public fixtures, then runs the same checks.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

COLUMNS = ('permit_no', 'issued_on', 'status', 'district', 'applicant')


def run_suite():
    command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    result = subprocess.run(command, cwd=ROOT)
    return result.returncode == 0


def run_crosspage_check():
    command = [sys.executable, '-B', str(ROOT / 'crosspage_cases.py'), 'verify']
    result = subprocess.run(command, cwd=ROOT)
    return result.returncode == 0


def describe(ref):
    return 'p{page_no}·r{row_no}·c{column_no} [{start}:{end}]'.format(**ref)


def render(name, result):
    print('== ' + name + ' ==')
    for record in result['records']:
        print('record key=' + record['row_key'] + ' fields=' +
              json.dumps(record['fields'], ensure_ascii=False))
        print('  members: ' + ', '.join(
            'p{page_no}·r{row_no} [{start}:{end}]'.format(**ref)
            for ref in record['member_rows']))
        for column, (label, refs) in enumerate(zip(COLUMNS, record['field_sources']), 1):
            print('  field {} {} <- {}'.format(
                column, label, ', '.join(describe(ref) for ref in refs)))


def demo():
    from crosspage_cases import examples, verify
    from permit_capture.source_manifest import manifest_from_object
    from tablecapture import Capture
    notes = {
        'whole': 'cross-page rowspan: one K record, members on both pages, '
                 'all five sources bound to page 1',
        'split': 'same-key continuation: status/applicant keep one source per page',
        'shared': 'different permits A and B share the NORTH cell of page 1 column 4 '
                  'yet stay two records',
    }
    with tempfile.TemporaryDirectory(prefix='crosspage-demo-') as directory:
        root = Path(directory)
        for obj in examples():
            source = manifest_from_object(obj)
            capture = Capture(root, source)
            for page in source.pages:
                capture.ingest_page(page.url, page.html, page.page_no)
            identifier = capture.finish()
            result = Capture(root).read(obj['batch_id'])
            render(obj['batch_id'], result)
            print('  publication_id=' + identifier)
            print('  note: ' + notes[obj['batch_id']])
            print()
        verify(True)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['verify']:
        suite_ok = run_suite()
        crosspage_ok = run_crosspage_check()
        if not (suite_ok and crosspage_ok):
            print('VERIFY_FAILED suite={} crosspage={}'.format(suite_ok, crosspage_ok))
            return 1
        print('VERIFY_OK: original suite and V2 cross-page checks all pass')
        return 0
    if argv == ['demo']:
        return demo()
    print('usage: python3.11 -B verify_v2.py verify|demo', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
