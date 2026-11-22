from math import fsum

from .volume_curve import volume
from .water_levels import read_level


def split_cycles(model, cycles):
    """Split at actual readings and supply boundaries; never span either silently."""
    windows = []
    for row in cycles:
        a, b = row['start'], row['end']
        relevant = [r for r in model['supplies'] if r['start'] < b and a < r['end']]
        points = {a, b}
        points.update(r['time'] for r in model['samples']['chamber'] if a < r['time'] < b)
        for r in relevant:
            points.update(max(a, min(b, r[k])) for k in ('start', 'end'))
            points.update(s['time'] for s in model['samples'][r['station']]
                          if max(a, r['start']) < s['time'] < min(b, r['end']))
        steps = []
        boundaries = sorted(points)
        for i, (first, last) in enumerate(zip(boundaries, boundaries[1:]), 1):
            source = next((r for r in relevant if r['start'] <= first < last <= r['end']), None)
            h0, p0 = read_level(model, 'chamber', first)
            h1, p1 = read_level(model, 'chamber', last)
            middle, pm = read_level(model, 'chamber', (first+last)/2)
            v0, v1 = (volume(model['sections'], h) for h in (h0, h1))
            change = None if middle is None or v0 is None or v1 is None else v1-v0
            steps.append(dict(id=f"{row['id']}/{i}", cycle=row['id'], start=first, end=last,
                              h0=h0, h1=h1, volume=change, source=source,
                              state='blind' if change is None else 'known', evidence=list(dict.fromkeys(p0+pm+p1))))
        windows.extend(steps)
    return windows


def hydraulic_evidence(model, steps):
    result = []
    for row in steps:
        source = row['source']
        evidence = dict(chamber=row['evidence'], source=source['id'] if source else None)
        low = high = None
        if source:
            first, last = row['start'], row['end']
            values = [read_level(model, source['station'], t) for t in (first, (first+last)/2, last)]
            evidence['outside'] = list(dict.fromkeys(key for _, ids in values for key in ids))
            if row['volume'] is not None and all(value is not None for value, _ in values):
                x, y = values[0][0]-row['h0'], values[2][0]-row['h1']
                low, high = min(x, y), max(x, y)
        state = ('unattributed' if row['volume'] is not None else 'blind') if not source else (
            'blind' if low is None else 'opposed' if low < 0 or row['volume'] < 0 else 'attributed')
        result.append(dict(id=row['id'], cycle=row['cycle'], start=row['start'], end=row['end'],
                           lo=low, hi=high, volume=row['volume'], state=state, evidence=evidence))
    return result


def partition_balance(cycles, steps):
    result = []
    for cycle in cycles:
        rows = [r for r in steps if r['cycle'] == cycle['id']]
        unknown = [r['id'] for r in rows if r['volume'] is None]
        known = fsum(r['volume'] for r in rows if r['volume'] is not None)
        accounted = fsum(r['volume'] for r in rows if r['state'] == 'attributed')
        all_attributable = not any(r['state'] in ('blind', 'opposed') for r in rows)
        result.append(dict(id=cycle['id'], volume=None if unknown else known, accounted=accounted,
                           residual=known-accounted if all_attributable else None,
                           state='partial' if unknown or not all_attributable else 'known',
                           evidence={'steps': [r['id'] for r in rows], 'unknown': unknown}))
    return result
