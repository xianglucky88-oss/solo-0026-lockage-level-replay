"""闸室失水离线诊断。

只在两扇门都确证关闭且有确证停供的共同时间段内，用连续有证的闸室水尺
和既有截面几何计算规定长度滚动窗的水量减少量；超过限值的窗终点区间记为
异常。诊断只读既有归约事实，不改变原始水量归账。
"""
from bisect import bisect_left, bisect_right
from math import inf

from .contract import ContractError, enum, ident, invariant, num, obj, records
from .volume_curve import volume

GATES = ('lower', 'upper')
STATES = ('open', 'closed')


def parse_config(source):
    initial = {}
    raw = source.get('initial', [])
    if not isinstance(raw, list) or len(raw) > len(GATES):
        raise ContractError('门初态应含 0 至 2 条记录')
    for row in raw:
        row = obj(row, '门初态')
        gate = enum(row.get('gate'), GATES, '门')
        invariant(gate not in initial, '每扇门的初态只能有一条')
        initial[gate] = dict(state=enum(row.get('state'), STATES, '门初态'),
                             witness=ident(row.get('witness'), '初态见证'))
    stops = [dict(id=r['id'], start=num(r.get('start'), '停供开始', 0), end=num(r.get('end'), '停供结束', 0))
             for r in records(source.get('stops', []), '停供时段', 120)]
    stops.sort(key=lambda r: r['start'])
    invariant(all(r['start'] < r['end'] for r in stops)
              and all(a['end'] <= b['start'] for a, b in zip(stops, stops[1:])),
              '停供时段必须为不重叠正长度区间')
    return dict(initial=initial, stops=stops,
                window=num(source.get('window'), '滚动窗长', 0.001),
                limit=num(source.get('limit'), '失水限值', 0))


def gate_timeline(events, initial):
    """重建单扇门的位置时段；重复或矛盾门位转为未知，保留到下一次有证门位。"""
    if initial is None:
        state, witness = 'unknown', None
    else:
        state, witness = initial['state'], initial['witness']
    start = 0
    segments = []
    for event in events:
        segments.append(dict(start=start, end=event['time'], state=state, witness=witness))
        if state == event['action']:
            state, witness = 'unknown', event['id']
        else:
            state, witness = event['action'], event['id']
        start = event['time']
    segments.append(dict(start=start, end=inf, state=state, witness=witness))
    return segments


def closed_periods(timelines):
    """两扇门都确证关闭的时间段，附各自门位见证。"""
    periods = []
    for lower in timelines['lower']:
        if lower['state'] != 'closed':
            continue
        for upper in timelines['upper']:
            if upper['state'] != 'closed':
                continue
            start = max(lower['start'], upper['start'])
            end = min(lower['end'], upper['end'])
            if start < end:
                periods.append(dict(start=start, end=end,
                                    gates=dict(lower=lower['witness'], upper=upper['witness'])))
    periods.sort(key=lambda row: row['start'])
    return periods


def gauge_runs(samples, max_gap):
    """按可插值时距把闸室水尺分成连续有证的读数段。"""
    runs = []
    for sample in samples:
        if runs and sample['time'] - runs[-1][-1]['time'] <= max_gap:
            runs[-1].append(sample)
        else:
            runs.append([sample])
    return runs


def volume_profile(run, sections):
    """连续有证读数段上水量曲线的全部折点：水尺样点及跨越的截面折点。"""
    levels = {sample['time']: sample['level'] for sample in run}
    for a, b in zip(run, run[1:]):
        rise = b['level'] - a['level']
        if rise == 0:
            continue
        for section in sections:
            mark = section['level']
            if min(a['level'], b['level']) < mark < max(a['level'], b['level']):
                levels[a['time'] + (b['time'] - a['time']) * (mark - a['level']) / rise] = mark
    times = sorted(levels)
    return times, [volume(sections, levels[t]) for t in times]


def profile_at(times, values, t):
    index = bisect_left(times, t)
    if index < len(times) and times[index] == t:
        return values[index]
    if index == 0 or index == len(times):
        return None
    (ta, va), (tb, vb) = (times[index - 1], values[index - 1]), (times[index], values[index])
    if va is None or vb is None:
        return None
    return va + (vb - va) * (t - ta) / (tb - ta)


def rolling_loss(times, values, start, end, window, limit):
    """[start,end] 内长度为 window 的滚动窗水量减少量。

    差值是窗终点的分段线性函数，折点取自水量折点及其平移，逐段精确求值，
    不取整秒；返回最大减少量及其窗终点，以及减少量超过限值的终点区间。
    """
    first, last = start + window, end
    points = {first, last}
    points.update(times[bisect_left(times, first):bisect_left(times, last)])
    points.update(t + window for t in times[bisect_left(times, start):bisect_left(times, last - window)])
    points = sorted(points)

    def decrease(t):
        before = profile_at(times, values, t - window)
        after = profile_at(times, values, t)
        return None if before is None or after is None else before - after

    measured = [decrease(p) for p in points]
    best = None
    spans = []
    open_span = None
    if len(points) == 1:
        only = measured[0]
        if only is not None:
            best = (only, points[0])
            if only > limit:
                spans.append([points[0], points[0], only])
        return best, spans
    for i in range(len(points) - 1):
        p, q = points[i], points[i + 1]
        if decrease((p + q) / 2) is None:
            open_span = None
            continue
        dp, dq = measured[i], measured[i + 1]
        if best is None or dp > best[0]:
            best = (dp, p)
        if dq > best[0]:
            best = (dq, q)
        over_p, over_q = dp > limit, dq > limit
        if over_p and over_q:
            sub = (p, q, max(dp, dq))
        elif over_p:
            sub = (p, p + (q - p) * (dp - limit) / (dp - dq), dp)
        elif over_q:
            sub = (p + (q - p) * (limit - dp) / (dq - dp), q, dq)
        else:
            open_span = None
            continue
        a, b, peak = sub
        if open_span is not None and open_span[1] == a:
            open_span[1] = b
            open_span[2] = max(open_span[2], peak)
        else:
            open_span = [a, b, peak]
            spans.append(open_span)
    return best, spans


def coverage_ids(samples, start, end):
    """在 [start,end] 上内插所需的水尺样点：区间内样点加两端夹点。"""
    if not samples:
        return []
    times = [sample['time'] for sample in samples]
    first = min(max(bisect_right(times, start) - 1, 0), len(samples) - 1)
    last = min(max(bisect_left(times, end), 0), len(samples) - 1)
    return [sample['id'] for sample in samples[first:last + 1]]


def analyze_period(row_id, stop, period, start, end, config, runs, samples):
    window, limit = config['window'], config['limit']
    best = None
    found = []
    for run_start, run_end, times, values in runs:
        first, last = max(start, run_start), min(end, run_end)
        if last - first < window:
            continue
        run_best, run_spans = rolling_loss(times, values, first, last, window, limit)
        if run_best is not None and (best is None or run_best[0] > best[0]):
            best = run_best
        found.extend(run_spans)
    evidence = dict(gates=period['gates'], stop=stop['id'], samples=coverage_ids(samples, start, end))
    if best is None:
        return dict(id=row_id, start=start, end=end, state='blind', worst=None, window=None,
                    spans=[], evidence=evidence)
    loss, moment = best
    spans = [dict(start=a, end=b, loss=peak, samples=coverage_ids(samples, a - window, b))
             for a, b, peak in found]
    if spans:
        evidence['samples'] = coverage_ids(samples, spans[0]['start'] - window, spans[-1]['end'])
    else:
        evidence['samples'] = coverage_ids(samples, moment - window, moment)
    return dict(id=row_id, start=start, end=end, state='loss' if spans else 'observed',
                worst=loss, window=[moment - window, moment], spans=spans, evidence=evidence)


def analyze_leakage(source, model):
    config = parse_config(source)
    events = {gate: [] for gate in GATES}
    for row in model['gates']:
        gate, action = row['event'].split('-')
        events[gate].append(dict(id=row['id'], time=row['time'],
                                 action='open' if action == 'open' else 'closed'))
    timelines = {gate: gate_timeline(events[gate], config['initial'].get(gate)) for gate in GATES}
    periods = closed_periods(timelines)
    samples = model['samples']['chamber']
    runs = []
    for run in gauge_runs(samples, model['max_gap']):
        times, values = volume_profile(run, model['sections'])
        runs.append((run[0]['time'], run[-1]['time'], times, values))
    rows = []
    for stop in config['stops']:
        count = 0
        for period in periods:
            start = max(stop['start'], period['start'])
            end = min(stop['end'], period['end'])
            if not start < end:
                continue
            count += 1
            rows.append(analyze_period(f"{stop['id']}/{count}", stop, period, start, end,
                                       config, runs, samples))
    rows.sort(key=lambda row: (row['start'], row['id']))
    return rows
