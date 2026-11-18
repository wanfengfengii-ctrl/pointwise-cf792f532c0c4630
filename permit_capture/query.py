from dataclasses import dataclass
import base64
import binascii
from hashlib import sha256
import json
from .filter_rules import parse_filters, canonical_filter_bytes, validate_page_size, FilterError
from .reader import read_records


@dataclass(frozen=True)
class QueryCursor:
    publication_id: str
    filter_sha256: str
    last: tuple


@dataclass(frozen=True)
class QueryResult:
    publication_id: str
    records: list
    next_cursor: str | None


def encode_cursor(cursor):
    raw = json.dumps({'publication_id': cursor.publication_id,
                      'filter_sha256': cursor.filter_sha256, 'last': list(cursor.last)},
                     sort_keys=True, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii')


def decode_cursor(value, publication_id, filter_digest):
    try:
        raw = base64.b64decode(value.encode('ascii'), altchars=b'-_', validate=True)
        obj = json.loads(raw)
        if set(obj) != {'publication_id', 'filter_sha256', 'last'}:
            raise FilterError('invalid cursor fields')
        if obj['publication_id'] != publication_id or obj['filter_sha256'] != filter_digest:
            raise FilterError('cursor context differs')
        if not isinstance(obj['last'], list) or len(obj['last']) != 2 or not all(
                isinstance(v, str) for v in obj['last']):
            raise FilterError('invalid cursor position')
        return QueryCursor(publication_id, filter_digest, tuple(obj['last']))
    except (ValueError, TypeError, AttributeError, UnicodeError, binascii.Error) as exc:
        raise FilterError(f'invalid cursor: {exc}') from exc


def matching_records(snapshot, filters=None):
    filters = parse_filters(filters)
    selected = []
    for record in read_records(snapshot):
        permit, issued, _, district, _ = record['fields']
        if filters.permit_no is not None and permit != filters.permit_no:
            continue
        if filters.district is not None and district != filters.district:
            continue
        if filters.issued_from is not None and issued < filters.issued_from:
            continue
        if filters.issued_to is not None and issued > filters.issued_to:
            continue
        selected.append(record)
    return sorted(selected, key=lambda r: (r['fields'][0], r['row_key']))


def select_records(snapshot, filters=None, limit=100, cursor=None):
    validate_page_size(limit)
    filters = parse_filters(filters)
    digest = sha256(canonical_filter_bytes(filters)).hexdigest()
    selected = matching_records(snapshot, filters)
    if cursor is not None:
        position = decode_cursor(cursor, snapshot.publication_id, digest).last
        keys = [(r['fields'][0], r['row_key']) for r in selected]
        if position not in keys:
            raise FilterError('cursor row absent from selection')
        selected = selected[keys.index(position) + 1:]
    page = selected[:limit]
    next_cursor = None
    if len(selected) > limit:
        last = page[-1]
        next_cursor = encode_cursor(QueryCursor(snapshot.publication_id, digest,
                                                (last['fields'][0], last['row_key'])))
    return QueryResult(snapshot.publication_id, page, next_cursor)
