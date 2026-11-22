GATES = ('lower-open','lower-close','upper-open','upper-close')


def cycles(gates):
    complete=[]
    rejected=[]
    pattern=[]
    for gate in gates:
        if gate['event'] == GATES[len(pattern)]:
            pattern.append(gate)
            if len(pattern)==len(GATES):
                complete.append(tuple(pattern))
                pattern=[]
        else:
            rejected.extend(pattern)
            pattern = [gate] if gate['event']==GATES[0] else []
            if not pattern:
                rejected.append(gate)
    rejected.extend(pattern)
    return complete,rejected
