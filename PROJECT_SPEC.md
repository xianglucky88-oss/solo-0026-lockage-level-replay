# 船闸水位回放台：数据规格

## 使用者和目标

船闸运行资料员提交合成闸室几何、三处水尺零点、闸门事件、已知供水阶段、水位样点，得到带可信边界的过闸循环、各阶段水量账和水位证据链。服务在回环地址启动，仅供本机处理。无需登录，不控制真实设备或放行生产数据。

## 数据合同

时标为同一时钟上的秒，高程及高差为米，容积为立方米。只识别下门开、下门关、上门开、上门关的完整上行序列；水尺只在允许空档内插值。截面为分段常面积；补水归因只使用所属循环、同段两端读数及正向水头，不推算瞬时流量。 归因水量只汇总本循环可信的补水记录；循环边界或补水证据不足时，不将观测缺失计为零差额。

`examples/sample.json` 是字段及合法取值示例。`alternate.json` 是同一对象的另一有效输入，`reject.json` 是应被阻止的输入。对象在输入顶层以 `id` 标识；归约行使用原始身份或来源组件组合作为标识。缺测映射为 `null` 或待定状态，不当成数值零。接口接受不超过 512 KiB 的 UTF-8 JSON 对象；重复字段、非法数值或域内冲突返回 400。

### 载荷排开修正

顶层可选 `loads`，省略时所有基础输出与旧版逐字段一致；附件缺初况、初况缺排水量或确证档缺见证编号均返回 400，不能按空闸（排开体积零）处理。每条载荷给出本档起点初况（`confirmed` 已知排水量加见证编号，或 `unverified` 未核实状态）以及此后每次变更的前后两档排水量、状态与见证编号；未核实档不得附见证。一段时间内沿用最后一次确证档位，遇到明确未核实的变更即停止沿用，直到新档重新确证。不同载荷的变更时刻不得重合，须逐秒拆开。

净水量 = 几何容积 − 当前确证档位的载荷排开体积。闸边水尺读数与开门高差继续表示实测水面，不参与净水修正。循环水量行同时给出 `gross`（几何容积变化）与 `volume`（净水量变化）；连续供水阶段在载荷变更处拆开，各段从起点后态算到终点前态，与供水起止同秒的变更不拆段、其档位差作为瞬时净水账项单列在所属闸次中，既不混入连续供水量也不计两次。完整闸次按闭门起点的变更前档、开门终点的变更后档计算，瞬时账项只收起止之间的部分。循环、供水和核差明细的 `evidence` 同时列出所用的水尺样点与载荷见证编号。途中缺证而首尾均确证时，允许报告有判据的首尾净变化（状态 `partial`），但缺证段不靠总量补齐，也不得据此宣布对账闭合；`load-sample.json`、`load-alternate.json`、`load-reject.json` 给出有效、缺证与拒绝三类输入。

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
| 载荷净水量 | `POST /levelreplay/api/evaluate 的 outputs.loads` | 标定载荷甲/1/after 的 状态 为 "confirmed" |
| 循环差分 | `POST /levelreplay/api/compare` | 异版产生差异，同版无差异，跨对象拒绝 |
| 封存版本 | `POST /levelreplay/api/seal` | 同输入仅写入一次，旧版不随对照改变 |
| 资料导出 | `GET /levelreplay/api/export/<摘要>` | 下载头有效且内容完整保留历史快照 |

## 职责

- `levelreplay/contract.py`：JSON 与领域字段边界。
- `levelreplay/domain.py`：解析领域单位与归约事实。
- `levelreplay/rules.py`：消费归约证据和约束，产生业务判定。
- `levelreplay/loadings.py`：载荷档位时间线、排开体积口径与净水量归约。
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
