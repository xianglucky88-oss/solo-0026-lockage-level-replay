from .contract import ident, invariant, num, obj, records
from .water_levels import read_level

EPS = 1e-9


def normalize(pieces):
    """规范化状态分段：
    同态且正长度相接（含浮点容差）的段合并；同态重复的点段并入相邻段；
    异态的零长度端点段独立保留，不被相邻段吞掉。"""
    result = []
    for piece in sorted(pieces, key=lambda r: (r['start'], r['end'])):
        start, end, state = piece['start'], piece['end'], piece['state']
        if result:
            last = result[-1]
            touch = abs(start - last['end']) <= EPS
            positive = (end - start) > EPS or (last['end'] - last['start']) > EPS
            if last['state'] == state and touch and positive:
                last['end'] = max(last['end'], end)
                last['evidence']['samples'] = sorted(set(last['evidence']['samples'])
                                                      | set(piece['evidence']['samples']))
                continue
            if last['state'] == state and abs(last['start'] - start) <= EPS and abs(last['end'] - end) <= EPS:
                last['evidence']['samples'] = sorted(set(last['evidence']['samples'])
                                                      | set(piece['evidence']['samples']))
                continue
        result.append(dict(start=start, end=end, state=state, evidence=dict(piece['evidence'])))
    return result


def merge_intervals(intervals):
    """合并闭区间；长度为零的点区间予以保留。"""
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1] + EPS:
            if b > result[-1][1]:
                result[-1] = (result[-1][0], b)
        else:
            result.append((a, b))
    return result


def intersect_intervals(left, right):
    """两个已排序闭区间列表的交；孤立交点（点区间）保留。"""
    out = []
    i = j = 0
    while i < len(left) and j < len(right):
        a = max(left[i][0], right[j][0])
        b = min(left[i][1], right[j][1])
        if a <= b + EPS:
            out.append((a, b))
        if left[i][1] < right[j][1] - EPS:
            i += 1
        else:
            j += 1
    return merge_intervals(out)


def clip_intervals(intervals, lo, hi):
    kept = [(max(a, lo), min(b, hi)) for a, b in intervals if b >= lo - EPS and a <= hi + EPS]
    return merge_intervals(kept)


def read_alignments(review, facts):
    cycle_by_id = {cycle[0]['id']: cycle for cycle in facts['cycles']}
    parsed = []
    for row in records(obj(review, '复核数据').get('alignments', []), '整闸对时', 24):
        key = ident(row.get('id'), '整闸对时标识')
        cycle_id = ident(row.get('cycle'), '对时所属循环')
        invariant(cycle_id in cycle_by_id, '整闸对时需指向完整循环')
        cycle = cycle_by_id[cycle_id]
        low = num(row.get('offset_low'), '门机对时秒差下界', -60, 60)
        high = num(row.get('offset_high'), '门机对时秒差上界', -60, 60)
        invariant(low < high, '门机对时秒差上界必须大于下界')
        lower_gate, _, upper_gate, _ = cycle
        invariant(lower_gate['event'] == 'lower-open' and upper_gate['event'] == 'upper-open',
                  '整闸对时需要同一闸次的下门开与上门开')
        invariant(min(lower_gate['time'], upper_gate['time']) + low >= 0,
                  '对时后的门位记录时刻不能为负')
        parsed.append(dict(id=key, cycle=cycle_id, lower_gate=lower_gate,
                           upper_gate=upper_gate, low=low, high=high))
    return parsed


def station_components(model, station):
    """测站可证时刻域：相邻读数缺口不超过最大插值间隔才可连成段，断档两端只保留实测点。"""
    points = model['samples'][station]
    components = []
    if len(points) == 1:
        components.append((points[0]['time'], points[0]['time']))
    for a, b in zip(points, points[1:]):
        if b['time'] - a['time'] <= model['max_gap']:
            components.append((a['time'], b['time']))
        else:
            components.extend(((a['time'], a['time']), (b['time'], b['time'])))
    return merge_intervals(components)


def signed_head(model, station, gate, delta):
    """返回对时后时刻的闸室减外站有符号高差及引用样点；缺证时高差为 None。"""
    tau = gate['time'] + delta
    chamber, chamber_ids = read_level(model, 'chamber', tau)
    outside, outside_ids = read_level(model, station, tau)
    evidence = list(dict.fromkeys(chamber_ids + outside_ids))
    if chamber is None or outside is None:
        return None, evidence
    return chamber - outside, evidence


def make_piece(start, end, state, samples, gate_id):
    return dict(start=start, end=end, state=state,
                evidence=dict(samples=sorted(set(samples)), gates=[gate_id]))


def blind_piece(model, gate, station, start, end):
    samples = []
    probes = (start, (start + end) / 2, end) if end - start > EPS else (start,)
    for delta in probes:
        _, ids = signed_head(model, station, gate, delta)
        samples.extend(ids)
    return make_piece(start, end, 'blind', samples, gate['id'])


def within_band(x0, x1, d0, d1, limit):
    """线性高差 d(δ) 落在 [-限值, 限值] 内的闭区间（可为点或空）。"""
    if abs(d1 - d0) <= EPS:
        return (x0, x1) if -limit - EPS <= d0 <= limit + EPS else None
    r_lo = x0 + (-limit - d0) * (x1 - x0) / (d1 - d0)
    r_hi = x0 + (limit - d0) * (x1 - x0) / (d1 - d0)
    if d1 > d0:
        wa, wb = max(x0, r_lo), min(x1, r_hi)
    else:
        wa, wb = max(x0, r_hi), min(x1, r_lo)
    if wa > wb + EPS:
        return None
    return wa, wb


def provable_pieces(model, gate, station, start, end, limit):
    """在可证秒差区间内按两站实测点切分，逐线性元胞解绝对高差限值，交点与实测点均可成端点。"""
    if end - start <= EPS:
        head, samples = signed_head(model, station, gate, start)
        return [make_piece(start, end, 'over' if abs(head) > limit + EPS else 'verified',
                           samples, gate['id'])]
    gate_time = gate['time']
    breaks = {start, end}
    for name in ('chamber', station):
        for sample in model['samples'][name]:
            delta = sample['time'] - gate_time
            if start + EPS < delta < end - EPS:
                breaks.add(delta)
    breaks = sorted(breaks)
    pieces = []
    for x0, x1 in zip(breaks, breaks[1:]):
        d0, e0 = signed_head(model, station, gate, x0)
        d1, e1 = signed_head(model, station, gate, x1)
        samples = list(dict.fromkeys(e0 + e1))
        band = within_band(x0, x1, d0, d1, limit)
        if band is None:
            pieces.append(make_piece(x0, x1, 'over', samples, gate['id']))
            continue
        wa, wb = band
        if wa > x0 + EPS:
            pieces.append(make_piece(x0, wa, 'over', samples, gate['id']))
        pieces.append(make_piece(wa, wb, 'verified', samples, gate['id']))
        if wb < x1 - EPS:
            pieces.append(make_piece(wb, x1, 'over', samples, gate['id']))
    return normalize(pieces)


def gate_partition(model, gate, low, high):
    """单门在允许秒差域上的完整划分：可证符合、可证不符、缺证，互不遗漏。"""
    station = gate['event'].split('-')[0]
    shifted = []
    for name in ('chamber', station):
        shifted.append([(a - gate['time'], b - gate['time']) for a, b in station_components(model, name)])
    provable = clip_intervals(intersect_intervals(shifted[0], shifted[1]), low, high)
    pieces = []
    cursor = low
    for a, b in provable:
        if a > cursor + EPS:
            pieces.append(blind_piece(model, gate, station, cursor, a))
        pieces.extend(provable_pieces(model, gate, station, a, b, model['limit']))
        cursor = b
    if cursor < high - EPS:
        pieces.append(blind_piece(model, gate, station, cursor, high))
    if not pieces:
        pieces.append(blind_piece(model, gate, station, low, high))
    return normalize(pieces)


def state_at(pieces, x):
    """闭区间取点；缺证段只认内部，其实测边界点仍由相邻可证段单独作证。
    同一点同时落在正长度段与零长度实测点段时，优先采信可单独作证的点段。"""
    states, samples = set(), []
    point_states = set()
    for piece in pieces:
        if piece['state'] == 'blind':
            inside = piece['start'] + EPS < x < piece['end'] - EPS
        else:
            inside = piece['start'] - EPS <= x <= piece['end'] + EPS
        if inside:
            states.add(piece['state'])
            samples.extend(piece['evidence']['samples'])
            if piece['end'] - piece['start'] <= EPS:
                point_states.add(piece['state'])
    if not states:
        for piece in pieces:
            if abs(x - piece['start']) <= EPS or abs(x - piece['end']) <= EPS:
                states.add(piece['state'])
                samples.extend(piece['evidence']['samples'])
    if point_states:
        if 'over' in point_states:
            return 'over', samples
        if 'verified' in point_states:
            return 'verified', samples
    if 'over' in states:
        return 'over', samples
    if 'verified' in states:
        return 'verified', samples
    return 'blind', samples


def combine_states(first, second):
    if first == 'over' or second == 'over':
        return 'over'
    if first == 'verified' and second == 'verified':
        return 'verified'
    return 'blind'


def common_partition(lower_pieces, upper_pieces, low, high):
    """以两门全部区间端点为切点，逐原子段联合判定，再合并同态相邻段。"""
    cuts = {low, high}
    for pieces in (lower_pieces, upper_pieces):
        for piece in pieces:
            for value in (piece['start'], piece['end']):
                if low - EPS <= value <= high + EPS:
                    cuts.add(value)
    cuts = sorted(cuts)
    all_gates = sorted({g for pieces in (lower_pieces, upper_pieces)
                        for p in pieces for g in p['evidence']['gates']})
    atoms = []
    for index, cut in enumerate(cuts):
        s1, e1 = state_at(lower_pieces, cut)
        s2, e2 = state_at(upper_pieces, cut)
        atoms.append(dict(start=cut, end=cut, state=combine_states(s1, s2), samples=e1 + e2))
        if index + 1 < len(cuts):
            middle = (cut + cuts[index + 1]) / 2
            s1, e1 = state_at(lower_pieces, middle)
            s2, e2 = state_at(upper_pieces, middle)
            atoms.append(dict(start=cut, end=cuts[index + 1], state=combine_states(s1, s2),
                              samples=e1 + e2))
    merged = normalize([dict(start=atom['start'], end=atom['end'], state=atom['state'],
                             evidence=dict(samples=atom['samples'], gates=all_gates)) for atom in atoms])
    for piece in merged:
        piece['evidence']['gates'] = all_gates
    return merged


def overall_state(common):
    has_verified = any(piece['state'] == 'verified' for piece in common)
    has_blind = any(piece['state'] == 'blind' for piece in common)
    if not has_verified and not has_blind:
        return 'infeasible'
    return 'feasible' if has_verified else 'indeterminate'


def gate_view(gate, pieces):
    return dict(gate=gate['id'], station=gate['event'].split('-')[0], pieces=pieces)


def alignment_review(review, model, facts):
    rows = []
    for item in read_alignments(review, facts):
        lower_pieces = gate_partition(model, item['lower_gate'], item['low'], item['high'])
        upper_pieces = gate_partition(model, item['upper_gate'], item['low'], item['high'])
        common = common_partition(lower_pieces, upper_pieces, item['low'], item['high'])
        samples = {sample for pieces in (lower_pieces, upper_pieces)
                   for piece in pieces for sample in piece['evidence']['samples']}
        rows.append(dict(id=item['id'], cycle=item['cycle'],
                         lower_gate=item['lower_gate']['id'], upper_gate=item['upper_gate']['id'],
                         offset_low=item['low'], offset_high=item['high'],
                         state=overall_state(common),
                         gates=[gate_view(item['lower_gate'], lower_pieces),
                                gate_view(item['upper_gate'], upper_pieces)],
                         common=common,
                         evidence=dict(gates=sorted([item['lower_gate']['id'], item['upper_gate']['id']]),
                                       samples=sorted(samples))))
    return rows
