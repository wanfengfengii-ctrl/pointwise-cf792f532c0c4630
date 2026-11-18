import json
import re
from hashlib import sha256
from .schema import COLUMN_ORDER

SNAPSHOT_FORMAT_VERSION = 1
SPAN_SNAPSHOT_FORMAT_VERSION = 2
SPAN_RULES = 'permit-span-v2'
FIELD_SOURCE_FIELDS = {'page_no', 'row_no', 'column_no', 'start', 'end',
                       'page_sha256', 'fragment_sha256'}
MEMBER_ROW_FIELDS = {'page_no', 'row_no', 'start', 'end',
                     'page_sha256', 'fragment_sha256'}


class SnapshotError(ValueError):
    pass


def canonical_snapshot_bytes(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def publication_fingerprint(snapshot):
    return sha256(canonical_snapshot_bytes(snapshot)).hexdigest()


def build_snapshot(manifest, records, refs):
    records = sorted(records, key=lambda r: (r.page_no, r.row_no, r.row_key))
    refmap = {(r.page_no, r.row_no): r for r in refs}
    return {'format': SNAPSHOT_FORMAT_VERSION, 'rules': 'permit-v1',
            'batch_id': manifest.batch_id, 'table_selector': manifest.table_selector,
            'columns': list(COLUMN_ORDER),
            'pages': [{'page_no': p.page_no, 'url': p.url, 'sha256': p.sha256,
                       'path': f'pages/{p.page_no:06d}.html'} for p in manifest.pages],
            'records': [{'row_key': r.row_key, 'fields': list(r.fields),
                         'page_no': r.page_no, 'row_no': r.row_no,
                         'evidence': refmap[r.page_no, r.row_no].as_dict()} for r in records]}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError('duplicate JSON key')
        result[key] = value
    return result


SPAN_SNAPSHOT_FIELDS = {'format', 'rules', 'batch_id', 'table_selector', 'columns',
                        'pages', 'records'}


def _check_source_ref(ref, with_column):
    for name in ('page_no', 'row_no', 'start', 'end'):
        if type(ref[name]) is not int:
            raise SnapshotError('invalid source coordinate type')
    if ref['page_no'] < 1 or ref['row_no'] < 1 or not 0 <= ref['start'] < ref['end']:
        raise SnapshotError('invalid source coordinates')
    if with_column and (type(ref['column_no']) is not int
                        or not 1 <= ref['column_no'] <= len(COLUMN_ORDER)):
        raise SnapshotError('invalid source column')
    for name in ('page_sha256', 'fragment_sha256'):
        if not isinstance(ref[name], str) or not re.fullmatch('[0-9a-f]{64}', ref[name]):
            raise SnapshotError('invalid source digest')


def decode_span_snapshot(raw):
    """Decode a V2 cross-page snapshot; V1 snapshots are untouched."""
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        if set(obj) != SPAN_SNAPSHOT_FIELDS:
            raise SnapshotError('snapshot fields differ')
        if (type(obj['format']) is not int or obj['format'] != SPAN_SNAPSHOT_FORMAT_VERSION
                or obj['rules'] != SPAN_RULES):
            raise SnapshotError('unsupported snapshot rules')
        if not isinstance(obj['batch_id'], str) or not obj['batch_id'].strip():
            raise SnapshotError('invalid batch id')
        if (not isinstance(obj['table_selector'], str)
                or obj['columns'] != list(COLUMN_ORDER)):
            raise SnapshotError('invalid schema')
        if not isinstance(obj['pages'], list) or not obj['pages']:
            raise SnapshotError('invalid page list')
        descriptor = {'batch_id': obj['batch_id'],
                      'table_selector': obj['table_selector'], 'pages': []}
        for i, page in enumerate(obj['pages'], 1):
            if set(page) != {'page_no', 'url', 'sha256', 'path'}:
                raise SnapshotError('invalid page shape')
            if (type(page['page_no']) is not int or page['page_no'] != i
                    or page['path'] != f'pages/{i:06d}.html'):
                raise SnapshotError('invalid page path')
            descriptor['pages'].append({k: page[k] for k in ('page_no', 'url', 'sha256')})
        from .source_manifest import SourceManifest
        SourceManifest.from_descriptor(descriptor)
        if not isinstance(obj['records'], list):
            raise SnapshotError('invalid records')
        for record in obj['records']:
            if set(record) != {'row_key', 'fields', 'field_sources', 'member_rows'}:
                raise SnapshotError('invalid record shape')
            if (not isinstance(record['row_key'], str)
                    or not isinstance(record['fields'], list)
                    or len(record['fields']) != len(COLUMN_ORDER)
                    or not isinstance(record['field_sources'], list)
                    or len(record['field_sources']) != len(COLUMN_ORDER)
                    or not isinstance(record['member_rows'], list)
                    or not record['member_rows']):
                raise SnapshotError('invalid record values')
            for refs in record['field_sources']:
                if not isinstance(refs, list) or not refs:
                    raise SnapshotError('invalid field sources')
                for ref in refs:
                    if set(ref) != FIELD_SOURCE_FIELDS:
                        raise SnapshotError('invalid field source')
                    _check_source_ref(ref, with_column=True)
            for ref in record['member_rows']:
                if set(ref) != MEMBER_ROW_FIELDS:
                    raise SnapshotError('invalid member row')
                _check_source_ref(ref, with_column=False)
        if canonical_snapshot_bytes(obj) != raw:
            raise SnapshotError('noncanonical snapshot encoding')
        return obj
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f'invalid snapshot: {exc}') from exc


def decode_artifact(raw):
    """Decode either the V1 record snapshot or a V2 cross-page snapshot."""
    try:
        probe = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SnapshotError(f'invalid snapshot: {exc}') from exc
    if isinstance(probe, dict) and probe.get('format') == SPAN_SNAPSHOT_FORMAT_VERSION:
        return decode_span_snapshot(raw)
    return decode_snapshot(raw)


def decode_snapshot(raw):
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        if set(obj) != {'format', 'rules', 'batch_id', 'table_selector', 'columns', 'pages', 'records'}:
            raise SnapshotError('snapshot fields differ')
        if type(obj['format']) is not int or obj['format'] != 1 or obj['rules'] != 'permit-v1':
            raise SnapshotError('unsupported snapshot rules')
        if obj['columns'] != list(COLUMN_ORDER) or not isinstance(obj['records'], list):
            raise SnapshotError('invalid schema')
        from .source_manifest import SourceManifest
        descriptor = {'batch_id': obj['batch_id'], 'table_selector': obj['table_selector'],
                      'pages': []}
        for i, page in enumerate(obj['pages'], 1):
            if set(page) != {'page_no', 'url', 'sha256', 'path'} or page['path'] != f'pages/{i:06d}.html':
                raise SnapshotError('invalid page path')
            descriptor['pages'].append({k: page[k] for k in ('page_no', 'url', 'sha256')})
        SourceManifest.from_descriptor(descriptor)
        for record in obj['records']:
            if set(record) != {'row_key', 'fields', 'page_no', 'row_no', 'evidence'}:
                raise SnapshotError('invalid record shape')
            if not isinstance(record['fields'], list) or not isinstance(record['evidence'], dict):
                raise SnapshotError('invalid record values')
        if canonical_snapshot_bytes(obj) != raw:
            raise SnapshotError('noncanonical snapshot encoding')
        return obj
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError(f'invalid snapshot: {exc}') from exc
