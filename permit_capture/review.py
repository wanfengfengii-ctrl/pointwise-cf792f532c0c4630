from dataclasses import dataclass, asdict
import argparse
import json
from pathlib import Path
from .archive import ArchiveRepository
from .reader import load_publication
from .filter_rules import parse_filters
from .query import select_records
from .replay import verify_publication, verify_snapshot, verify_review_bundle
from .export import write_review_bundle


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewRequest:
    publication_id: str
    filters: object


class ReviewService:
    def __init__(self, repository):
        self.repository = repository

    def pin(self, publication_id, filters=None):
        request = ReviewRequest(publication_id, parse_filters(filters))
        snapshot = load_publication(self.repository, publication_id)
        report = verify_snapshot(snapshot)
        if not report.ok:
            raise ReviewError('; '.join(report.errors))
        return request, snapshot

    def read(self, publication_id, filters=None, limit=100, cursor=None):
        try:
            request, snapshot = self.pin(publication_id, filters)
            return select_records(snapshot, request.filters, limit, cursor)
        except (ValueError, OSError) as exc:
            raise ReviewError(str(exc)) from exc

    def verify(self, publication_id):
        return verify_publication(self.repository, publication_id)

    def export_bundle(self, publication_id, destination, filters=None):
        try:
            request, snapshot = self.pin(publication_id, filters)
            return write_review_bundle(snapshot, destination, request.filters)
        except (ValueError, OSError) as exc:
            raise ReviewError(str(exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read or replay fixed permit publications')
    parser.add_argument('action', choices=('read', 'verify', 'export', 'offline'))
    parser.add_argument('--workdir')
    parser.add_argument('--publication')
    parser.add_argument('--filters', default='{}')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--cursor')
    parser.add_argument('--output')
    parser.add_argument('--bundle')
    args = parser.parse_args(argv)
    try:
        if args.action == 'offline':
            if not args.bundle:
                parser.error('--bundle required')
            report = verify_review_bundle(Path(args.bundle))
            print(json.dumps(asdict(report), ensure_ascii=False))
            return 0 if report.ok else 2
        if not args.workdir or not args.publication:
            parser.error('--workdir and --publication required')
        service = ReviewService(ArchiveRepository(Path(args.workdir) / 'archive'))
        filters = json.loads(args.filters)
        if args.action == 'read':
            result = asdict(service.read(args.publication, filters, args.limit, args.cursor))
        elif args.action == 'verify':
            report = service.verify(args.publication)
            print(json.dumps(asdict(report), ensure_ascii=False))
            return 0 if report.ok else 2
        else:
            if not args.output:
                parser.error('--output required')
            result = {'path': str(service.export_bundle(args.publication, args.output, filters))}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
