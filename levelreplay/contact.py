"""合成双门到位日志的证据重建与闸次判读。

每扇门在各自观测时刻带有开到位、关到位两个接点：
- 恰一个有效 → 证实对应到位状态（open/close）；
- 两个都未有效 → 行程中（travel），不证实任何到位；
- 两个都有效 → 冲突（conflict）。

到位动作必须由记录指定时长（hold）的连续同状态样点证实；相邻样点
间隔超过登记上限（max_interval）即断证。确认状态的到位时刻按该连续
观测的首个样点记录，短暂冒出的单点不产生动作。
"""

from .contract import enum, ident, invariant, num, obj, records

STATIONS = ('lower', 'upper')


def parse_contact(source):
    logs = obj(source.get('gate_logs'), '双门到位日志')
    hold = num(logs.get('hold'), '到位证实时长', 1e-9)
    max_interval = num(logs.get('max_interval'), '样点间隔上限', 1e-9)
    rows = records(logs.get('contacts'), '双门到位样点', 1200, 2)
    contacts = []
    for row in rows:
        key = ident(row.get('id'), '双门到位样点标识')
        station = enum(row.get('station'), STATIONS, '门侧')
        opened = row.get('open_contact')
        closed = row.get('close_contact')
        invariant(type(opened) is bool and type(closed) is bool, '到位接点必须为布尔值')
        if opened and closed:
            state = 'conflict'
        elif opened:
            state = 'open'
        elif closed:
            state = 'close'
        else:
            state = 'travel'
        contacts.append(dict(id=key, station=station, time=num(row.get('time'), '接点观测时刻', 0),
                             state=state))
    by_station = {station: [] for station in STATIONS}
    for row in contacts:
        by_station[row['station']].append(row)
    for station, group in by_station.items():
        group.sort(key=lambda r: r['time'])
        invariant(all(a['time'] < b['time'] for a, b in zip(group, group[1:])),
                  f'{station}门接点时刻必须严格递增且不能重复')
    invariant(all(by_station[station] for station in STATIONS), '上下两门都必须提交接点样点')
    return dict(hold=hold, max_interval=max_interval, contacts=by_station)


def reconstruct_station(station, rows, hold, max_interval):
    """重建单门证据：确认动作、短暂假到位、断证缺口与冲突。"""
    trace = []
    events = []
    run = []
    last = None

    def flush():
        nonlocal run
        if not run:
            return
        kind = run[0]['state']
        if kind in ('open', 'close'):
            span = run[-1]['time'] - run[0]['time']
            evidence = [r['id'] for r in run]
            if span >= hold:
                trace.append(dict(id=run[0]['id'], station=station, kind='action', state=kind,
                                  start=run[0]['time'], end=run[-1]['time'], evidence=evidence))
                events.append(dict(id=run[0]['id'], station=station, state=kind,
                                   time=run[0]['time'], evidence=evidence))
            else:
                trace.append(dict(id=run[0]['id'] + '.blip', station=station, kind='blip', state=kind,
                                  start=run[0]['time'], end=run[-1]['time'], evidence=evidence))
        run = []

    for row in rows:
        if last is not None and row['time'] - last['time'] > max_interval:
            flush()
            trace.append(dict(id=last['id'] + '.' + row['id'], station=station, kind='gap',
                              state='gap', start=last['time'], end=row['time'],
                              evidence=[last['id'], row['id']]))
        kind = row['state']
        if kind == 'conflict':
            flush()
            trace.append(dict(id=row['id'] + '.conflict', station=station, kind='conflict',
                              state='conflict', start=row['time'], end=row['time'],
                              evidence=[row['id']]))
        else:
            if run and kind != run[0]['state']:
                flush()
            run.append(row)
        last = row
    flush()
    return trace, events


def reconstruct(log):
    trace = []
    events = []
    for station in STATIONS:
        station_trace, station_events = reconstruct_station(
            station, log['contacts'][station], log['hold'], log['max_interval'])
        trace.extend(station_trace)
        events.extend(station_events)
    # 两门各自保留独立证据；事件按时刻归并，同刻按门侧确定性排序，
    # 同一时刻的两门动作作为一组判读，不依赖输入顺序。
    order = {station: i for i, station in enumerate(STATIONS)}
    events.sort(key=lambda r: (r['time'], order[r['station']]))
    trace.sort(key=lambda r: (r['start'], order[r['station']], r['id']))
    # 同门连续重复确认（接触抖动）只保留首次，避免造出重复动作。
    confirmed = []
    last_state = {}
    for event in events:
        if last_state.get(event['station']) == event['state']:
            continue
        last_state[event['station']] = event['state']
        confirmed.append(event)
    return trace, confirmed


def build_legs(events):
    """各门独立把确认的开、关配成门腿；未闭合的门腿单独保留。

    记录起始处的全闭确认（此前没有任何开门）是初始门位，不是孤立关门。
    """
    legs = []
    open_leg = {}
    seen_open = set()
    for event in events:
        station = event['station']
        if event['state'] == 'open':
            seen_open.add(station)
            open_leg[station] = dict(station=station, open=event, close=None)
        elif station in open_leg:
            leg = open_leg.pop(station)
            leg['close'] = event
            legs.append(leg)
        elif station in seen_open:
            legs.append(dict(station=station, open=None, close=event))
    legs.extend(open_leg.values())
    legs.sort(key=lambda leg: (leg['open'] or leg['close'])['time'])
    return legs


def phase_broken(log, start, end):
    """全闭阶段断证：阶段内任一门出现冲突，或缺口完全落在全闭阶段内部。

    跨越阶段边界的缺口只遮住开门或关门动作本身，不污染全闭段证据；
    完全落在阶段内部的缺口使双门均关闭失去连续证实。
    """
    if end <= start:
        return True
    for station in STATIONS:
        rows = log['contacts'][station]
        if any(r['state'] == 'conflict' and start < r['time'] < end for r in rows):
            return True
        for a, b in zip(rows, rows[1:]):
            if b['time'] - a['time'] <= log['max_interval']:
                continue
            if start < a['time'] and b['time'] < end:
                return True
    return False


def interpret(events, log):
    """按门腿序列判读完整闸次、折返、异常与未决。"""
    legs = build_legs(events)
    lockages = []
    closed = [leg for leg in legs if leg['open'] is not None and leg['close'] is not None]
    for leg in legs:
        if leg['open'] is None:
            lockages.append(dict(id='orphan/' + leg['close']['id'], kind='anomaly',
                                 state='handoff', station=leg['station'],
                                 start=leg['close']['time'], end=leg['close']['time'],
                                 evidence={'events': [leg['close']['id']]}))
        elif leg['close'] is None:
            lockages.append(dict(id='pending/' + leg['open']['id'], kind='pending',
                                 state='unclosed', station=leg['station'],
                                 start=leg['open']['time'], end=leg['open']['time'],
                                 evidence={'events': [leg['open']['id']]}))
    for first, second in zip(closed, closed[1:]):
        start, end = first['close']['time'], second['open']['time']
        first_ev = (first['open']['id'], first['close']['id'])
        second_ev = (second['open']['id'], second['close']['id'])
        evidence = {'events': list(first_ev + second_ev)}
        pair_id = first['close']['id'] + '.' + second['open']['id']
        if second['open']['time'] < first['close']['time']:
            lockages.append(dict(id='anomaly/' + pair_id, kind='anomaly', state='dual-open',
                                 station=None, start=second['open']['time'], end=first['close']['time'],
                                 evidence=evidence))
        elif end <= start:
            # 同刻交接：没有正时长全闭阶段，不能造出全闭。
            lockages.append(dict(id='anomaly/' + pair_id, kind='anomaly', state='handoff',
                                 station=None, start=start, end=end, evidence=evidence))
        elif phase_broken(log, start, end):
            # 全闭段断证优先：证据缺失时既不能判完整闸次，也不能判折返。
            lockages.append(dict(id='pending/' + pair_id, kind='pending', state='broken',
                                 station=None, start=start, end=end, evidence=evidence))
        elif first['station'] == second['station']:
            lockages.append(dict(id='turnback/' + second['open']['id'], kind='turnback',
                                 state='turnback', station=first['station'], start=start, end=end,
                                 evidence=evidence))
        else:
            direction = 'upward' if first['station'] == 'lower' else 'downward'
            lockages.append(dict(id=first['open']['id'], kind='cycle', state=direction,
                                 station=first['station'], start=start, end=end,
                                 events=(first['open'], first['close'],
                                         second['open'], second['close']),
                                 evidence=evidence))
    order = {station: i for i, station in enumerate(STATIONS)}
    lockages.sort(key=lambda r: (r['start'], r.get('station') is None,
                                 order.get(r.get('station'), len(STATIONS)), r['id']))
    complete = [r.pop('events') for r in lockages if r['kind'] == 'cycle']
    return lockages, complete
