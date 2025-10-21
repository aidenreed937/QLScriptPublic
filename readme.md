**项目说明**
- 本仓库提供一个通用“签到脚本”模板，便于在青龙面板中通过环境变量快速对接任意签到接口。
- 参考思路来自 smallfawn/QLScriptPublic，但本脚本为通用模板，不绑定具体站点。

**文件结构**
- `scripts/checkin_template.py`: 通用签到脚本（Python 3）。

**编码与终端**
- 使用 PowerShell 7 启动，并在首次执行前设置：
  - `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`
- 仓库内所有文件均为 UTF-8 (no BOM)。

**运行前置**
- 青龙镜像通常包含 Python 3。如缺 `requests`，脚本会自动降级 `urllib`，无需额外安装。

**环境变量**
- `CHECKIN_URL`                必填，目标签到接口，例如：https://example.com/api/checkin
- `CHECKIN_METHOD`             可选，GET/POST，默认：POST
- `CHECKIN_HEADERS`            可选，请求头，支持 JSON 或 `k1=v1;k2=v2`/`k1: v1` 格式
- `CHECKIN_BODY`               可选，请求体（POST 时常用），支持 JSON 文本或原始字符串
- `CHECKIN_SUCCESS_KEYWORD`    可选，响应中包含该字符串即视为成功
- `CHECKIN_SUCCESS_REGEX`      可选，正则匹配成功，优先级高于关键字
- `CHECKIN_TIMEOUT`            可选，请求超时秒数，默认：15
- `CHECKIN_RETRY`              可选，失败重试次数，默认：1（总尝试=1+重试）
- `CHECKIN_VERIFY`             可选，是否校验证书，默认：true（可设为 false）

**通知（可选其一或都不配）**
- `PUSHPLUS_TOKEN`             PushPlus token，使用 https://www.pushplus.plus/send 推送
- `BARK_URL`                   Bark 完整推送地址，如 https://api.day.app/XXXXXXX

**代理（可选）**
- `HTTP_PROXY` / `HTTPS_PROXY` 需代理时设置，脚本自动透传

**青龙面板使用**
- 复制或挂载仓库到青龙：将 `scripts/checkin_template.py` 放入脚本目录。
- 新建定时任务：`task scripts/checkin_template.py`
- 建议 cron（每日 08:00 执行）：`0 8 * * *`
- 在“环境变量”里配置上述参数，至少设置 `CHECKIN_URL`。

**AnyRouter 专用脚本**
- 文件：`scripts/checkin_anyrouter.py`
- 站点：`https://anyrouter.top`，接口：`/api/user/sign_in`
- 需要在浏览器复制 `anyrouter.top` 域的完整 Cookie（登录后获取），配置到青龙环境变量：
  - `ANYROUTER_COOKIE`（必填）
  - `ANYROUTER_BASE`（可选，默认 `https://anyrouter.top`）
  - `ANYROUTER_TIMEOUT`（可选，默认 15）
  - `ANYROUTER_RETRY`（可选，默认 1）
  - `ANYROUTER_VERIFY`（可选，默认 true）
- 通知可选其一：`PUSHPLUS_TOKEN` 或 `BARK_URL`
- 定时任务示例：`task scripts/checkin_anyrouter.py`
- 建议 cron：`0 8 * * *`

**使用模板直接配置 AnyRouter（可选方案）**
- 也可用通用模板 `scripts/checkin_template.py`：
  - `CHECKIN_URL` = `https://anyrouter.top/api/user/sign_in`
  - `CHECKIN_METHOD` = `POST`
  - `CHECKIN_HEADERS` = `{"Cookie":"<粘贴你的Cookie>", "User-Agent":"Mozilla/5.0 QL-Checkin", "Referer":"https://anyrouter.top/user"}`
  - `CHECKIN_BODY` 为空（不设置）

**本地快速测试（PowerShell 7）**
- 示例（以 JSON Body、关键字判断为例）：
```
$env:CHECKIN_URL = "https://httpbin.org/post"
$env:CHECKIN_METHOD = "POST"
$env:CHECKIN_HEADERS = '{"User-Agent": "QL-Checkin/1.0"}'
$env:CHECKIN_BODY = '{"action":"checkin"}'
$env:CHECKIN_SUCCESS_KEYWORD = "json"
python ./scripts/checkin_template.py
```

**常见用法提示**
- 需要携带 Cookie 签到时，可在 `CHECKIN_HEADERS` 中设置：`{"Cookie": "key=value; ..."}`
- Body 为 x-www-form-urlencoded 时：
  - 将 `CHECKIN_BODY` 设为 `k1=v1&k2=v2`，并在 `CHECKIN_HEADERS` 中加 `{"Content-Type":"application/x-www-form-urlencoded"}`
- 若接口返回结构复杂，建议配置 `CHECKIN_SUCCESS_REGEX` 更为稳妥。

**注意**
- 本脚本仅提供通用框架，不包含任何站点的专有逻辑或破解流程。
- 使用前请确认遵守目标站点的使用条款与法律法规。
