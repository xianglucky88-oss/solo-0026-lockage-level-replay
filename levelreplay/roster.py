"""过闸名册核对（离线通行取证）。

同一完整上行闸次（lower-open → lower-close → upper-open → upper-close）
可容纳多艘模型船。本模块只依据门序形成的半开窗口做归属取证：

* 入口记录只能与该闸次「下门开→下门关」的半开窗口相交；
* 出口记录只能与同一闸次「上门开→上门关」的半开窗口相交；
* 观测区间可同时交多个窗口，按整条区间判定，绝不把中点强行归到一个窗口。

在每条记录最多使用一次、每艘有号船每个闸次最多登记一次、容量受限、
已确认配对必须被满足的约束下，保留能解释最多入口出口对的全部最优解释；
只有在所有最优解释里都相同的配对及其闸次，才进入已核实名册。

不做全量最优解枚举（其数量可达指数级），而是用「排除/强制后的最优值是否
仍可达全局最优」精确回答「是否出现在每个最优解释里」，输入规模有界。
逐闸人数极值用字典序加权的记忆化搜索求得，无组合枚举。
"""

from .contract import ident, invariant, num, obj, records


ENTRY_LIMIT = 60
EXIT_LIMIT = 60
PAIR_LIMIT = 60


def _vessel(value, label):
    if value is None:
        return None
    return ident(value, label)


def _observation(row, side):
    start = num(row.get('start'), f'{side}观测起点', 0)
    end = num(row.get('end'), f'{side}观测终点', 0)
    invariant(start < end, f'{side}观测时间范围终点必须大于起点')
    return start, end


def parse_roster(review, cycles):
    """解析名册输入，并把每条记录映射到其观测区间可相交的完整闸次窗口。"""
    roster = obj(review, '名册数据')
    capacity = num(roster.get('capacity'), '每批容量', 1, 1000)
    invariant(float(capacity).is_integer(), '每批容量应为正整数')
    capacity = int(capacity)

    windows = []
    for group in cycles:
        lower_open, lower_close, upper_open, upper_close = group
        invariant(lower_open['time'] < lower_close['time']
                  < upper_open['time'] < upper_close['time'],
                  '完整闸次门序时间必须严格递增')
        windows.append(dict(id=lower_open['id'],
                            entry=(lower_open['time'], lower_close['time']),
                            exit_=(upper_open['time'], upper_close['time'])))
    cycle_ids = {w['id'] for w in windows}

    def build(rows, label, window_key, side):
        limit = ENTRY_LIMIT if side == '入口' else EXIT_LIMIT
        result = {}
        for row in records(rows, label, limit, 0):
            start, end = _observation(row, side)
            vessel = _vessel(row.get('vessel'), f'{side}船号')
            declared = row.get('cycle')
            if declared is not None:
                declared = ident(declared, f'{side}已确认闸次')
                invariant(declared in cycle_ids, f'{side}已确认闸次必须指向完整闸次：{declared}')
            feasible = [w['id'] for w in windows
                        if start < w[window_key][1] and w[window_key][0] < end]
            result[row['id']] = dict(id=row['id'], start=start, end=end, vessel=vessel,
                                     declared=declared, feasible=feasible)
        return result

    entries = build(roster.get('entries', []), '入口登记', 'entry', '入口')
    exits = build(roster.get('exits', []), '出口登记', 'exit_', '出口')

    pairs = []
    for row in records(roster.get('pairs', []), '已确认配对', PAIR_LIMIT, 0):
        pairs.append(dict(id=row['id'],
                          entry=ident(row.get('entry'), '已确认入口记录号'),
                          exit=ident(row.get('exit'), '已确认出口记录号')))
    return dict(capacity=capacity, windows=windows, entries=entries, exits=exits, pairs=pairs)


def _shared(entry, exit_):
    return sorted(set(entry['feasible']) & set(exit_['feasible']))


def classify_pairs(data):
    """把已确认配对拆成可强制满足的配对与必须显式指出的冲突配对。

    冲突不会被静默解除确认：冲突配对不参与求解，而是逐条上报原因。
    """
    entries, exits = data['entries'], data['exits']
    forced, conflicts = [], []
    used_entry, used_exit = set(), set()

    def conflict(pair, cycle, reason):
        conflicts.append(dict(id=pair['id'], entry=pair['entry'], exit=pair['exit'],
                              cycle=cycle, reason=reason, state='conflict'))

    for pair in data['pairs']:
        entry, exit_ = entries.get(pair['entry']), exits.get(pair['exit'])
        if entry is None or exit_ is None:
            conflict(pair, None, 'missing-record')
            continue
        if entry['vessel'] is not None and exit_['vessel'] is not None \
                and entry['vessel'] != exit_['vessel']:
            conflict(pair, None, 'vessel-mismatch')
            continue
        shared = _shared(entry, exit_)
        if not shared:
            conflict(pair, None, 'no-window')
            continue
        if not entry['start'] < exit_['end']:
            conflict(pair, None, 'bad-order')
            continue
        if entry['declared'] is not None and exit_['declared'] is not None \
                and entry['declared'] != exit_['declared']:
            conflict(pair, None, 'cycle-mismatch')
            continue
        declared = {c for c in (entry['declared'], exit_['declared']) if c is not None}
        if declared and not declared.issubset(set(shared)):
            conflict(pair, None, 'declared-window')
            continue
        if pair['entry'] in used_entry or pair['exit'] in used_exit:
            conflict(pair, None, 'record-reused')
            continue
        vessel = entry['vessel']
        if vessel is not None and len(shared) == 1:
            if any(f['vessel'] == vessel and f['cycle'] == shared[0] for f in forced):
                conflict(pair, shared[0], 'vessel-duplicate')
                continue
        used_entry.add(pair['entry'])
        used_exit.add(pair['exit'])
        forced.append(dict(id=pair['id'], entry=pair['entry'], exit=pair['exit'],
                           vessel=vessel, cycles=shared,
                           cycle=shared[0] if len(shared) == 1 else None,
                           declared=next(iter(declared), None)))
    return forced, conflicts


class Solver:
    """记忆化回溯，求给定强制/排除约束下的最优分配。

    每条成边的得分 = BASE + 该闸次权重。BASE 远大于权重总和的最大绝对值，
    因此最大化得分等价于：先最大化配对总数（全局最优），再按权重做字典序优化。
    权重取 0 时即求最大配对数；取 ±1 可在不牺牲配对总数的前提下，精确求某闸次
    在所有最优解释里成员数的最大值或最小值，无需枚举全部最优解。
    """

    BASE = 1000

    def __init__(self, data):
        self.capacity = data['capacity']
        self.entries = data['entries']
        self.exits = data['exits']
        # 与约束无关的静态结构在同一 data 上的多个 Solver 间缓存复用，
        # 避免每次可达性探测都重算候选边与入口排序。
        static = data.get('_solver_static')
        if static is None:
            cycles = [w['id'] for w in data['windows']]
            cycle_index = {c: i for i, c in enumerate(cycles)}
            exit_ids = sorted(self.exits)
            exit_bit = {x: 1 << idx for idx, x in enumerate(exit_ids)}
            entry_ids = sorted(self.entries)
            options = {}
            for e in entry_ids:
                opts = []
                for x in exit_ids:
                    shared = _shared(self.entries[e], self.exits[x])
                    if not shared:
                        continue
                    ev, xv = self.entries[e]['vessel'], self.exits[x]['vessel']
                    if ev is not None and xv is not None and ev != xv:
                        continue
                    if not self.entries[e]['start'] < self.exits[x]['end']:
                        continue
                    opts.append((x, tuple(cycle_index[c] for c in shared)))
                options[e] = opts
            entry_ids.sort(key=lambda e: (len(options[e]), e))
            count_shift = max(1, (data['capacity'] + 1).bit_length())
            static = dict(cycles=cycles, cycle_index=cycle_index, exit_ids=exit_ids,
                          exit_bit=exit_bit, entry_ids=entry_ids, options=options,
                          count_shift=count_shift, count_mask=(1 << count_shift) - 1)
            data['_solver_static'] = static
        self.cycles = static['cycles']
        self.cycle_index = static['cycle_index']
        self.exit_ids = static['exit_ids']
        self.exit_bit = static['exit_bit']
        self.entry_ids = static['entry_ids']
        self.options = static['options']
        self.count_shift = static['count_shift']
        self.count_mask = static['count_mask']
        self.blocked_exit_mask = 0
        self.zero_counts = 0
        self.reset()

    def reset(self, force_pairs=(), block_pairs=(), block_entries=(), block_exits=(),
              weights=None):
        # 必须使用的 (entry, exit) 对（闸次可在其可行窗口内任选）。
        self.must_pair = {}
        # 必须把指定入口固定到 (exit, cycle_index)：闸次被唯一钉住时使用。
        self.force = {}
        seen_exit = set()
        self.inconsistent = False
        for f in force_pairs:
            # 同一条记录不能被两个强制配对同时占用；出现即该约束集不可行。
            if f['entry'] in self.must_pair or f['exit'] in seen_exit:
                self.inconsistent = True
            self.must_pair[f['entry']] = f['exit']
            seen_exit.add(f['exit'])
            if f['cycle'] is not None:
                self.force[f['entry']] = (f['exit'], self.cycle_index[f['cycle']])
        self.block_pairs = set(block_pairs)
        self.block_entries = set(block_entries)
        self.block_exits = set(block_exits)
        self.blocked_exit_mask = 0
        for x in block_exits:
            self.blocked_exit_mask |= self.exit_bit.get(x, 0)
        self.weights = tuple(weights) if weights is not None else tuple(0 for _ in self.cycles)
        self.memo = {}

    def solve(self):
        """返回最大加权得分；约束不可行时返回 None。"""
        if self.inconsistent:
            return None
        # 有号船号 -> 所在闸次下标的不可变映射；同一艘船在同一闸次只能登记一次。
        return self._search(0, 0, self.zero_counts, frozenset())

    def maximum_pairs(self):
        score = self.solve()
        return None if score is None else score // self.BASE

    def cycle_extremes_all(self):
        """一次记忆化搜索，求每个闸次在「保持全局最优配对数」前提下的成员数区间。

        返回 (下界列表, 上界列表)。对每个状态额外记录：在取得该状态最优配对数的
        所有方案里，各闸次从该状态起新增成员数的最小、最大值，自底向上按最优分支合并。
        """
        if self.solve() is None:
            n = len(self.cycles)
            return [0] * n, [0] * n
        range_memo = {}
        zero = tuple(0 for _ in self.cycles)

        def ranges(i, used_mask, counts, vessel_cycle):
            if i == len(self.entry_ids):
                return zero, zero
            key = (i, used_mask, counts, vessel_cycle)
            if key in range_memo:
                return range_memo[key]
            e = self.entry_ids[i]
            best_score = self._search(i, used_mask, counts, vessel_cycle)
            # 收集所有能取得 best_score 的分支，合并其各闸计数区间。
            lows = [None] * len(self.cycles)
            highs = [None] * len(self.cycles)

            def absorb(inc_lo, inc_hi):
                for idx in range(len(self.cycles)):
                    lows[idx] = inc_lo[idx] if lows[idx] is None else min(lows[idx], inc_lo[idx])
                    highs[idx] = inc_hi[idx] if highs[idx] is None else max(highs[idx], inc_hi[idx])

            # 不成对分支。
            if e not in self.must_pair:
                skip_score = self._search(i + 1, used_mask, counts, vessel_cycle)
                if skip_score is not None and skip_score == best_score:
                    lo, hi = ranges(i + 1, used_mask, counts, vessel_cycle)
                    absorb(lo, hi)
            if e not in self.block_entries:
                for x, cycles_ok in self.options[e]:
                    bit = self.exit_bit[x]
                    if used_mask & bit or bit & self.blocked_exit_mask or (e, x) in self.block_pairs:
                        continue
                    if e in self.must_pair and self.must_pair[e] != x:
                        continue
                    ev, xv = self.entries[e]['vessel'], self.exits[x]['vessel']
                    for c in cycles_ok:
                        if self._count_at(counts, c) >= self.capacity:
                            continue
                        if (ev is not None and (ev, c) in vessel_cycle) \
                                or (xv is not None and (xv, c) in vessel_cycle):
                            continue
                        if e in self.force and self.force[e] != (x, c):
                            continue
                        added = frozenset((b, c) for b in (ev, xv) if b is not None)
                        new_counts = counts + (1 << (c * self.count_shift))
                        value = self._search(i + 1, used_mask | bit, new_counts,
                                             vessel_cycle | added)
                        if value is None:
                            continue
                        if self.BASE + self.weights[c] + value != best_score:
                            continue
                        sub_lo, sub_hi = ranges(i + 1, used_mask | bit, new_counts,
                                                vessel_cycle | added)
                        inc_lo = tuple(v + (1 if idx == c else 0) for idx, v in enumerate(sub_lo))
                        inc_hi = tuple(v + (1 if idx == c else 0) for idx, v in enumerate(sub_hi))
                        absorb(inc_lo, inc_hi)
            result = (tuple(lows), tuple(highs))
            range_memo[key] = result
            return result

        lo, hi = ranges(0, 0, self.zero_counts, frozenset())
        return list(lo), list(hi)

    def _count_at(self, packed, c):
        return (packed >> (c * self.count_shift)) & self.count_mask

    def essential_edges(self):
        """单趟求最优分配中的本质边（出现在所有最优解里的 (entry, exit) 对）。

        判据：沿某个最优分配自顶向下，对每个状态枚举所有能取得该状态最优分的
        合法选择。若当前匹配边之外还存在任一并列最优选择（跳过本入口，或换一条
        出口/闸次且下游可达同样总分），则该边可被替代，记为非本质；否则为本质。
        所有判定复用同一份 _search 记忆化表，不额外发起整轮搜索。
        """
        if self.solve() is None:
            return set(), []
        assignment = []
        essential = set()
        used_mask, counts, vessel_cycle = 0, self.zero_counts, frozenset()
        for i, e in enumerate(self.entry_ids):
            target = self._search(i, used_mask, counts, vessel_cycle)
            chosen, alternatives = None, 0
            # 跳过本入口若仍达最优，是一条并列替代。
            if e not in self.must_pair:
                skip = self._search(i + 1, used_mask, counts, vessel_cycle)
                if skip is not None and skip == target:
                    alternatives += 1
            if e not in self.block_entries:
                for x, cycles_ok in self.options[e]:
                    bit = self.exit_bit[x]
                    if used_mask & bit or bit & self.blocked_exit_mask or (e, x) in self.block_pairs:
                        continue
                    if e in self.must_pair and self.must_pair[e] != x:
                        continue
                    ev, xv = self.entries[e]['vessel'], self.exits[x]['vessel']
                    for c in cycles_ok:
                        if self._count_at(counts, c) >= self.capacity:
                            continue
                        if (ev is not None and (ev, c) in vessel_cycle) \
                                or (xv is not None and (xv, c) in vessel_cycle):
                            continue
                        if e in self.force and self.force[e] != (x, c):
                            continue
                        added = frozenset((b, c) for b in (ev, xv) if b is not None)
                        new_counts = counts + (1 << (c * self.count_shift))
                        value = self._search(i + 1, used_mask | bit, new_counts,
                                             vessel_cycle | added)
                        if value is None:
                            continue
                        if self.BASE + self.weights[c] + value != target:
                            continue
                        if chosen is None:
                            # 第一条等优边作为本状态的选择，不计入替代。
                            chosen = (x, c, bit, new_counts, vessel_cycle | added)
                        else:
                            # 其余等优边才是可替换当前选择的替代方案。
                            alternatives += 1
            if chosen is None:
                continue
            x, c, bit, new_counts, new_vc = chosen
            assignment.append((e, x, c))
            # 没有任何并列最优替代时，该 (entry, exit) 对在所有最优解中都成立。
            if alternatives == 0:
                essential.add((e, x))
            used_mask |= bit
            counts, vessel_cycle = new_counts, new_vc
        return essential, assignment

    def _search(self, i, used_mask, counts, vessel_cycle):
        if i == len(self.entry_ids):
            return 0
        key = (i, used_mask, counts, vessel_cycle)
        if key in self.memo:
            return self.memo[key]
        e = self.entry_ids[i]
        # 被强制使用的入口不能跳过；其余入口可以不成对。
        best = None if e in self.must_pair else \
            self._search(i + 1, used_mask, counts, vessel_cycle)
        if e not in self.block_entries:
            for x, cycles_ok in self.options[e]:
                bit = self.exit_bit[x]
                if used_mask & bit or bit & self.blocked_exit_mask or (e, x) in self.block_pairs:
                    continue
                if e in self.must_pair and self.must_pair[e] != x:
                    continue
                ev, xv = self.entries[e]['vessel'], self.exits[x]['vessel']
                for c in cycles_ok:
                    # 容量按该闸次登记的配对（即船数）计，与有无船号无关。
                    if self._count_at(counts, c) >= self.capacity:
                        continue
                    if (ev is not None and (ev, c) in vessel_cycle) \
                            or (xv is not None and (xv, c) in vessel_cycle):
                        continue
                    if e in self.force and self.force[e] != (x, c):
                        continue
                    added = frozenset((b, c) for b in (ev, xv) if b is not None)
                    new_counts = counts + (1 << (c * self.count_shift))
                    value = self._search(i + 1, used_mask | bit, new_counts,
                                         vessel_cycle | added)
                    if value is None:
                        continue
                    candidate = self.BASE + self.weights[c] + value
                    if best is None or candidate > best:
                        best = candidate
        self.memo[key] = best
        return best


def _make_solver(data, feasible_forced, weights=None, **constraints):
    solver = Solver(data)
    solver.reset(force_pairs=tuple(feasible_forced), weights=weights, **constraints)
    return solver


def analyze_roster(review, facts):
    data = parse_roster(review, facts['cycles'])

    # 没有完整门序：所有观察单独保留，绝不并入已核实通行。
    if not data['windows']:
        stray = [_stray(r, 'window') for r in list(data['entries'].values())
                 + list(data['exits'].values())]
        return dict(roster_verified=[], roster_contention=[], roster_conflict=[],
                    roster_stray=sorted(stray, key=lambda r: r['id']))

    forced, conflicts = classify_pairs(data)

    # 已确认配对若在容量/船号约束下无法与其余强制对共存，显式指出而非静默丢弃。
    # 迭代剔除：每轮找出一个「去掉它即可恢复可行」的配对，直到剩余强制集可行。
    feasible_forced = list(forced)
    while _make_solver(data, feasible_forced).solve() is None:
        culprit = None
        for f in feasible_forced:
            others = [g for g in feasible_forced if g['id'] != f['id']]
            if _make_solver(data, others).solve() is not None:
                culprit = f
                break
        if culprit is None:  # 兜底：没有单个可恢复者，剔除首个。
            culprit = feasible_forced[0]
        conflicts.append(dict(id=culprit['id'], entry=culprit['entry'], exit=culprit['exit'],
                              cycle=culprit['cycle'], reason='forced-infeasible',
                              state='conflict'))
        feasible_forced = [g for g in feasible_forced if g['id'] != culprit['id']]

    solver = _make_solver(data, feasible_forced)
    optimum_score = solver.solve()
    invariant(optimum_score is not None, '可满足的强制配对不应导致无解')
    optimum = optimum_score // Solver.BASE

    forced_key_set = {(f['entry'], f['exit']): f for f in feasible_forced}

    def can_reach(force=(), block_pairs=(), block_entries=(), block_exits=(),
                  release=()):
        released = set(release)
        base = [f for key, f in forced_key_set.items() if key not in released]
        probe = Solver(data)
        probe.reset(force_pairs=tuple(base) + tuple(force),
                    block_pairs=block_pairs, block_entries=block_entries,
                    block_exits=block_exits)
        return probe.maximum_pairs() == optimum

    def force_holder(e, x, c):
        return dict(id=f'probe/{e}/{x}', entry=e, exit=x,
                    vessel=data['entries'][e]['vessel'], cycles=[c],
                    cycle=solver.cycles[c], declared=None)

    # 单趟求出最优分配及其中的本质 (entry, exit) 对，避免逐边整轮重算。
    essential, assignment = solver.essential_edges()
    verified, contended_pairs, invariant_pairs = [], [], []
    for e, x, c0 in assignment:
        if (e, x) not in essential:
            continue
        # 强制对需要在「放开其强制约束」后再确认一次，避免把约束本身当成不变事实。
        release = [(e, x)] if (e, x) in forced_key_set else ()
        if release and can_reach(block_pairs=[(e, x)], release=release):
            continue
        invariant_pairs.append((e, x))
        shared = _shared(data['entries'][e], data['exits'][x])
        possible = [c for c in shared
                    if can_reach(force=[force_holder(e, x, solver.cycle_index[c])],
                                 release=release)]
        vessel = data['entries'][e]['vessel'] or data['exits'][x]['vessel']
        if len(possible) == 1:
            verified.append(dict(id=f'{possible[0]}/{e}/{x}', cycle=possible[0],
                                 entry=e, exit=x, vessel=vessel, state='verified'))
        else:
            contended_pairs.append((e, x, possible))

    contention = _contention(data, solver, invariant_pairs)
    stray = _strays(data, solver, invariant_pairs, contended_pairs)

    return dict(roster_verified=sorted(verified, key=lambda r: r['id']),
                roster_contention=sorted(contention, key=lambda r: r['id']),
                roster_conflict=sorted(conflicts, key=lambda r: r['id']),
                roster_stray=sorted(stray, key=lambda r: r['id']))


def _contention(data, solver, invariant_pairs):
    lo_all, hi_all = solver.cycle_extremes_all()
    rows = []
    for w in data['windows']:
        cycle, c = w['id'], solver.cycle_index[w['id']]
        lo, hi = lo_all[c], hi_all[c]
        here = [(e, x) for e in solver.entry_ids for x, cycles_ok in solver.options[e]
                if c in cycles_ok]
        member_entries = sorted({e for e, _ in here})
        member_exits = sorted({x for _, x in here})
        fixed = sorted({e for e, x in invariant_pairs
                        if cycle in _shared(data['entries'][e], data['exits'][x])})
        contenders = [e for e in member_entries if e not in fixed]
        full = hi >= data['capacity']
        # 人数确定且无成员争用时，该闸次没有争用，不出争用账。
        if lo == hi and not contenders:
            continue
        state = 'full-contention' if full and lo == hi else 'contention'
        rows.append(dict(id=cycle, cycle=cycle, low=lo, high=hi, capacity=data['capacity'],
                         contenders={'entries': contenders, 'exits': member_exits},
                         feasible_cycles=[cycle], state=state))
    return rows


def _stray(row, reason):
    return dict(id=row['id'], record=row['id'], vessel=row['vessel'],
                start=row['start'], end=row['end'], reason=reason, state='stray')


def _strays(data, solver, invariant_pairs, contended_pairs):
    invariant_entry = {e for e, _ in invariant_pairs}
    invariant_exit = {x for _, x in invariant_pairs}
    contended_entry = {e for e, _, _ in contended_pairs}
    contended_exit = {x for _, x, _ in contended_pairs}
    rows = []
    for side, pool in (('entry', data['entries']), ('exit', data['exits'])):
        invariant_used = invariant_entry if side == 'entry' else invariant_exit
        contended_used = contended_entry if side == 'entry' else contended_exit
        for row in pool.values():
            if not row['feasible']:
                rows.append(_stray(row, 'window'))
            elif row['id'] in invariant_used:
                continue
            else:
                reason = 'contended' if row['id'] in contended_used else 'unmatched'
                rows.append(_stray(row, reason))
    return rows
