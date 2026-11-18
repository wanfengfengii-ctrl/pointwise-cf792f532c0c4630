from dataclasses import dataclass
from .schema import COLUMN_ORDER
from .field_rules import normalize_field, FieldRuleError
from .identity import make_row_key, assert_unique_keys


@dataclass(frozen=True)
class FieldIssue:
    code: str
    row_key: str | None
    page_no: int
    row_no: int
    column_no: int
    detail: str


class RowValidationError(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__('; '.join(str(issue) for issue in self.issues))


def validate_record(record):
    if len(record.fields) != len(COLUMN_ORDER):
        raise RowValidationError(('fixed column count required',))
    for column, (name, value) in enumerate(zip(COLUMN_ORDER, record.fields), 1):
        try:
            normalized = normalize_field(name, value)
        except FieldRuleError as exc:
            raise RowValidationError((FieldIssue(exc.code, record.row_key, record.page_no,
                                               record.row_no, column, str(exc)),)) from exc
        if normalized != value:
            raise RowValidationError((f'noncanonical {name}',))
    if make_row_key(record.row_key, record.fields[0]) != record.row_key:
        raise RowValidationError(('noncanonical row key',))
    if (type(record.page_no) is not int or type(record.row_no) is not int
            or record.page_no < 1 or record.row_no < 1
            or type(record.start) is not int or type(record.end) is not int
            or not 0 <= record.start < record.end):
        raise RowValidationError(('invalid source position',))
    return record


def validate_batch_records(records):
    records = tuple(records)
    for record in records:
        validate_record(record)
    assert_unique_keys(records)
    positions = [(r.page_no, r.row_no) for r in records]
    if len(set(positions)) != len(positions):
        raise RowValidationError(('duplicate source row',))
    return records
