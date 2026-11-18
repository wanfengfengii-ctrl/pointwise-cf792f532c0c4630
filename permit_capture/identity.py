import unicodedata


class IdentityConflict(ValueError):
    pass


def make_row_key(explicit_key, permit_no):
    if explicit_key is None:
        return permit_no
    if not isinstance(explicit_key, str):
        raise IdentityConflict('row key must be text')
    key = unicodedata.normalize('NFC', explicit_key).strip()
    if not key:
        raise IdentityConflict('explicit row key must not be empty')
    return key


def assert_unique_keys(records):
    seen = {}
    for record in records:
        position = (record.page_no, record.row_no)
        if record.row_key in seen:
            raise IdentityConflict(f'duplicate {record.row_key!r}: {seen[record.row_key]} and {position}')
        seen[record.row_key] = position
