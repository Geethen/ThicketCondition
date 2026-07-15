#!/usr/bin/env python3
"""Create deterministic, stratum-balanced inspector assignments.

Example:
    py -3 inspector/create_assignments.py --campaign thicket-2026-r1 \
        --labelers GS AB CD EF --overlap 0.12
"""
import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from build import ASSIGNMENTS, CSV, dataset_id, load_points


CODE_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def digest(seed, *parts):
    text = '|'.join([seed, *map(str, parts)]).encode()
    return hashlib.sha256(text).hexdigest()


def overlap_quotas(points, fraction):
    by_stratum = Counter(p['s'] for p in points)
    target = round(len(points) * fraction)
    raw = {s: n * fraction for s, n in by_stratum.items()}
    quotas = {s: int(v) for s, v in raw.items()}
    remaining = target - sum(quotas.values())
    order = sorted(raw, key=lambda s: (-(raw[s] - quotas[s]), s))
    for s in order[:remaining]:
        quotas[s] += 1
    return quotas


def create_manifest(points, labelers, campaign, seed, overlap):
    if not labelers:
        raise ValueError('provide at least one labeler code')
    if len({c.lower() for c in labelers}) != len(labelers):
        raise ValueError('labeler codes must be unique, ignoring case')
    if any(not CODE_RE.fullmatch(c) for c in labelers):
        raise ValueError('labeler codes may contain only letters, numbers, _ and -')
    if not 0 <= overlap < 1:
        raise ValueError('--overlap must be at least 0 and less than 1')
    if overlap and len(labelers) < 2:
        raise ValueError('QA overlap requires at least two labelers')

    ds_id = dataset_id(points)
    assigned = {code: set() for code in labelers}
    primary_counts = Counter()
    overlap_counts = Counter()
    overlap_by_stratum = Counter()
    overlap_ids = set()
    primary_for = {}
    quotas = overlap_quotas(points, overlap)
    strata = sorted({p['s'] for p in points})

    for si, stratum in enumerate(strata):
        group = [p for p in points if p['s'] == stratum]
        primary_order = sorted(group, key=lambda p: digest(seed, 'primary', stratum, p['id']))
        qa_order = sorted(group, key=lambda p: digest(seed, 'overlap', stratum, p['id']))
        qa_ids = {p['id'] for p in qa_order[:quotas[stratum]]}
        overlap_ids.update(qa_ids)
        for pos, point in enumerate(primary_order):
            primary_index = (pos + si) % len(labelers)
            primary = labelers[primary_index]
            assigned[primary].add(point['id'])
            primary_counts[primary] += 1
            primary_for[point['id']] = primary

    # Allocate second reads after all primary work is known. Minimise per-stratum
    # and total overlap counts so QA work is as balanced as the primary work.
    for stratum in strata:
        qa_points = [p for p in points if p['s'] == stratum and p['id'] in overlap_ids]
        qa_points.sort(key=lambda p: digest(seed, 'second', stratum, p['id']))
        for point in qa_points:
            primary = primary_for[point['id']]
            candidates = [c for c in labelers if c != primary]
            second = min(candidates, key=lambda c: (
                overlap_by_stratum[(c, stratum)], overlap_counts[c],
                len(assigned[c]), digest(seed, 'tie', point['id'], c)))
            assigned[second].add(point['id'])
            overlap_counts[second] += 1
            overlap_by_stratum[(second, stratum)] += 1

    records = {}
    for code in labelers:
        ids = sorted(assigned[code])
        assignment_id = digest(seed, ds_id, campaign, code, ','.join(map(str, ids)))[:16]
        records[code] = {
            'assignment_id': assignment_id,
            'point_ids': ids,
            'primary_count': primary_counts[code],
            'qa_overlap_count': overlap_counts[code],
        }
    return {
        'version': 1,
        'dataset': ds_id,
        'campaign': campaign,
        'seed': seed,
        'overlap_fraction': overlap,
        'labelers': records,
        'qa_overlap_point_ids': sorted(overlap_ids),
    }


def assignment_url(base_url, code):
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['assignment'] = code
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, help='stable campaign name, e.g. thicket-2026-r1')
    parser.add_argument('--labelers', nargs='+', required=True, metavar='CODE')
    parser.add_argument('--overlap', type=float, default=0.12,
                        help='fraction deliberately assigned twice for QA (default: 0.12)')
    parser.add_argument('--seed', help='deterministic seed (defaults to campaign name)')
    parser.add_argument('--output', default=ASSIGNMENTS)
    parser.add_argument('--base-url', help='deployed inspector URL; generates shareable links')
    parser.add_argument('--links-output', help='CSV path for the link register')
    args = parser.parse_args(argv)

    points = load_points()
    manifest = create_manifest(points, args.labelers, args.campaign,
                               args.seed or args.campaign, args.overlap)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2)
        fh.write('\n')
    print(f'wrote {args.output}: {len(points)} unique points, '
          f'{len(manifest["qa_overlap_point_ids"])} deliberate QA overlaps')
    for code, record in manifest['labelers'].items():
        print(f'  {code}: {len(record["point_ids"])} points '
              f'({record["primary_count"]} primary, {record["qa_overlap_count"]} overlap)')
    if args.base_url:
        links_path = args.links_output or os.path.join(
            os.path.dirname(os.path.abspath(args.output)), 'assignment_links.csv')
        with open(links_path, 'w', encoding='utf-8', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow(['campaign','assignment','assigned_points','primary_points','qa_overlap_points','url'])
            for code, record in manifest['labelers'].items():
                url = assignment_url(args.base_url, code)
                writer.writerow([args.campaign, code, len(record['point_ids']),
                                 record['primary_count'], record['qa_overlap_count'], url])
                print(f'    {url}')
        print(f'wrote shareable link register: {links_path}')


if __name__ == '__main__':
    main()
