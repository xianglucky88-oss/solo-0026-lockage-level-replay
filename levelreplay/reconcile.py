from math import fsum, isclose


def summarize(model, facts, outputs):
    sessions = []
    for chamber in outputs['volume']:
        rows = [r for r in outputs['supply'] if r.get('cycle') == chamber['id'] and 'parent' not in r]
        known = [r for r in rows if r['state'] == 'attributed']
        accounted = fsum(r['volume'] for r in known)
        instants = chamber.get('instants', [])
        instant_sum = fsum(item['net'] for item in instants)
        if 'loads' in outputs:
            evidence = {'chamber': chamber['evidence'], 'supply': [r['id'] for r in rows],
                        'excluded': [r['id'] for r in rows if r['state'] != 'attributed'],
                        'instants': [f"{item['load']}@{item['time']}" for item in instants]}
        else:
            evidence = {'chamber': chamber['evidence'], 'supply': [r['id'] for r in rows],
                        'excluded': [r['id'] for r in rows if r['state'] != 'attributed']}
        balance = None if chamber['volume'] is None or len(known) != len(rows) \
            else chamber['volume'] - accounted - instant_sum
        state = ('blind' if chamber['volume'] is None else
                 'partial' if chamber['state'] == 'partial' or balance is None else
                 'unattributed' if not rows else 'balanced' if isclose(balance,0,abs_tol=1e-8) else 'residual')
        sessions.append(dict(id=chamber['id'], start=chamber['start'], end=chamber['end'],
                             volume=chamber['volume'], accounted=accounted, residual=balance,
                             state=state, evidence=evidence))
    return sessions
