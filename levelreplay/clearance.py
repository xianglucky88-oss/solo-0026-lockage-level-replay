from bisect import bisect_left

from .contract import invariant, num, obj, records, unique_order
from .water_levels import read_level

# 缺证原因按固定顺序输出，保证同一区段生成稳定标识与可比对结果
GAP_REASONS = ('gauge-blind', 'track-blind', 'bed-outside', 'bed-blind', 'draft-blind')
EPS = 1e-9


def read_clearance(review):
    """解析并校验 review.clearance 航迹净空复核输入。"""
    block = obj(review.get('clearance'), '航迹净空复核数据')
    bed = []
    for row in records(block.get('bed'), '床面剖面', 240, 2):
        bed.append(dict(id=row['id'],
                        x=num(row.get('station'), '床面沿程坐标', -100000, 100000),
                        z=num(row.get('elevation'), '床面高程', -100, 100)))
    unique_order(bed, 'x', '床面剖面沿程坐标')
    track = []
    for row in records(block.get('track'), '航迹点', 600, 2):
        track.append(dict(id=row['id'],
                          time=num(row.get('time'), '航迹时刻', 0),
                          x=num(row.get('station'), '航迹沿程坐标', -100000, 100000)))
    unique_order(track, 'time', '航迹时刻')
    drafts = []
    for row in records(block.get('draft_regimes'), '吃水档', 60, 1):
        drafts.append(dict(id=row['id'],
                           start=num(row.get('start'), '吃水生效时刻', 0),
                           draft=num(row.get('draft'), '吃水值', 0, 100)))
    unique_order(drafts, 'start', '吃水档生效时刻')
    bed_gap = num(block.get('bed_gap'), '床面最大插值间隔', 1e-9, 1e6)
    track_gap = num(block.get('track_gap'), '航迹最大插值间隔', 1e-9, 1e6)
    limit = num(block.get('clearance_limit'), '要求最小净空', 0, 100)
    invariant(track[-1]['time'] - track[0]['time'] > 0, '航迹必须覆盖一个正长度时段')
    return dict(bed=bed, track=track, drafts=drafts, bed_gap=bed_gap,
                track_gap=track_gap, limit=limit)


def interp(points, key, value_key, at, gap):
    """分段线性插值；只在间隔不超过 gap 时内插，越界返回 (None, [])，超间隔返回两端见证。"""
    index = bisect_left(points, at, key=lambda row: row[key])
    if index < len(points) and points[index][key] == at:
        row = points[index]
        return row[value_key], [row['id']]
    if index == 0 or index == len(points):
        return None, []
    first, second = points[index - 1], points[index]
    if second[key] - first[key] > gap:
        return None, [first['id'], second['id']]
    ratio = (at - first[key]) / (second[key] - first[key])
    return first[value_key] + (second[value_key] - first[value_key]) * ratio, [first['id'], second['id']]


def draft_at(drafts, at):
    """吃水档在 at 时刻生效的值；生效时刻采用新档，早于首档返回不明。"""
    index = bisect_left(drafts, at, key=lambda row: row['start'])
    if index < len(drafts) and drafts[index]['start'] == at:
        row = drafts[index]
        return row['draft'], [row['id']]
    if index == 0:
        return None, []
    row = drafts[index - 1]
    return row['draft'], [row['id']]


def evaluate_at(at, model, spec):
    """在同一时刻汇集水尺、航迹、床面、吃水四路证据，返回缺证原因、见证和各高程。"""
    reasons = set()
    water, gauge_ids = read_level(model, 'chamber', at)
    if water is None:
        reasons.add('gauge-blind')
    x, track_ids = interp(spec['track'], 'time', 'x', at, spec['track_gap'])
    bed_z, bed_ids = None, []
    if x is None:
        reasons.add('track-blind')
    else:
        bed_z, bed_ids = interp(spec['bed'], 'x', 'z', x, spec['bed_gap'])
        if bed_z is None:
            reasons.add('bed-outside' if not bed_ids else 'bed-blind')
    draft, draft_ids = draft_at(spec['drafts'], at)
    if draft is None:
        reasons.add('draft-blind')
    evidence = dict(gauge=list(gauge_ids), track=list(track_ids),
                    bed=list(bed_ids), draft=list(draft_ids))
    clearance = None if reasons else water - bed_z - draft
    values = dict(water=water, bed=bed_z, draft=draft, x=x, clearance=clearance)
    return reasons, evidence, values


def critical_times(spec, model):
    """航迹样点、水尺折点、吃水生效时刻，以及航迹穿过床面折点的时刻。"""
    track = spec['track']
    lo, hi = track[0]['time'], track[-1]['time']
    moments = {point['time'] for point in track}
    for row in model['samples']['chamber']:
        if lo <= row['time'] <= hi:
            moments.add(row['time'])
    for row in spec['drafts']:
        if lo <= row['start'] <= hi:
            moments.add(row['start'])
    ordered = sorted(moments)
    extras = []
    for first, second in zip(track, track[1:]):
        if first['x'] == second['x']:
            continue  # 停留：折点证据已由两端航迹样点承载
        for point in spec['bed']:
            crest = point['x']
            if min(first['x'], second['x']) < crest < max(first['x'], second['x']):
                hit = first['time'] + (crest - first['x']) * (second['time'] - first['time']) / (second['x'] - first['x'])
                if all(abs(hit - moment) > EPS for moment in ordered + extras):
                    extras.append(hit)
    return sorted(ordered + extras)


def time_label(at):
    return str(int(at)) if float(at).is_integer() else repr(round(at, 9))


def merge_evidence(target, extra):
    for key, ids in extra.items():
        for ident in ids:
            if ident not in target[key]:
                target[key].append(ident)


def empty_evidence():
    return dict(gauge=[], track=[], bed=[], draft=[])


def kind_of(at, spec, model):
    track_times = {point['time'] for point in spec['track']}
    gauge_times = {row['time'] for row in model['samples']['chamber']}
    draft_starts = {row['start'] for row in spec['drafts']}
    if any(abs(at - value) <= EPS for value in track_times):
        return 'track'
    if any(abs(at - value) <= EPS for value in gauge_times):
        return 'gauge'
    if any(abs(at - value) <= EPS for value in draft_starts):
        return 'draft'
    return 'bed'


def analyze_clearance(review, model):
    spec = read_clearance(review)
    times = critical_times(spec, model)
    evaluations = [evaluate_at(at, model, spec) for at in times]
    index_by_time = {at: i for i, at in enumerate(times)}
    limit = spec['limit']

    checks = []
    for at, (reasons, evidence, values) in zip(times, evaluations):
        if reasons:
            continue
        kind = kind_of(at, spec, model)
        checks.append(dict(id=f'clear/{time_label(at)}/{kind}', time=at, kind=kind,
                           water=values['water'], bed=values['bed'], x=values['x'],
                           draft=values['draft'], clearance=values['clearance'],
                           state='under' if values['clearance'] < limit else 'known',
                           evidence=evidence))
        # 吃水跳变：保留变更前一侧的值，禁止跨跳变线性插值
        draft_index = bisect_left(spec['drafts'], at, key=lambda row: row['start'])
        if (draft_index < len(spec['drafts']) and spec['drafts'][draft_index]['start'] == at
                and draft_index > 0):
            previous = spec['drafts'][draft_index - 1]
            before_clearance = values['water'] - values['bed'] - previous['draft']
            before_evidence = dict(evidence)
            before_evidence['draft'] = [previous['id']]
            checks.append(dict(id=f'clear/{time_label(at)}/draft-before', time=at,
                               kind='draft-before', water=values['water'], bed=values['bed'],
                               x=values['x'], draft=previous['draft'],
                               clearance=before_clearance,
                               state='under' if before_clearance < limit else 'known',
                               evidence=before_evidence))

    # 以相邻临界时刻之间的中点判定每个单元是否缺证，并合并同因相邻缺证单元
    cell_blind = []
    gaps = []
    current = None
    for i, (left_at, right_at) in enumerate(zip(times, times[1:])):
        midpoint = (left_at + right_at) / 2
        reasons, mid_evidence, _ = evaluate_at(midpoint, model, spec)
        cell_blind.append(bool(reasons))
        if not reasons:
            current = None
            continue
        key = tuple(name for name in GAP_REASONS if name in reasons)
        left_reasons = evaluations[i][0]
        if current is not None and current['end'] == left_at and current['_reasons'] == key \
                and left_reasons:
            current['end'] = right_at
            merge_evidence(current['evidence'], mid_evidence)
            merge_evidence(current['evidence'], evaluations[i + 1][1])
            continue
        current = dict(start=left_at, end=right_at, reasons=list(key),
                       evidence=empty_evidence(), _reasons=key)
        merge_evidence(current['evidence'], evaluations[i][1])
        merge_evidence(current['evidence'], mid_evidence)
        merge_evidence(current['evidence'], evaluations[i + 1][1])
        gaps.append(current)
    for row in gaps:
        row.pop('_reasons')
        row['id'] = None  # 占位，下面按时间顺序编号
    for number, row in enumerate(gaps, 1):
        row['id'] = f'gap/{number}'
        row['state'] = 'blind'

    # 只在全程有证的单元内逐段求低于限值的时段，吃水跳变两侧作为同刻节点分别参与
    pieces = []

    def add_piece(start, end, low, evidence):
        pieces.append(dict(start=start, end=end, low=low, evidence=evidence))

    for row in checks:
        if row['clearance'] < limit:
            add_piece(row['time'], row['time'], row['clearance'], row['evidence'])
    for previous, row in zip(checks, checks[1:]):
        if abs(previous['time'] - row['time']) <= EPS:
            continue  # 吃水跳变的同刻双侧点，低值已作为点片段登记
        ia, ib = index_by_time[previous['time']], index_by_time[row['time']]
        if any(cell_blind[i] for i in range(ia, ib)):
            continue
        ca, cb = previous['clearance'], row['clearance']
        under_a, under_b = ca < limit, cb < limit
        if not under_a and not under_b:
            continue
        if under_a and under_b:
            start, end = previous['time'], row['time']
        elif under_a:
            start = previous['time']
            end = previous['time'] + (limit - ca) / (cb - ca) * (row['time'] - previous['time'])
        else:
            start = previous['time'] + (limit - ca) / (cb - ca) * (row['time'] - previous['time'])
            end = row['time']
        evidence = empty_evidence()
        merge_evidence(evidence, previous['evidence'])
        merge_evidence(evidence, row['evidence'])
        add_piece(start, end, min(ca, cb), evidence)

    breaches = []
    for piece in sorted(pieces, key=lambda item: (item['start'], item['end'])):
        if breaches and piece['start'] <= breaches[-1]['end'] + EPS:
            target = breaches[-1]
            target['end'] = max(target['end'], piece['end'])
            target['min_clearance'] = min(target['min_clearance'], piece['low'])
            merge_evidence(target['evidence'], piece['evidence'])
        else:
            breaches.append(dict(start=piece['start'], end=piece['end'],
                                 min_clearance=piece['low'], state='under',
                                 evidence=piece['evidence']))
    for number, row in enumerate(breaches, 1):
        row['id'] = f'breach/{number}'

    if checks:
        lowest = min(checks, key=lambda row: (row['clearance'], row['time']))
        min_clearance, min_time, min_evidence = lowest['clearance'], lowest['time'], lowest['evidence']
    else:
        min_clearance = min_time = None
        min_evidence = empty_evidence()
    if breaches:
        state = 'under'
    elif gaps or not checks:
        state = 'blind'
    else:
        state = 'verified'
    verdict = dict(id='clearance-verdict', state=state, min_clearance=min_clearance,
                   min_time=min_time, limit=limit, breaches=len(breaches), gaps=len(gaps),
                   evidence=min_evidence)

    order = ['id', 'time', 'kind', 'water', 'bed', 'x', 'draft', 'clearance', 'state', 'evidence']
    checks = [{key: row[key] for key in order} for row in checks]
    gaps = [dict(id=row['id'], start=row['start'], end=row['end'], reasons=row['reasons'],
                 state=row['state'], evidence=row['evidence']) for row in gaps]
    breaches = [dict(id=row['id'], start=row['start'], end=row['end'],
                     min_clearance=row['min_clearance'], state=row['state'],
                     evidence=row['evidence']) for row in breaches]
    return dict(clearance=checks, clearance_gaps=gaps,
                clearance_breaches=breaches, clearance_verdict=[verdict])
