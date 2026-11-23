# 船闸水位回放台

船闸运行资料员使用浏览器处理合成闸室几何、三处水尺零点、闸门事件、已知供水阶段、水位样点，交付带可信边界的过闸循环、各阶段水量账和水位证据链。全部内置数据为合成样例，不连接生产设备或在线服务。

## 本机运行

Python 3.11 或以上（含标准库 SQLite），不需要下载包。现代浏览器负责界面；Node.js 仅用于构建时检查 JavaScript 语法。

```sh
python3 -m levelreplay.server
```

访问 `http://127.0.0.1:8863`。默认把封存成果写入 `.local/levelreplay.sqlite`；可传 `--port`、`--db` 覆盖。界面先显示合成首例；可切换对照、导入 JSON、查看证据明细、封存成果、比较同对象版本或下载指定版本。

## 手动核对

```sh
python3 -m levelreplay.check_flow level
python3 -m levelreplay.check_flow balance
python3 -m levelreplay.check_flow contacts
python3 -m levelreplay.check_flow export
```

这些命令在临时成果库和临时回环端口运行实际 HTTP 接口，分别核对样例、对照和拒绝路径。`examples` 保存业务示例及可人工核算的观测点。完整命令见 `.project-runtime.json`；自动化测试套件留置，不包含私有验收测试、缺陷任务或参考解答。

### 合成双门到位日志

除直接提交整理好的 `gates` 门位事件外，界面「双门到位样例」与 `examples/contacts-sample.json` 演示可选的 `gate_logs`：每扇门在各自观测时刻带开到位、关到位两个接点，服务按记录的证实时长与间隔上限重建确认动作，在「原门位证据」列出确认动作、短暂假到位、断证缺口与接点冲突，并在「闸次判读」区分完整闸次（带上行/下行方向）、折返、异常与未决。循环对账只纳入完整闸次，按实际首关与后开边界计算带符号净容积；下行保留负容积，不把出水计成引水，折返与未决不进对账。`examples/contacts-anomaly.json` 覆盖同刻交接、全闭段断证与接点冲突，`examples/contacts-reject.json` 是应被 400 阻止的输入。只含 `gates` 的旧档口径不变、仍可回放与封存。

### 扩展复核

使用「复核样例」，随后在「扩展复核」切换各项结果。有效输入见 `examples/review-sample.json` 与 `examples/review-alternate.json`。例如运行 `python3 -m levelreplay.check_flow steps` 可核对派生结果、拒绝非法复核和基础版本兼容性。
