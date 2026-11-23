from .clearance import analyze_clearance
from .quality import evidence_usage, gate_disposition, head_envelope, read_probes, station_gaps
from .segments import hydraulic_evidence, partition_balance, split_cycles


def analyze_review(review, model, facts, outputs):
    steps = split_cycles(model, outputs['volume'])
    hydraulic = hydraulic_evidence(model, steps)
    envelopes = head_envelope(model, read_probes(review, model))
    result = dict(steps=[{**r, 'source': r['source']['id'] if r['source'] else None} for r in steps],
                  hydraulic=hydraulic, partition=partition_balance(outputs['volume'], hydraulic),
                  envelope=envelopes, gaps=station_gaps(model, facts['edge']),
                  gate_map=gate_disposition(facts, outputs['head']), usage=evidence_usage(facts['edge'], envelopes))
    if 'clearance' in review:
        result.update(analyze_clearance(review, model))
    return result
