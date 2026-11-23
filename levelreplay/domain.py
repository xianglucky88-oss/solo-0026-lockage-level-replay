from .contract import enum, invariant, num, obj, records, unique_order
from . import contact
from .gate_sequence import GATES, cycles
from .water_levels import STATIONS, read_level


def parse_common(source):
    datum = obj(source.get('datum'), '标尺基准')
    zeros = {station:num(datum.get(station),'水尺零点',-100,100) for station in STATIONS}
    samples = {station:[] for station in STATIONS}
    for row in records(source.get('samples'),'水位样点',600,1):
        station = enum(row.get('station'),STATIONS,'测站')
        samples[station].append(dict(id=row['id'],time=num(row.get('time'),'样点时刻',0),
                                     level=num(row.get('level'),'相对水位',-100,100)+zeros[station],station=station))
    for values in samples.values():
        values.sort(key=lambda r:r['time'])
        unique_order(values,'time','同测站水尺时标')
    sections = [dict(id=r['id'],level=num(r.get('level'),'截面起算水位',-200,200),area=num(r.get('area'),'水面面积',0.001,1e7))
                for r in records(source.get('sections'),'截面',24,1)]
    unique_order(sections,'level','各段截面起算点')
    phases = [dict(id=r['id'],start=num(r.get('start'),'补水开始',0),end=num(r.get('end'),'补水结束',0),
                   station=enum(r.get('station'),STATIONS[1:],'补水来源')) for r in records(source.get('supplies'),'补水记录',120)]
    phases.sort(key=lambda r:r['start'])
    invariant(all(r['start']<r['end'] for r in phases) and all(a['end']<=b['start'] for a,b in zip(phases,phases[1:])),
              '补水区段必须为不重叠正长度区间')
    return dict(samples=samples,sections=sections,supplies=phases,
                max_gap=num(source.get('max_gap'),'可插值时长',0.001),limit=num(source.get('head_limit'),'开门高差限值',0,10))


def parse(source):
    invariant(('gates' in source) != ('gate_logs' in source),
              '门位事件与合成双门到位日志必须二选一，不能同时提交')
    model = parse_common(source)
    if 'gate_logs' in source:
        model['contact'] = contact.parse_contact(source)
        model['gates'] = []
        model['contact_mode'] = True
        return model
    gates = [dict(id=r['id'],time=num(r.get('time'),'门位时刻',0),event=enum(r.get('event'),GATES,'门位动作'))
             for r in records(source.get('gates'),'门位事件',180,1)]
    unique_order(gates,'time','门位时标')
    model.update(gates=gates, contact_mode=False)
    return model


def gate_edges(model, gates):
    edges=[]
    for gate in gates:
        station=gate['event'].split('-')[0]
        chamber,first=read_level(model,'chamber',gate['time'])
        outside,second=read_level(model,station,gate['time'])
        edges.append(dict(id=gate['id'],time=gate['time'],chamber=chamber,outside=outside,station=station,
                          evidence=first+second,state='known' if chamber is not None and outside is not None else 'blind'))
    return edges


def evaluate(model):
    if model['contact_mode']:
        log = model['contact']
        trace, events = contact.reconstruct(log)
        gates = [dict(id=e['id'], time=e['time'], event=f"{e['station']}-{e['state']}") for e in events]
        model['gates'] = gates
        lockages, complete = contact.interpret(events, log)
        transitions=[dict(id=e['id'],time=e['time'],state=f"{e['station']}-{e['state']}",station=e['station']) for e in events]
        edges=gate_edges(model, gates)
        return dict(level=[dict(row,state='observed') for values in model['samples'].values() for row in values],
                    gate=transitions,edge=edges,cycles=complete,rejected=[],
                    gate_trace=trace,lockage=lockages)
    complete,rejected=cycles(model['gates'])
    transitions=[dict(id=row['id'],time=row['time'],state=row['event'],station=row['event'].split('-')[0]) for row in model['gates']]
    edges=gate_edges(model, model['gates'])
    return dict(level=[dict(row,state='observed') for values in model['samples'].values() for row in values],
                gate=transitions,edge=edges,cycles=complete,rejected=rejected)
