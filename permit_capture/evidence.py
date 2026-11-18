from dataclasses import dataclass, asdict
from hashlib import sha256


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceRef:
    page_no: int
    row_no: int
    start: int
    end: int
    page_sha256: str
    fragment_sha256: str

    def as_dict(self):
        return asdict(self)


def bind_row_evidence(page, row):
    ref = EvidenceRef(page.page_no, row.row_no, row.start, row.end,
                      page.sha256, sha256(page.data[row.start:row.end]).hexdigest())
    validate_evidence_ref(ref, page.data, page.sha256)
    return ref


def validate_evidence_ref(ref, raw, page_digest):
    if (any(type(value) is not int for value in
            (ref.page_no, ref.row_no, ref.start, ref.end))
            or ref.page_no < 1 or ref.row_no < 1
            or not 0 <= ref.start < ref.end <= len(raw)):
        raise EvidenceError('invalid evidence coordinates')
    actual = sha256(raw).hexdigest()
    if actual != page_digest or ref.page_sha256 != actual:
        raise EvidenceError(f'page {ref.page_no}: digest mismatch')
    if sha256(raw[ref.start:ref.end]).hexdigest() != ref.fragment_sha256:
        raise EvidenceError(f'page {ref.page_no} row {ref.row_no}: fragment mismatch')
    return True
