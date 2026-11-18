from urllib.parse import urlsplit, urlunsplit
from ipaddress import ip_address
import re


class SourceUrlError(ValueError):
    pass


def normalize_source_url(value):
    if not isinstance(value, str) or not value or any(
            c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise SourceUrlError('URL must be a nonempty address without whitespace')
    if '#' in value or '\\' in value:
        raise SourceUrlError('fragments and backslashes are forbidden')
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise SourceUrlError(str(exc)) from exc
    if parts.scheme.lower() not in ('http', 'https') or not host:
        raise SourceUrlError('absolute HTTP(S) URL required')
    if parts.username is not None or parts.password is not None:
        raise SourceUrlError('credentials are forbidden')
    if parts.netloc.endswith(':') or (port is not None and not 0 < port < 65536):
        raise SourceUrlError('invalid port')
    host = host.lower()
    try:
        if ':' in host or re.fullmatch(r'[0-9.]+', host):
            ip_address(host)
        else:
            host = host.encode('idna').decode('ascii')
            labels = host.rstrip('.').split('.')
            if len(host) > 253 or not all(re.fullmatch(
                    r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
                raise ValueError('invalid hostname')
        if re.search(r'%(?![0-9A-Fa-f]{2})', parts.path + parts.query):
            raise ValueError('invalid percent escape')
        if any(c in value for c in '<>"{}|^' + chr(96)):
            raise ValueError('invalid URL character')
    except (ValueError, UnicodeError) as exc:
        raise SourceUrlError(str(exc)) from exc
    if ':' in host:
        host = '[' + host + ']'
    authority = host + (f':{port}' if port is not None else '')
    return urlunsplit((parts.scheme.lower(), authority, parts.path, parts.query, ''))
