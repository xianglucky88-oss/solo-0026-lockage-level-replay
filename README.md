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
python3 -m levelreplay.check_flow export
```

这些命令在临时成果库和临时回环端口运行实际 HTTP 接口，分别核对样例、对照和拒绝路径。`examples` 保存业务示例及可人工核算的观测点。完整命令见 `.project-runtime.json`；自动化测试套件留置，不包含私有验收测试、缺陷任务或参考解答。

### 扩展复核

使用「复核样例」，随后在「扩展复核」切换各项结果。有效输入见 `examples/review-sample.json` 与 `examples/review-alternate.json`。例如运行 `python3 -m levelreplay.check_flow steps` 可核对派生结果、拒绝非法复核和基础版本兼容性。

### 闸室失水离线诊断

复核样例同时带有 `leakage` 诊断配置：两扇门各自有见证的初态、有确证的停供时段、滚动窗长与失水限值。诊断只在双门确证关闭且停供、闸室水尺连续有证的时间段内计算滚动水量损失，超限的窗终点区间记为异常，并附最严重有证损失与门位、停供、水尺见证；不改变原始水量归账。运行 `python3 -m levelreplay.check_flow leakage` 核对。
