# 船闸水位回放台：数据规格

## 使用者和目标

船闸运行资料员提交合成闸室几何、三处水尺零点、闸门事件、已知供水阶段、水位样点，得到带可信边界的过闸循环、各阶段水量账和水位证据链。服务在回环地址启动，仅供本机处理。无需登录，不控制真实设备或放行生产数据。

## 数据合同

时标为同一时钟上的秒，高程及高差为米，容积为立方米。只识别下门开、下门关、上门开、上门关的完整上行序列；水尺只在允许空档内插值。截面为分段常面积；补水归因只使用所属循环、同段两端读数及正向水头，不推算瞬时流量。 归因水量只汇总本循环可信的补水记录；循环边界或补水证据不足时，不将观测缺失计为零差额。

`examples/sample.json` 是字段及合法取值示例。`alternate.json` 是同一对象的另一有效输入，`reject.json` 是应被阻止的输入。对象在输入顶层以 `id` 标识；归约行使用原始身份或来源组件组合作为标识。缺测映射为 `null` 或待定状态，不当成数值零。接口接受不超过 512 KiB 的 UTF-8 JSON 对象；重复字段、非法数值或域内冲突返回 400。

成果保存数据合同版本、原始包、各阶段输出、输入摘要和整包摘要。SQLite 通过唯一摘要让同一个有效产物只存一次，错误输入不落库；版本比对只允许同一对象和同一合同版本。校验摘要用于本地内容完整性，不是签名。

## 生产工作流

| 工作流 | 公开入口 | 主要样例观测 |
|---|---|---|
| 水尺归一 | `POST /levelreplay/api/evaluate 的 outputs.level` | 下尺-10 的 高程 为 10.02 |
| 门位重建 | `POST /levelreplay/api/evaluate 的 outputs.gate` | 续次下门关 的 状态 为 "lower-close" |
| 水位边界 | `POST /levelreplay/api/evaluate 的 outputs.edge` | 上门开 的 状态 为 "known" |
| 开门高差 | `POST /levelreplay/api/evaluate 的 outputs.head` | 上门开 的 状态 为 "over" |
| 容积归约 | `POST /levelreplay/api/evaluate 的 outputs.volume` | 下门开 的 净水量 为 450 |
| 补水核账 | `POST /levelreplay/api/evaluate 的 outputs.supply` | 引水甲 的 净水量 为 450 |
| 可复盘窗口 | `POST /levelreplay/api/evaluate 的 outputs.revisit` | gate/续次下门关 的 状态 为 "sequence" |
| 循环核差 | `POST /levelreplay/api/evaluate 的 outputs.balance` | 下门开 的 状态 为 "balanced" |
| 循环差分 | `POST /levelreplay/api/compare` | 异版产生差异，同版无差异，跨对象拒绝 |
| 封存版本 | `POST /levelreplay/api/seal` | 同输入仅写入一次，旧版不随对照改变 |
| 资料导出 | `GET /levelreplay/api/export/<摘要>` | 下载头有效且内容完整保留历史快照 |

## 职责

- `levelreplay/contract.py`：JSON 与领域字段边界。
- `levelreplay/domain.py`：解析领域单位与归约事实。
- `levelreplay/rules.py`：消费归约证据和约束，产生业务判定。
- `levelreplay/artifacts.py`：成果规范编码、摘要与差分。
- `levelreplay/archive.py`：SQLite 历史版本与原子封存。
- `levelreplay/service.py`：串联归约、比对和历史读取。
- `levelreplay/server.py`：本机 HTTP 及静态界面入口。
- `web/ui.js`：归约阶段、图形、归档与交互。
- `levelreplay/reconcile.py`：联合原始归约事实与业务判定，推导循环核差。

浏览器显示来源和判定结果；发布决定由服务端归约。自动化测试套件留置。每条工作流的有界核对命令见 `.project-runtime.json`。

## 扩展复核

顶层可选 `review`；省略时基础包与旧版逐字段一致。启用时增加下列结果，与原数据共同封存，旧版与增强版可按阶段比对。`examples/review-sample.json` 给出有效输入结构。

- `steps` 闸室分段水量：在闸室样点、供水窗口与外站样点处切分，避开不可内插缺口。
- `hydraulic` 逐段水头判读：用同区段两端的外站水头和体积符号筛查补水方向。
- `partition` 分段水量核差：保留可归因的部分，证据未齐时不输出整循环差额。
- `envelope` 门位时钟复核：按双站曲线折点计算时钟不确定窗的绝对高差包络。
- `gaps` 测站采样缺口：只列超过内插限制的相邻读数缺口及其中相关门位。
- `gate_map` 门位归属：将单个事件追到完整循环；拒绝串接残缺序列。
- `usage` 测点使用谱系：分别列出瞬时门位和时窗复核引用，源点不重复计数。
- `alignment` 整闸次对时：复核包可在 `review.alignments` 给出指向完整循环的门机允许秒差上下界。同一闸次的下门开与上门开共用一个秒差，把该秒差加到两门记录时刻后，分别核验闸室与对应外站的绝对高差。可证秒差域取门机允许范围与两站水尺可证范围的全部交段，尊重各测站真实采样边界和最大插值间隔：准确落在实测点可单独有证，超过插值限制的断档两端不得连成一段。逐门保留可确认符合（`verified`）、可确认不符（`over`）、缺证（`blind`）的秒差范围；共同可行范围只收两门都确证符合的部分，任一门确证不符即排除，其余标为无法判断；整个允许域都被排除时出具无可行对时（`infeasible`），存在共同可行时为 `feasible`，仅余无法判断时为 `indeterminate`。共同范围不是估计出的真实时差，不得据此覆写原门时标或水量账。每个区间端点都在 `evidence` 中追到造成它的水尺样点与门位记录。
