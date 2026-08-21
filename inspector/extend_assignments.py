#!/usr/bin/env python3
"""Add labellers to a campaign that is already in flight, touching nobody else.

create_assignments.py deals a whole campaign at once: it re-shuffles every
unlabelled point across every labeller. Re-running it to make room for two new
people would silently re-deal the four assignments that are currently being
worked through. This script exists so that never happens.

It reads the live manifest, copies every existing record through byte-for-byte
(same point ids, same assignment_id, same counts), and gives the NEW labellers
only points that no existing assignment contains -- the Round 4 top-up. The
existing four cannot tell the difference: their link, their point list, their
browser-storage key and their Google Sheet upsert key are all unchanged.

QA overlap is drawn from the points the existing labellers already hold, so the
new pair is measured against the established team rather than only against each
other. Those are second reads: the point stays in its original owner's list too.

Example:
    py -3 inspector/extend_assignments.py \
        --add SM TB --per-labeler 600 --qa-overlap 72 \
        --base-url https://geethen.github.io/ThicketCondition/

Verify the result with:  node inspector/verify_round4_migration.mjs
"""
import argparse
import csv
import json
import os
from collections import Counter, defaultdict

from build import ASSIGNMENTS, campaign_dataset_id, load_points
from create_assignments import CODE_RE, assignment_url, digest


def spread(points, n, seed, tag):
    """Take n points spread evenly across strata, deterministically.

    Round-robin over strata ordered by size takes from the biggest first, so a
    small stratum is never emptied to satisfy a quota it cannot fill.
    """
    by_stratum = defaultdict(list)
    for p in points:
        by_stratum[p['s']].append(p)
    for s in by_stratum:
        by_stratum[s].sort(key=lambda p: digest(seed, tag, s, p['id']))
    order = sorted(by_stratum, key=lambda s: (-len(by_stratum[s]), s))
    taken, i = [], 0
    while len(taken) < n:
        progressed = False
        for s in order:
            if len(taken) >= n:
                break
            if i < len(by_stratum[s]):
                taken.append(by_stratum[s][i])
                progressed = True
        if not progressed:
            raise ValueError(f'only {len(taken)} points available, needed {n}')
        i += 1
    return taken


def extend(manifest, pts, new_codes, per_labeler, qa_overlap, seed):
    existing = manifest['labelers']
    for code in new_codes:
        if not CODE_RE.fullmatch(code):
            raise ValueError(f'labeler code may contain only letters, numbers, _ and -: {code}')
        if code.lower() in {c.lower() for c in existing}:
            raise ValueError(f'{code} already has an assignment; this script only adds')

    held = {i for r in existing.values() for i in r['point_ids']}
    by_id = {p['id']: p for p in pts}
    unassigned = [p for p in pts if p['src'] == 'new' and p['id'] not in held]
    need = per_labeler * len(new_codes)
    if len(unassigned) < need:
        raise ValueError(f'{len(unassigned)} unassigned points but {need} needed')
    if len(unassigned) > need:
        print(f'  note: {len(unassigned) - need} unassigned points left over')

    # Primary work: deal the new points across the new codes, stratum by stratum,
    # so each person's list mirrors the design rather than a corner of the map.
    pool = spread(unassigned, need, seed, 'r4-primary')
    per_stratum = defaultdict(list)
    for p in pool:
        per_stratum[p['s']].append(p)
    primary = {c: [] for c in new_codes}
    slot = 0
    for s in sorted(per_stratum):
        for p in sorted(per_stratum[s], key=lambda q: digest(seed, 'r4-deal', s, q['id'])):
            primary[new_codes[slot % len(new_codes)]].append(p['id'])
            slot += 1

    # QA: second reads on points an existing labeller already owns.
    qa = {c: [] for c in new_codes}
    if qa_overlap:
        held_points = [by_id[i] for i in sorted(held)]
        picked = spread(held_points, qa_overlap * len(new_codes), seed, 'r4-qa')
        for k, p in enumerate(sorted(picked, key=lambda q: digest(seed, 'r4-qa-deal', q['id']))):
            qa[new_codes[k % len(new_codes)]].append(p['id'])

    out = dict(manifest)
    out['labelers'] = dict(existing)
    ds_id = manifest['dataset']
    for code in new_codes:
        ids = sorted(set(primary[code]) | set(qa[code]))
        assert len(ids) == len(primary[code]) + len(qa[code]), 'QA collided with primary'
        out['labelers'][code] = {
            'assignment_id': digest(seed, ds_id, manifest['campaign'], code,
                                    ','.join(map(str, ids)))[:16],
            'point_ids': ids,
            'primary_count': len(primary[code]),
            'qa_overlap_count': len(qa[code]),
        }
    out['qa_overlap_point_ids'] = sorted(
        set(manifest.get('qa_overlap_point_ids', []))
        | {i for c in new_codes for i in qa[c]})
    out.setdefault('extensions', []).append({
        'added': list(new_codes), 'per_labeler': per_labeler,
        'qa_overlap_per_labeler': qa_overlap, 'seed': seed,
        'new_points': sorted({i for c in new_codes for i in primary[c]}),
    })
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default=ASSIGNMENTS)
    parser.add_argument('--add', nargs='+', required=True, metavar='CODE',
                        help='labeler codes to add')
    parser.add_argument('--per-labeler', type=int, required=True,
                        help='primary (first-read) points each new labeler gets')
    parser.add_argument('--qa-overlap', type=int, default=0,
                        help='extra second-read points each new labeler gets, taken '
                             'from what the existing labellers already hold')
    parser.add_argument('--seed', help='defaults to the campaign name + the codes added')
    parser.add_argument('--output', help='defaults to --manifest, rewritten in place')
    parser.add_argument('--base-url', help='deployed inspector URL; prints the new links')
    parser.add_argument('--links-output')
    args = parser.parse_args(argv)

    with open(args.manifest, encoding='utf-8') as fh:
        manifest = json.load(fh)
    pts = load_points()
    ds_id = campaign_dataset_id(pts)
    if manifest['dataset'] != ds_id:
        raise ValueError(f'manifest is for dataset {manifest["dataset"]}, current build '
                         f'is {ds_id}; extending across a re-draw is not safe')

    before = json.dumps(manifest['labelers'], sort_keys=True)
    seed = args.seed or f'{manifest["campaign"]}+{"+".join(args.add)}'
    out = extend(manifest, pts, args.add, args.per_labeler, args.qa_overlap, seed)

    kept = {c: r for c, r in out['labelers'].items() if c in manifest['labelers']}
    if json.dumps(kept, sort_keys=True) != before:
        raise AssertionError('an existing assignment changed -- refusing to write')

    path = args.output or args.manifest
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=2)
        fh.write('\n')

    print(f'wrote {path}')
    valid = {p['id'] for p in pts}
    occurrence = Counter(i for r in out['labelers'].values() for i in r['point_ids'])
    for code, record in out['labelers'].items():
        mark = ' (unchanged)' if code in manifest['labelers'] else ' (NEW)'
        assert set(record['point_ids']) <= valid, f'{code} holds unknown point ids'
        print(f'  {code}: {len(record["point_ids"])} points '
              f'({record["primary_count"]} primary, {record["qa_overlap_count"]} overlap)'
              f'{mark}')
    print(f'  {len(occurrence)} unique points assigned, '
          f'{sum(1 for n in occurrence.values() if n > 1)} held by two labellers')

    if args.base_url:
        links = args.links_output or os.path.join(
            os.path.dirname(os.path.abspath(path)), 'assignment_links.csv')
        with open(links, 'w', encoding='utf-8', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['campaign', 'assignment', 'assigned_points', 'primary_points',
                        'qa_overlap_points', 'url'])
            for code, record in out['labelers'].items():
                url = assignment_url(args.base_url, code)
                w.writerow([out['campaign'], code, len(record['point_ids']),
                            record['primary_count'], record['qa_overlap_count'], url])
                print(f'    {code}: {url}')
        print(f'wrote link register: {links}')


if __name__ == '__main__':
    main()
