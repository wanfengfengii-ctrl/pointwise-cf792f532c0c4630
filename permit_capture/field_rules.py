from datetime import date
import re
import unicodedata
from .schema import COLUMN_ORDER, OPTIONAL_COLUMNS

STATUS_VALUES = frozenset({'approved', 'pending', 'revoked'})
DISTRICT_VALUES = frozenset({'NORTH', 'EAST', 'SOUTH', 'WEST'})


class FieldRuleError(ValueError):
    def __init__(self, code, field, value):
        self.code, self.field, self.value = code, field, value
        super().__init__(f'{code}: {field}={value!r}')


def parse_issue_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise FieldRuleError('DATE_FORMAT', 'issued_on', value)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FieldRuleError('DATE_CALENDAR', 'issued_on', value) from exc


def normalize_field(name, value):
    if name not in COLUMN_ORDER:
        raise FieldRuleError('FIELD', name, value)
    if value is None and name in OPTIONAL_COLUMNS:
        return None
    if not isinstance(value, str):
        raise FieldRuleError('TEXT', name, value)
    text = unicodedata.normalize('NFC', value).strip()
    if not text:
        if name in OPTIONAL_COLUMNS:
            return None
        raise FieldRuleError('REQUIRED', name, value)
    if name == 'permit_no':
        if not text.isascii():
            raise FieldRuleError('PERMIT_FORMAT', name, value)
        text = text.upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9/-]{0,39}', text):
            raise FieldRuleError('PERMIT_FORMAT', name, value)
    elif name == 'issued_on':
        parse_issue_date(text)
    elif name == 'status' and text not in STATUS_VALUES:
        raise FieldRuleError('STATUS', name, value)
    elif name == 'district' and text not in DISTRICT_VALUES:
        raise FieldRuleError('DISTRICT', name, value)
    return text
