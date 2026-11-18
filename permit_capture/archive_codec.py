import json
from hashlib import sha256
from .schema import COLUMN_ORDER

SNAPSHOT_FORMAT_VERSION = 1


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
