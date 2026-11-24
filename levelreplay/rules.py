from .loadings import (account_cycle, account_interval, build_timeline, endpoint_account,
                       instant_entries, split_points, timeline_rows)
from .volume_curve import volume
from .water_levels import read_level


def decide(model, facts):
    compiled={k:facts[k] for k in ('level','gate','edge')}
    edges={r['id']:r for r in facts['edge']}
    timeline=build_timeline(model['loads'])
    heads=[]
    for g in model['gates']:
        if not g['event'].endswith('-open'):
            continue
        row=edges[g['id']]
        gap=abs(row['chamber']-row['outside']) if row['state']=='known' else None
        heads.append(dict(id=g['id'],time=g['time'],head=gap,evidence=row['evidence'],
                          state='blind' if gap is None else 'over' if gap>model['limit'] else 'verified'))
    chambers=[]
    for group in facts['cycles']:
        first=edges[group[1]['id']]
        last=edges[group[2]['id']]
        before,after=[volume(model['sections'],row['chamber']) for row in (first,last)]
        sample_evidence=first['evidence']+last['evidence']
        gross=(after-before) if before is not None and after is not None else None
        row=dict(id=group[0]['id'],start=first['time'],end=last['time'], volume=gross,
                 state='known' if gross is not None else 'blind',
                 evidence=list(dict.fromkeys(sample_evidence)))
        if timeline is not None:
            row['gross']=gross
            instants=instant_entries(timeline,first['time'],last['time'])
            gear=account_cycle(timeline,first['time'],last['time'])
            endpoints=endpoint_account(timeline,first['time'],last['time'])
            row['evidence']={'samples':list(dict.fromkeys(sample_evidence)),
                             'loads':list(dict.fromkeys(gear['evidence']+endpoints['evidence'])),
                             'instants':[f"{item['load']}@{item['time']}" for item in instants]}
            row['instants']=[dict(item, net=-item['displacement']) for item in instants]
            if gross is None:
                row['volume']=None
                row['state']='blind'
            elif not gear['blind']:
                row['volume']=gross-gear['displacement']
                row['state']='known'
            elif endpoints['supported']:
                row['volume']=gross-endpoints['displacement']
                row['state']='partial'
            else:
                row['volume']=None
                row['state']='blind'
        chambers.append(row)
    supplies=_supply_rows(model,timeline,chambers)
    revisit=[dict(id=f"gate/{r['id']}",time=r['time'],state='sequence',evidence=[r['id']]) for r in facts['rejected']]
    revisit.extend(dict(id=f"head/{r['id']}",time=r['time'],state=r['state'],evidence=r['evidence']) for r in heads if r['state']!='verified')
    result=dict(compiled,head=heads,volume=chambers,supply=supplies,revisit=sorted(revisit,key=lambda row:(row['time'],row['id'])))
    if timeline is not None:
        result['loads']=timeline_rows(model['loads'],timeline)
    return result


def _supply_rows(model,timeline,chambers):
    supplies=[]
    for supply in model['supplies']:
        interval=next((r for r in chambers if r['start']<=supply['start']<supply['end']<=r['end']),None)
        bounds=[supply['start'],supply['end']]
        if timeline is not None:
            bounds=[supply['start'],*split_points(timeline,supply['start'],supply['end']),supply['end']]
        segments=[_segment(model,timeline,supply,a,b) for a,b in zip(bounds,bounds[1:])]
        if len(segments)==1:
            only=segments[0]
            parent=dict(id=supply['id'],station=supply['station'],
                        cycle=interval['id'] if interval else None,
                        volume=only['volume'],
                        state='outside' if not interval else only['state'],
                        evidence=only['evidence'])
            if timeline is not None:
                parent['gross']=only['gross']
            supplies.append(parent)
            continue
        if not interval:
            state='outside'
        elif any(segment['state']=='blind' for segment in segments):
            state='blind'
        elif all(segment['state']=='attributed' for segment in segments):
            state='attributed'
        elif any(segment['state']=='attributed' for segment in segments):
            state='partial'
        elif any(segment['state']=='partial' for segment in segments):
            state='partial'
        else:
            state='opposed'
        parent=dict(id=supply['id'],station=supply['station'],
                    cycle=interval['id'] if interval else None,
                    gross=_sum(segments,'gross'),volume=_sum(segments,'volume'),state=state,
                    evidence={'samples':list(dict.fromkeys(s for segment in segments for s in segment['evidence'].get('samples',[]))),
                              'loads':list(dict.fromkeys(w for segment in segments for w in segment['evidence'].get('loads',[])))})
        if not interval:
            parent['gross']=None
            parent['volume']=None
        supplies.append(parent)
        for index,segment in enumerate(segments,1):
            supplies.append(dict(id=f"{supply['id']}/{index}",parent=supply['id'],station=supply['station'],
                                 cycle=interval['id'] if interval else None,start=segment['start'],end=segment['end'],
                                 gross=segment['gross'],volume=segment['volume'],
                                 state='outside' if not interval else segment['state'],
                                 evidence=segment['evidence']))
    return supplies


def _segment(model,timeline,supply,start,end):
    a,a_ids=read_level(model,'chamber',start)
    b,b_ids=read_level(model,'chamber',end)
    x,x_ids=read_level(model,supply['station'],start)
    y,y_ids=read_level(model,supply['station'],end)
    av,bv=volume(model['sections'],a),volume(model['sections'],b)
    samples=list(dict.fromkeys(a_ids+b_ids+x_ids+y_ids))
    gross=(bv-av) if av is not None and bv is not None else None
    if timeline is None:
        if None in (av,bv,x,y):
            state='blind'
        elif min(x-a,y-b)<0 or bv<av:
            state='opposed'
        else:
            state='attributed'
        return dict(start=start,end=end,gross=gross,volume=gross,state=state,evidence=samples)
    gear=account_interval(timeline,start,end)
    evidence={'samples':samples,'loads':gear['evidence']}
    net=None if gross is None or gear['blind'] else gross-gear['displacement']
    if None in (av,bv,x,y):
        state='blind'
    elif gear['blind']:
        state='partial'
    elif min(x-a,y-b)<0 or net<0:
        state='opposed'
    else:
        state='attributed'
    return dict(start=start,end=end,gross=gross,volume=net,state=state,evidence=evidence)


def _sum(segments,key):
    values=[segment[key] for segment in segments if segment[key] is not None]
    return sum(values) if len(values)==len(segments) else None
