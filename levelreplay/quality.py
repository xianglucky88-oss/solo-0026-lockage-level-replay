from collections import defaultdict

from .contract import ident, invariant, num, records
from .water_levels import STATIONS, read_level


def read_probes(review, model):
    gates = {r['id']: r for r in model['gates']}
    for probe in records(review.get('probes', []), '门位复核窗', 24):
        gate = ident(probe.get('gate'), '复核门位')
        invariant(gate in gates and gates[gate]['event'].endswith('-open'), '复核窗需指向开门事件')
        delta = num(probe.get('delta'), '时钟不确定量', 0, 60)
        time = gates[gate]['time']
        invariant(time >= delta, '复核窗口不能提前到负时刻')
        yield probe['id'], gates[gate], time-delta, time+delta


def head_envelope(model, probes):
    result = []
    for key, gate, a, b in probes:
        station = gate['event'].split('-')[0]
        stations = ('chamber', station)
        times = sorted({a, b} | {r['time'] for s in stations for r in model['samples'][s] if a < r['time'] < b})
        times = sorted(set(times) | {(x+y)/2 for x, y in zip(times, times[1:])})
        evidence, differences = set(), []
        for time in times:
            c, cp = read_level(model, 'chamber', time)
            s, sp = read_level(model, station, time)
            evidence.update(cp+sp)
            if c is not None and s is not None:
                differences.append(c-s)
        lo = hi = None
        if len(differences) == len(times):
            low, high = min(differences), max(differences)
            hi = max(abs(low), abs(high))
            lo = 0 if low <= 0 <= high else min(abs(low), abs(high))
        result.append(dict(id=key, gate=gate['id'], start=a, end=b, lo=lo, hi=hi,
                           state='blind' if lo is None else 'over' if lo > model['limit'] else
                           'verified' if hi <= model['limit'] else 'mixed', evidence=sorted(evidence)))
    return result


def station_gaps(model, edges):
    result = []
    for station in STATIONS:
        rows = model['samples'][station]
        for a, b in zip(rows, rows[1:]):
            if b['time']-a['time'] <= model['max_gap']:
                continue
            events = [r['id'] for r in edges if r['station'] in (station,) or station == 'chamber'
                      if a['time'] < r['time'] < b['time']]
            result.append(dict(id=f"{station}/{a['id']}", station=station, start=a['time'], end=b['time'],
                               count=len(events), state='blind', evidence={'samples': [a['id'], b['id']], 'events': events}))
    return result


def gate_disposition(facts, heads):
    cycle_by_gate = {g['id']: cycle[0]['id'] for cycle in facts['cycles'] for g in cycle}
    by_head = {r['id']: r['state'] for r in heads}
    return [dict(id=r['id'], cycle=cycle_by_gate.get(r['id']), station=r['station'], time=r['time'],
                 state='cycle' if r['id'] in cycle_by_gate else 'sequence',
                 evidence={'head': by_head.get(r['id']), 'samples': r['evidence']}) for r in facts['edge']]


def evidence_usage(edges, envelopes):
    uses = defaultdict(lambda: {'exact': [], 'envelope': []})
    for row in edges:
        for sample in set(row['evidence']):
            uses[sample]['exact'].append(row['id'])
    for row in envelopes:
        for sample in row['evidence']:
            uses[sample]['envelope'].append(row['id'])
    return [dict(id=sample, count=len(uses[sample]['exact'])+len(uses[sample]['envelope']),
                 state='shared' if len(uses[sample]['exact'])+len(uses[sample]['envelope']) > 1 else 'single',
                 evidence=uses[sample]) for sample in sorted(uses)]
