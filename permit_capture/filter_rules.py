from dataclasses import dataclass, asdict
import json
from .field_rules import normalize_field, parse_issue_date


class FilterError(ValueError):
    pass


@dataclass(frozen=True)
class Filters:
    permit_no: str | None = None
    district: str | None = None
    issued_from: str | None = None
    issued_to: str | None = None

    def as_dict(self):
        return asdict(self)


def parse_filters(value=None):
    if isinstance(value, Filters):
        value = value.as_dict()
    if value is None:
        value = {}
    allowed = {'permit_no', 'district', 'issued_from', 'issued_to'}
    try:
        if not isinstance(value, dict) or set(value) - allowed:
            raise FilterError('unknown filter')
        normalized = {key: None for key in allowed}
        for key, item in value.items():
            if item is None:
                continue
            if key in ('permit_no', 'district'):
                normalized[key] = normalize_field(key, item)
            else:
                normalized[key] = parse_issue_date(item).isoformat()
        if normalized['issued_from'] and normalized['issued_to']:
            if normalized['issued_from'] > normalized['issued_to']:
                raise FilterError('date range reversed')
        return Filters(**normalized)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, FilterError):
            raise
        raise FilterError(str(exc)) from exc


def canonical_filter_bytes(filters):
    return json.dumps(parse_filters(filters).as_dict(), sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def validate_page_size(limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise FilterError('limit must be an integer in 1..1000')
    return limit
