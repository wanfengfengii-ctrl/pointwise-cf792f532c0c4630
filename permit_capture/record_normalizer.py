from dataclasses import dataclass
from .schema import COLUMN_ORDER
from .field_rules import normalize_field, FieldRuleError
from .identity import make_row_key, IdentityConflict
from .row_validation import FieldIssue, RowValidationError, validate_batch_records


@dataclass(frozen=True)
class PermitRecord:
    row_key: str
    fields: tuple
    page_no: int
    row_no: int
    start: int
    end: int


@dataclass(frozen=True)
class NormalizationResult:
    records: tuple
    errors: tuple


def normalize_row(row, page_no):
    values, issues = [], []
    if len(row.cells) != len(COLUMN_ORDER):
        raise RowValidationError(('fixed column count required',))
    for column, (name, cell) in enumerate(zip(COLUMN_ORDER, row.cells), 1):
        try:
            values.append(normalize_field(name, cell.text))
        except FieldRuleError as exc:
            issues.append(FieldIssue(exc.code, row.row_key, page_no, row.row_no, column, str(exc)))
            values.append(None)
    if issues:
        raise RowValidationError(issues)
    try:
        key = make_row_key(row.row_key, values[0])
    except IdentityConflict as exc:
        raise RowValidationError((FieldIssue('ROW_KEY', row.row_key, page_no, row.row_no, 0, str(exc)),)) from exc
    return PermitRecord(key, tuple(values), page_no, row.row_no, row.start, row.end)


def normalize_rows(rows_by_page):
    records, errors = [], []
    for page_no, rows in rows_by_page:
        for row in rows:
            try:
                records.append(normalize_row(row, page_no))
            except RowValidationError as exc:
                errors.extend(exc.issues)
    if not errors:
        try:
            validate_batch_records(records)
        except (RowValidationError, IdentityConflict) as exc:
            errors.append(str(exc))
    return NormalizationResult(tuple(records), tuple(errors))
