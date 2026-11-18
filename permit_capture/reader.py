from dataclasses import dataclass
from copy import deepcopy
from .evidence import EvidenceRef, validate_evidence_ref
from .record_normalizer import PermitRecord
from .row_validation import validate_batch_records
from .page_store import html_bytes


class PublicationReadError(ValueError):
    pass


@dataclass(frozen=True)
class PublicationSnapshot:
    publication_id: str
    content: dict
    pages: dict


def validate_snapshot_materials(content, pages):
    descriptors = {p['page_no']: p for p in content['pages']}
    if set(pages) != set(descriptors):
        raise PublicationReadError('page set differs')
    from hashlib import sha256
    for number, page in descriptors.items():
        raw = html_bytes(pages[number])
        if sha256(raw).hexdigest() != page['sha256']:
            raise PublicationReadError(f'page {number}: digest mismatch')
    records = []
    for item in content['records']:
        ref = EvidenceRef(**item['evidence'])
        if item['page_no'] != ref.page_no or item['row_no'] != ref.row_no:
            raise PublicationReadError('record and evidence coordinates differ')
        if ref.page_no not in descriptors:
            raise PublicationReadError('unknown evidence page')
        validate_evidence_ref(ref, pages[ref.page_no], descriptors[ref.page_no]['sha256'])
        records.append(PermitRecord(item['row_key'], tuple(item['fields']),
                                    item['page_no'], item['row_no'], ref.start, ref.end))
    validate_batch_records(records)
    if [(r.page_no, r.row_no, r.row_key) for r in records] != sorted(
            (r.page_no, r.row_no, r.row_key) for r in records):
        raise PublicationReadError('record order differs')
    return tuple(records)


def load_publication(repository, publication_id):
    try:
        content, pages = repository.load(publication_id)
        validate_snapshot_materials(content, pages)
        return PublicationSnapshot(publication_id, deepcopy(content), dict(pages))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise PublicationReadError(str(exc)) from exc


def read_records(snapshot):
    return deepcopy(snapshot.content['records'])
