from .volume_curve import volume
from .water_levels import read_level


def decide(model, facts):
    compiled={k:facts[k] for k in ('level','gate','edge')}
    for extra in ('gate_trace','lockage'):
        if extra in facts:
            compiled[extra]=facts[extra]
    edges={r['id']:r for r in facts['edge']}
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
        chambers.append(dict(id=group[0]['id'],start=first['time'],end=last['time'], volume=after-before if before is not None and after is not None else None,
                             state='known' if before is not None and after is not None else 'blind',evidence=first['evidence']+last['evidence']))
    if model.get('contact_mode'):
        direction_by_id={r['id']:r['state'] for r in facts.get('lockage',[]) if r['kind']=='cycle'}
        for chamber in chambers:
            chamber['direction']=direction_by_id.get(chamber['id'])
    supplies=[]
    for supply in model['supplies']:
        interval=next((r for r in chambers if r['start']<=supply['start']<supply['end']<=r['end']),None)
        a,a_ids=read_level(model,'chamber',supply['start'])
        b,b_ids=read_level(model,'chamber',supply['end'])
        x,x_ids=read_level(model,supply['station'],supply['start'])
        y,y_ids=read_level(model,supply['station'],supply['end'])
        av,bv=volume(model['sections'],a),volume(model['sections'],b)
        if interval is None:
            state='outside'
        elif None in (av,bv,x,y):
            state='blind'
        elif min(x-a,y-b)<0 or bv<av:
            state='opposed'
        else:
            state='attributed'
        supplies.append(dict(id=supply['id'],state=state,cycle=interval['id'] if interval else None,station=supply['station'],
                             volume=bv-av if state=='attributed' else None,evidence=a_ids+b_ids+x_ids+y_ids))
    revisit=[dict(id=f"gate/{r['id']}",time=r['time'],state='sequence',evidence=[r['id']]) for r in facts['rejected']]
    revisit.extend(dict(id=f"head/{r['id']}",time=r['time'],state=r['state'],evidence=r['evidence']) for r in heads if r['state']!='verified')
    return dict(compiled,head=heads,volume=chambers,supply=supplies,revisit=sorted(revisit,key=lambda row:(row['time'],row['id'])))
