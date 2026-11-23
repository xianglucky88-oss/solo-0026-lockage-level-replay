import argparse
import json
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .server import CONFIG, PREFIX, ROOT, serve


def request(port, route, body=None, status=200):
    url = f'http://127.0.0.1:{port}{PREFIX}{route}'
    opts = {} if body is None else dict(data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    response = None
    try:
        response = urlopen(Request(url, **opts), timeout=5)
    except HTTPError as failure:
        response = failure
    with response:
        if response.status != status:
            raise ValueError(f'{route}：状态码 {response.status}，预期 {status}；{response.read(300)!r}')
        return json.load(response), response.headers


def observe(rows, row_id, field, target):
    result = next((row for row in rows if row['id'] == row_id), None)
    if field is None:
        if result is not None:
            raise ValueError(f'{row_id} 不应出现在结果中')
        return
    actual = result.get(field) if result is not None else None
    ok = isclose(actual, target, abs_tol=1e-8) if type(target) is float and type(actual) in (int,float) else actual == target
    if not ok or result is None or field not in result:
        raise ValueError(f'{row_id}.{field} 为 {actual!r}，应为 {target!r}')


def audit_contacts(port):
    sample = request(port, 'contacts-sample')[0]
    anomaly = request(port, 'contacts-anomaly')[0]
    reject = json.loads((ROOT/'examples'/'contacts-reject.json').read_text(encoding='utf8'))
    request(port, 'evaluate', reject, status=400)
    packet = request(port, 'evaluate', sample)[0]
    outputs = packet['outputs']

    def rows(stage):
        return {row['id']: row for row in outputs[stage]}

    trace = outputs['gate_trace']
    blip = next(row for row in trace if row['id'] == '上-21.blip')
    if blip['kind'] != 'blip' or blip['state'] != 'open':
        raise ValueError('短暂假到位应只进原门位明细')
    if any(row['id'] == '上-21' for row in outputs['gate']):
        raise ValueError('短暂单点不能生成确认动作')
    if not any(row['kind'] == 'gap' for row in trace):
        raise ValueError('断证缺口应列在原门位明细')
    lockage = rows('lockage')
    if lockage['上-10']['state'] != 'downward' or lockage['上-10']['kind'] != 'cycle':
        raise ValueError('首个闸次应为下行完整闸次')
    if lockage['turnback/下-36']['kind'] != 'turnback':
        raise ValueError('同侧再开应另记折返')
    if lockage['下-36']['state'] != 'upward' or lockage['下-36']['kind'] != 'cycle':
        raise ValueError('折返之后应完成反向上行闸次')
    if len(outputs['volume']) != 2 or set(rows('volume')) != {'上-10', '下-36'}:
        raise ValueError('折返不进入完整闸次对账')
    volume = rows('volume')
    if not volume['上-10']['volume'] < 0 or volume['上-10']['direction'] != 'downward':
        raise ValueError('下行应保留负净容积，不把出水计成引水')
    if not volume['下-36']['volume'] > 0 or volume['下-36']['direction'] != 'upward':
        raise ValueError('上行净容积应为正')
    balance = rows('balance')
    if balance['下-36']['state'] != 'balanced' or balance['上-10']['state'] == 'balanced':
        raise ValueError('循环核差应按方向分别成立')
    supplies = rows('supply')
    if supplies['下行误记引水']['state'] == 'attributed' or supplies['窗外记录']['state'] != 'outside':
        raise ValueError('下行不能共用上行补水结论，窗外记录不进闸次')

    odd = request(port, 'evaluate', anomaly)[0]['outputs']
    states = {(row['state'], row['kind']) for row in odd['lockage']}
    if ('handoff', 'anomaly') not in states or ('broken', 'pending') not in states:
        raise ValueError('同刻交接应列异常、全闭段断证应列未决')
    if any(row['kind'] == 'cycle' for row in odd['lockage']):
        raise ValueError('断证闸次不能被后续开门补齐')
    if odd['volume'] or odd['balance']:
        raise ValueError('无完整闸次时不应产生循环对账')
    if not any(row['kind'] == 'conflict' for row in odd['gate_trace']):
        raise ValueError('双接点冲突应列原门位明细')

    # 旧档逐字段口径不变，且封存与去重对双门日志同样有效。
    classic = request(port, 'evaluate', request(port, 'sample')[0])[0]
    if 'gate_trace' in classic['outputs'] or 'lockage' in classic['outputs']:
        raise ValueError('旧档不应出现双门到位新增输出')
    request(port, 'seal', reject, status=400)
    sealed = request(port, 'seal', sample)[0]
    if not sealed['created'] or request(port, 'seal', sample)[0]['created']:
        raise ValueError('双门日志成果封存去重失败')
    if request(port, f"packet/{sealed['digest']}")[0] != packet:
        raise ValueError('双门日志封存快照与归约成果不一致')


def audit(port, flow):
    if flow == 'contacts':
        return audit_contacts(port)
    review = any(op['id'] == flow and op.get('optional') for op in CONFIG['operations'])
    prefix = 'review-' if review else ''
    data = request(port, prefix+'sample')[0]
    alt = request(port, prefix+'alternate')[0]
    bad = json.loads((ROOT/'examples'/(prefix+'reject.json')).read_text(encoding='utf8'))
    original = request(port,'evaluate',data)[0]
    alternate = request(port,'evaluate',alt)[0]
    request(port,'evaluate',bad,status=400)
    if review:
        request(port, 'seal', bad, status=400)
        baselines = [request(port, 'evaluate', request(port, name)[0])[0] for name in ('sample', 'alternate')]
        for baseline, enhanced in zip(baselines, (original, alternate), strict=True):
            if flow in baseline['outputs'] or any(enhanced['outputs'][k] != v for k,v in baseline['outputs'].items()):
                raise ValueError('复核改变了基础口径')
    if flow in original['outputs']:
        cases = json.loads((ROOT/'examples'/(prefix+'observations.json')).read_text(encoding='utf8'))
        row, key, value, alt_row, alt_key, alt_value = cases[flow]
        observe(original['outputs'][flow],row,key,value)
        observe(alternate['outputs'][flow],alt_row,alt_key,alt_value)
        return
    request(port,'seal',bad,status=400)
    left = request(port,'seal',data)[0]
    again = request(port,'seal',data)[0]
    right = request(port,'seal',alt)[0]
    if not left['created'] or again['created'] or again['sequence'] != left['sequence'] or not right['created']:
        raise ValueError('版本去重或首写失败')
    if len(request(port,'revisions')[0]) != 2:
        raise ValueError('无效及重复记录不应该入库')
    if flow == 'seal':
        observed = request(port,f"packet/{left['digest']}")[0]
        if observed != original:
            raise ValueError('较新的归约改变了历史成果')
    elif flow == 'compare':
        versions = dict(left=left['digest'],right=right['digest'])
        changes = request(port,'compare',versions)[0]
        if not changes['rows'] or request(port,'compare',dict(left=left['digest'],right=left['digest']))[0]['rows']:
            raise ValueError('版本比对没有区分同版及异版')
        other = request(port,'seal',dict(alt,id='不同对象'))[0]
        request(port,'compare',dict(left=left['digest'],right=other['digest']),status=409)
        base = request(port, 'seal', request(port, 'review-sample')[0])[0]
        mixed = request(port, 'compare', dict(left=left['digest'], right=base['digest']))[0]
        stages = {op['label'] for op in CONFIG['operations'] if op.get('optional')}
        if not mixed['rows'] or any(row['stage'] not in stages or row['state'] != 'added' for row in mixed['rows']):
            raise ValueError('旧版比对只应报告新增的复核结果')
        if request(port, f"export/{base['digest']}")[0] != request(port, f"packet/{base['digest']}")[0]:
            raise ValueError('复核信息未完整保存')
    elif flow == 'export':
        payload, headers = request(port,f"export/{left['digest']}")
        if payload != original or 'attachment;' not in headers.get('Content-Disposition',''):
            raise ValueError('导出没有保留原版本或下载头')
        request(port,'export/' + '0'*64,status=404)
    else:
        raise ValueError(f'未知工作流：{flow}')


def main():
    parser = argparse.ArgumentParser(description='运行有界的生产工作流检查')
    parser.add_argument('flow', choices=[op['id'] for op in CONFIG['operations']]+['seal','compare','export','contacts'])
    args = parser.parse_args()
    with TemporaryDirectory(prefix=CONFIG['namespace']+'-') as directory:
        with serve(Path(directory)/'observations.sqlite', 0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                audit(server.server_port,args.flow)
            finally:
                server.shutdown()
                thread.join()
    print(f'{args.flow}：样例、对照与合法拒绝均已核对')


if __name__ == '__main__':
    main()
