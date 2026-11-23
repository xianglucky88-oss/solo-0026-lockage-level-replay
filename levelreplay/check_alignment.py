import argparse
import json
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from .check_flow import request
from .server import CONFIG, PREFIX, ROOT, serve


def near(actual, expected):
    return isclose(actual, expected, abs_tol=1e-8)


def pieces_tiling(row):
    for gate in row['gates']:
        pieces = gate['pieces']
        assert near(pieces[0]['start'], row['offset_low']) and near(pieces[-1]['end'], row['offset_high']), \
            f"{row['id']}/{gate['gate']} 区间未覆盖整个允许域"
        for a, b in zip(pieces, pieces[1:]):
            assert near(a['end'], b['start']), f"{row['id']}/{gate['gate']} 秒差区间存在断口"
            assert a['state'] != b['state'], f"{row['id']}/{gate['gate']} 同态相邻段未合并"
    assert near(row['common'][0]['start'], row['offset_low'])
    assert near(row['common'][-1]['end'], row['offset_high'])


def traceable(row):
    gate_ids = {row['lower_gate'], row['upper_gate']}
    for gate in row['gates']:
        assert gate['pieces'], '逐门区间不能为空'
        for piece in gate['pieces']:
            assert set(piece['evidence']['gates']) == {gate['gate']}, '门位证据必须可追到开门记录'
            assert piece['evidence']['samples'], '秒差区间端点必须有水尺样点作证'
    for piece in row['common']:
        assert set(piece['evidence']['gates']) == gate_ids, '共同区间必须同时追到两门'
        assert piece['evidence']['samples'], '共同区间端点必须有水尺样点作证'
    assert set(row['evidence']['gates']) == gate_ids and row['evidence']['samples']


def state_runs(row, stage, expected):
    actual = [(piece['state'], piece['start'], piece['end']) for piece in row[stage]]
    assert len(actual) == len(expected), f"{row['id']}/{stage} 区间数 {len(actual)}，预期 {len(expected)}"
    for (state, start, end), (want_state, want_start, want_end) in zip(actual, expected):
        assert state == want_state and near(start, want_start) and near(end, want_end), \
            f"{row['id']}/{stage} 出现 {state}[{start},{end}]，预期 {want_state}[{want_start},{want_end}]"


def load(name):
    return json.loads((ROOT / 'examples' / name).read_text(encoding='utf8'))


def audit(port):
    cases = [
        ('review-align-disjoint.json', 'infeasible'),
        ('review-align-point.json', 'feasible'),
        ('review-align-gap.json', 'indeterminate'),
    ]
    rows = {}
    for name, overall in cases:
        packet = request(port, 'evaluate', load(name))[0]
        row = packet['outputs']['alignment']
        assert len(row) == 1, '整闸对时应逐条输出'
        row = row[0]
        rows[name] = row
        assert row['state'] == overall, f"{name} 总判定 {row['state']}，应为 {overall}"
        pieces_tiling(row)
        traceable(row)

    # 情形一：两门各自都有符合窗口但无交集，整个允许域被不符排除
    disjoint = rows['review-align-disjoint.json']
    state_runs(disjoint, 'common', [('over', -5, 5)])
    lower = next(g for g in disjoint['gates'] if g['gate'] == '下门开')
    upper = next(g for g in disjoint['gates'] if g['gate'] == '上门开')
    state_runs(lower, 'pieces', [('over', -5, 2.0), ('verified', 2.0, 5)])
    state_runs(upper, 'pieces', [('verified', -5, -2.0), ('over', -2.0, 5)])
    boundary = upper['pieces'][0]['evidence']['samples']
    assert '上尺-198' in boundary, '区间端点必须追到造成它的实测水尺样点'

    # 情形二：共同可行范围只有一个边界点
    point = rows['review-align-point.json']
    state_runs(point, 'common', [('over', -5, 5.0), ('verified', 5.0, 5.0)])
    assert point['state'] == 'feasible'
    point_upper = next(g for g in point['gates'] if g['gate'] == '上门开')
    exact = [p for p in point_upper['pieces'] if p['state'] == 'verified']
    assert len(exact) == 1 and near(exact[0]['start'], 5) and near(exact[0]['end'], 5), \
        '准确落在实测点的可证符合必须单独成段'
    assert '上尺-205' in exact[0]['evidence']['samples']

    # 情形三：缺测使共同可行性无法排除
    gap = rows['review-align-gap.json']
    state_runs(gap, 'common',
               [('blind', -6, -5), ('over', -5, -5), ('blind', -5, 6), ('over', 6, 6)])
    assert not any(p['state'] == 'verified' for p in gap['common']), \
        '缺测区间不能凭空给出共同可行'
    upper_gap = next(g for g in gap['gates'] if g['gate'] == '上门开')
    state_runs(upper_gap, 'pieces',
               [('blind', -6, -5), ('over', -5, -5), ('blind', -5, 6), ('over', 6, 6)])
    # 断档两端的实测点单独有证，隔着断档的两端没有被连成一段
    assert upper_gap['pieces'][1]['evidence']['samples'] == ['上尺-195', '闸尺-190', '闸尺-200']
    assert upper_gap['pieces'][3]['evidence']['samples'] == ['上尺-206', '闸尺-200', '闸尺-210']

    # 复核样例：下门符合、上门整域确证不符，无可行对时
    sample = request(port, 'evaluate', load('review-sample.json'))[0]['outputs']['alignment'][0]
    assert sample['state'] == 'infeasible'
    assert all(piece['state'] == 'over' for piece in sample['common'])
    alternate = request(port, 'evaluate', load('review-alternate.json'))[0]['outputs']['alignment'][0]
    assert alternate['state'] == 'indeterminate' and \
        any(piece['state'] == 'blind' for piece in alternate['common'])

    # 不覆写原门时标与原开门高差口径
    for name, _ in cases:
        packet = request(port, 'evaluate', load(name))[0]
        assert packet['outputs']['head'], '原开门高差口径必须保留'
        assert packet['source']['gates'][0]['time'] == 100, '对时结果不得覆写原门位时标'

    # 非法输入被拒绝，且不落库
    bad = load('review-align-disjoint.json')
    bad['review']['alignments'][0]['cycle'] = '不存在的循环'
    request(port, 'evaluate', bad, status=400)
    bad = load('review-align-point.json')
    bad['review']['alignments'][0]['offset_low'], bad['review']['alignments'][0]['offset_high'] = 3, 1
    request(port, 'evaluate', bad, status=400)
    bad = load('review-align-gap.json')
    bad['review']['alignments'][0]['offset_low'] = -200
    request(port, 'evaluate', bad, status=400)
    request(port, 'seal', bad, status=400)

    # 不含 review 的基础包不受影响
    base = load('sample.json')
    assert 'alignment' not in request(port, 'evaluate', base)[0]['outputs']


def main():
    parser = argparse.ArgumentParser(description='运行整闸次对时复核的有界检查')
    parser.parse_args()
    with TemporaryDirectory(prefix=CONFIG['namespace'] + '-align-') as directory:
        with serve(Path(directory) / 'observations.sqlite', 0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                audit(server.server_port)
            finally:
                server.shutdown()
                thread.join()
    print('alignment：三种对时情形、证据追溯与非法拒绝均已核对')


if __name__ == '__main__':
    main()
