"""标定载荷档位时间线与净水量归约。

几何容积由水尺决定，载荷浸入深度变化会在不增水的情况下抬升水面。
净水量 = 几何容积 - 当前确证档位的载荷排开体积。未核实档位会切断沿用，
重新确证前不得按空闸（排开体积 0）处理，也不得用总量补齐缺证区间。
"""

from .contract import ContractError, enum, ident, invariant, num, obj, records, unique_order

CONFIRMED = 'confirmed'
UNVERIFIED = 'unverified'
INITIAL = 'initial'


def parse_loads(source):
    """解析可选的载荷附件；无附件返回 None，附件缺初况按 400 拒绝。"""
    if 'loads' not in source:
        return None
    raw_loads = source.get('loads')
    if not isinstance(raw_loads, list) or not raw_loads:
        raise ContractError('载荷附件应至少含一条载荷记录，无附件时应省略该字段')
    loads = []
    for row in records(raw_loads, '载荷记录', 24):
        key = ident(row.get('id'), '载荷标识')
        initial = obj(row.get('initial'), f'{key}初况')
        status = enum(initial.get('state'), (CONFIRMED, UNVERIFIED), f'{key}初况状态')
        displacement = num(initial.get('displacement'), f'{key}初况排水量', 0, 1e7)
        witness = ident(initial.get('witness'), f'{key}初况见证') if status == CONFIRMED else None
        if status == UNVERIFIED:
            invariant(initial.get('witness') in (None, ''), '未核实初况不得附见证编号')
        changes = []
        raw_changes = row.get('changes', [])
        if not isinstance(raw_changes, list) or not 0 <= len(raw_changes) <= 120:
            raise ContractError(f'{key}档位变更应含 0 至 120 条记录')
        for change in raw_changes:
            time = num(obj(change, f'{key}档位变更').get('time'), '变更时刻', 0)
            before = obj(change.get('before'), f'{key}变更前档')
            after = obj(change.get('after'), f'{key}变更后档')
            before_state = enum(before.get('state'), (CONFIRMED, UNVERIFIED), f'{key}变更前状态')
            after_state = enum(after.get('state'), (CONFIRMED, UNVERIFIED), f'{key}变更后状态')
            before_value = num(before.get('displacement'), f'{key}变更前排水量', 0, 1e7)
            after_value = num(after.get('displacement'), f'{key}变更后排水量', 0, 1e7)
            before_witness = _witness(before_state, before.get('witness'), f'{key}变更前见证')
            after_witness = _witness(after_state, after.get('witness'), f'{key}变更后见证')
            changes.append(dict(time=time,
                                before=dict(state=before_state, displacement=before_value, witness=before_witness),
                                after=dict(state=after_state, displacement=after_value, witness=after_witness)))
        changes.sort(key=lambda item: item['time'])
        unique_order(changes, 'time', f'{key}变更时刻')
        loads.append(dict(id=key,
                          initial=dict(state=status, displacement=displacement, witness=witness),
                          changes=changes))
    all_times = [item['time'] for load in loads for item in load['changes']]
    invariant(len(all_times) == len(set(all_times)), '不同载荷的变更时刻不得重合，需逐秒拆开')
    return loads


def _witness(state, value, label):
    if state == CONFIRMED:
        return ident(value, label)
    invariant(value in (None, ''), '未核实档位不得附见证编号')
    return None


def build_timeline(loads):
    """把各载荷初况与变更展开成统一的档位事件时间线。"""
    if not loads:
        return None
    events = []
    for load in loads:
        events.append(dict(time=0, load=load['id'], kind=INITIAL,
                           state=load['initial']['state'], displacement=load['initial']['displacement'],
                           witness=load['initial']['witness']))
        for change in load['changes']:
            for side, kind in (('before', 'before'), ('after', 'after')):
                gear = change[side]
                events.append(dict(time=change['time'], load=load['id'], kind=kind,
                                   state=gear['state'], displacement=gear['displacement'],
                                   witness=gear['witness']))
    events.sort(key=lambda event: (event['time'], 0 if event['kind'] == 'before' else 1, event['load']))
    return events


def gear_at(timeline, time):
    """时刻 time 的有效档位：取不晚于该时刻的最后一次后态/初况。"""
    current = None
    for event in timeline:
        if event['time'] > time:
            break
        if event['kind'] in (INITIAL, 'after'):
            current = event
    return current


def gear_at_start(timeline, time):
    """time 作为闭区间起点（闭门起点）时的档位：取当秒变更前的档位。"""
    same_second = [event for event in timeline
                   if event['time'] == time and event['kind'] == 'before']
    if same_second:
        return same_second[0]
    return gear_at(timeline, time)


def gear_at_end(timeline, time):
    """time 作为闭区间终点时的档位：优先同秒变更的后态，否则取前态。"""
    same_second = [event for event in timeline
                   if event['time'] == time and event['kind'] == 'after']
    if same_second:
        return same_second[-1]
    return _gear_before(timeline, time)


def split_points(timeline, start, end):
    """区间 (start, end) 内的载荷变更时刻，用于拆开连续供水阶段。"""
    return sorted({event['time'] for event in timeline
                   if event['kind'] == 'after' and start < event['time'] < end})


def account_interval(timeline, start, end):
    """连续供水段 [start, end] 的载荷排开变化口径。

    起点取 start 的后态档位（同秒变更归瞬时账），终点取 end 的前态
    档位（同秒变更归瞬时账）。仅当未核实后态严格落在区间内部时该段
    不可签认。净水量 = 几何容积变化 - 排开体积变化。
    """
    first_gear = gear_at(timeline, start)
    last_gear = _gear_before(timeline, end)
    return _account(timeline, first_gear, last_gear, start, end)


def account_cycle(timeline, start, end):
    """完整闸次 [start, end] 的载荷排开变化口径（首尾边界档）。

    起点取闭门起点当秒变更前档位，终点取开门终点当秒变更后档位，
    与首尾净变化同口径；同秒的档位差另列瞬时账，不与此处重复。
    """
    first_gear = gear_at_start(timeline, start)
    last_gear = gear_at_end(timeline, end)
    return _account(timeline, first_gear, last_gear, start, end)


def _account(timeline, first_gear, last_gear, start, end):
    witnesses = _witnesses(first_gear, last_gear)
    blind_region = any(event['kind'] == 'after' and event['state'] == UNVERIFIED
                       and start < event['time'] < end
                       for event in timeline)
    if first_gear is None or last_gear is None or blind_region \
            or first_gear['state'] == UNVERIFIED or last_gear['state'] == UNVERIFIED:
        return dict(displacement=None, blind=True, evidence=witnesses)
    displacement = last_gear['displacement'] - first_gear['displacement']
    return dict(displacement=displacement, blind=False, evidence=witnesses)


def endpoint_account(timeline, start, end):
    """只看首尾档位的账：首尾均确证即可报告有判据的首尾净变化。

    起点取闭门起点当秒变更前档位，终点取开门终点当秒变更后档位。中间
    缺证不影响首尾差值本身，但该差值不能用于宣布对账闭合。
    """
    first_gear = gear_at_start(timeline, start)
    last_gear = gear_at_end(timeline, end)
    witnesses = _witnesses(first_gear, last_gear)
    if first_gear is None or last_gear is None \
            or first_gear['state'] == UNVERIFIED or last_gear['state'] == UNVERIFIED:
        return dict(displacement=None, supported=False, evidence=witnesses)
    return dict(displacement=last_gear['displacement'] - first_gear['displacement'],
                supported=True, evidence=witnesses)


def _witnesses(*gears):
    return list(dict.fromkeys(gear['witness'] for gear in gears if gear and gear['witness']))


def _gear_before(timeline, time):
    """time 的前态档位：严格早于 time 的最后后态，或 time 当秒变更的前态。"""
    current = None
    for event in timeline:
        if event['time'] > time:
            break
        if event['time'] == time and event['kind'] == 'before':
            return event
        if event['kind'] in (INITIAL, 'after'):
            current = event
    return current


def instant_entries(timeline, start, end):
    """收起止之间确证档位变更的瞬时净水账项；未核实变更不产账。

    与供水起止同秒的变更仍属本闸次的瞬时账，只是绝不进入连续供水量。
    """
    entries = []
    for event in timeline:
        if event['kind'] != 'after' or not (start <= event['time'] <= end):
            continue
        before = _paired_before(timeline, event)
        if event['state'] != CONFIRMED or before['state'] != CONFIRMED:
            continue
        entries.append(dict(time=event['time'], load=event['load'],
                            displacement=event['displacement'] - before['displacement'],
                            witness=event['witness']))
    return entries


def _paired_before(timeline, after_event):
    for event in timeline:
        if (event['time'] == after_event['time'] and event['load'] == after_event['load']
                and event['kind'] == 'before'):
            return event
    raise RuntimeError('载荷变更缺少配对前态')


def timeline_rows(loads, timeline):
    """输出每个载荷的档位时间线，初况与每次变更前后各一行。"""
    rows = []
    for load in loads:
        base = f"{load['id']}/初况"
        initial = load['initial']
        rows.append(dict(id=base, load=load['id'], time=0, kind=INITIAL,
                         displacement=initial['displacement'], state=initial['state'],
                         witness=initial['witness']))
        for index, change in enumerate(load['changes'], 1):
            for side, kind in (('before', 'before'), ('after', 'after')):
                gear = change[side]
                rows.append(dict(id=f"{load['id']}/{index}/{kind}", load=load['id'], time=change['time'],
                                 kind=kind, displacement=gear['displacement'], state=gear['state'],
                                 witness=gear['witness']))
    return sorted(rows, key=lambda row: (row['time'], row['load'], row['kind']))
