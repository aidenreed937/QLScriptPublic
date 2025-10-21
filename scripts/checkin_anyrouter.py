#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AnyRouter 签到脚本（适用于青龙面板）

站点： https://anyrouter.top
接口： /api/user/sign_in  （需要已登录 Cookie）

环境变量：
- ANYROUTER_COOKIE    必填，浏览器复制 anyrouter.top 域下的 Cookie，形如：key1=val1; key2=val2
- ANYROUTER_BASE      可选，默认：https://anyrouter.top
- ANYROUTER_TIMEOUT   可选，请求超时秒数，默认：15
- ANYROUTER_RETRY     可选，失败重试次数，默认：1（总尝试=1+重试）
- ANYROUTER_VERIFY    可选，是否校验证书，默认：true（可设为 false）
- ANYROUTER_HEADERS   可选，追加请求头，支持 JSON 或 k=v;k=v / 换行分隔
- ANYROUTER_PREGET    可选，是否先 GET 用户页预热会话，默认：false
\n新增调试/判定开关：
- ANYROUTER_LOG_BYTES   可选，失败时/调试用的响应预览最大字符数，默认：500
- ANYROUTER_STRICT_JSON 可选，为 true 时仅在 JSON 且 success==true 判定成功

通知（可选其一或都不配）：
- PUSHPLUS_TOKEN      PushPlus 的 token
- BARK_URL            Bark 完整推送地址，如 https://api.day.app/XXXXXXX

使用：
- 青龙新增定时： task scripts/checkin_anyrouter.py
- 建议 cron：     0 8 * * *
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

# 优先使用 requests；如不可用，降级到 urllib
try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except Exception:
    import urllib.request
    import urllib.error
    _HAS_REQUESTS = False


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def parse_bool(val: Optional[str], default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y"}


def get_env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def preview_response(text: str, limit: int) -> str:
    """生成响应预览：若为 JSON 则美化后再截断。"""
    if limit <= 0:
        return ""
    try:
        obj = json.loads(text)
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        pretty = text or ""
    if len(pretty) > limit:
        return pretty[:limit] + f"\n...[已截断，总长度={len(pretty)}，预览上限={limit}]"
    return pretty


def parse_headers(val: Optional[str]) -> Dict[str, str]:
    """解析追加请求头：支持 JSON 或 k=v/每行一对。"""
    if not val:
        return {}
    s = val.strip()
    # JSON 优先
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    # 其次 k=v 或 k: v，允许以分号或换行分割
    headers: Dict[str, str] = {}
    for pair in re.split(r"[;\n]+", s):
        pair = pair.strip()
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        elif ":" in pair:
            k, v = pair.split(":", 1)
        else:
            continue
        headers[k.strip()] = v.strip()
    return headers


def mask_secret(s: str, head: int = 6, tail: int = 6) -> str:
    """通用脱敏：保留首尾少量字符。"""
    s = (s or "").strip()
    if len(s) <= head + tail:
        return "*" * len(s)
    return f"{s[:head]}***{s[-tail:]}"


def notify(title: str, content: str) -> None:
    token = os.getenv("PUSHPLUS_TOKEN")
    bark = os.getenv("BARK_URL")
    sent = False

    if token:
        try:
            if _HAS_REQUESTS:
                requests.post(
                    "https://www.pushplus.plus/send",
                    json={"token": token, "title": title, "content": content, "template": "html"},
                    timeout=10,
                )
            else:
                data = json.dumps({"token": token, "title": title, "content": content, "template": "html"}).encode("utf-8")
                req = urllib.request.Request(
                    url="https://www.pushplus.plus/send",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10).read()
            sent = True
            log("PushPlus 通知已发送")
        except Exception as e:
            log(f"PushPlus 通知失败: {e}")

    if bark:
        try:
            if _HAS_REQUESTS:
                requests.post(bark, json={"title": title, "body": content}, timeout=10)
            else:
                data = json.dumps({"title": title, "body": content}).encode("utf-8")
                req = urllib.request.Request(
                    url=bark,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10).read()
            sent = True
            log("Bark 通知已发送")
        except Exception as e:
            log(f"Bark 通知失败: {e}")

    if not sent:
        log("未配置通知通道，已在控制台输出结果。")


def request_post(url: str, headers: Dict[str, str], timeout: int, verify: bool) -> Tuple[int, str]:
    if _HAS_REQUESTS:
        try:
            resp = requests.post(url, headers=headers, timeout=timeout, verify=verify)
            return resp.status_code, resp.text
        except Exception as e:
            raise RuntimeError(f"请求失败: {e}")
    else:
        try:
            req = urllib.request.Request(url=url, method="POST")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                text = resp.read().decode("utf-8", errors="ignore")
                return status, text
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
            return e.code, text
        except Exception as e:
            raise RuntimeError(f"请求失败: {e}")


def request_get(url: str, headers: Dict[str, str], timeout: int, verify: bool) -> Tuple[int, str]:
    if _HAS_REQUESTS:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, verify=verify)
            return resp.status_code, resp.text
        except Exception as e:
            raise RuntimeError(f"请求失败: {e}")
    else:
        try:
            req = urllib.request.Request(url=url, method="GET")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                text = resp.read().decode("utf-8", errors="ignore")
                return status, text
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
            return e.code, text
        except Exception as e:
            raise RuntimeError(f"请求失败: {e}")


def detect_success(status: int, text: str, strict_json: bool = False) -> Tuple[bool, str]:
    if strict_json:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and str(data.get("success", "")).lower() in {"true", "1"}:
                return True, "严格JSON: success=true"
            return False, "严格JSON: 未匹配 success=true"
        except Exception:
            return False, "严格JSON: 非 JSON 响应"
    # 优先尝试 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # 常见字段判断：success/ok/status/code/msg/message
            if str(data.get("success", "")).lower() in {"true", "1"}:
                return True, "success=true"
            if str(data.get("ok", "")).lower() in {"true", "1"}:
                return True, "ok=true"
            code = data.get("code")
            if code in (0, 200, "0", "200"):
                return True, f"code={code}"
            status_field = data.get("status")
            if status_field in ("success", "ok", 0, 200, "0", "200"):
                return True, f"status={status_field}"
            msg = str(data.get("msg") or data.get("message") or "")
            if any(kw in msg for kw in ("成功", "已签到", "签到成功")):
                return True, f"msg={msg}"
    except Exception:
        pass

    # 纯文本兜底
    if any(kw in text for kw in ("成功", "已签到", "签到")):
        return True, "文本包含成功关键词"

    # 状态码兜底
    if 200 <= status < 300:
        return True, f"状态码 {status}"
    return False, "未匹配成功条件"


def main() -> int:
    cookie = os.getenv("ANYROUTER_COOKIE")
    if not cookie:
        log("[错误] 未配置 ANYROUTER_COOKIE（请粘贴浏览器中的 Cookie）")
        return 2

    base = os.getenv("ANYROUTER_BASE", "https://anyrouter.top").rstrip("/")
    url = f"{base}/api/user/sign_in"
    referer = f"{base}/user"

    timeout = int(os.getenv("ANYROUTER_TIMEOUT", "15") or 15)
    retry = int(os.getenv("ANYROUTER_RETRY", "1") or 1)
    verify = parse_bool(os.getenv("ANYROUTER_VERIFY"), True)
    log_bytes = get_env_int("ANYROUTER_LOG_BYTES", 500)
    strict_json = parse_bool(os.getenv("ANYROUTER_STRICT_JSON"), False)
    preget = parse_bool(os.getenv("ANYROUTER_PREGET"), False)

    ua = os.getenv(
        "ANYROUTER_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36",
    )
    headers = {
        "Cookie": cookie,
        # 使用可覆盖的浏览器 UA，降低风控概率
        "User-Agent": ua,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }
    # 追加自定义请求头（可用于对齐浏览器请求）
    extra_headers = parse_headers(os.getenv("ANYROUTER_HEADERS"))
    if extra_headers:
        headers.update(extra_headers)

    # 打印环境参数（脱敏，不影响后续流程）
    try:
        log("=== 运行环境参数 ===")
        log(f"Base: {base}")
        log(f"签到接口: {url}")
        log(f"Referer: {referer}")
        log(f"User-Agent: {ua}")
        log(f"Cookie(脱敏): {mask_secret(cookie)}")
        log(f"Timeout: {timeout}s, Retry: {retry}, Verify: {verify}")
        log(f"Strict JSON: {strict_json}, Log Bytes: {log_bytes}, Preget: {preget}")
        # 系统代理
        hp = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        hps = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if hp or hps:
            log(f"系统代理: http={hp or ''}, https={hps or ''}")
        else:
            log("系统代理: 未检测到 HTTP_PROXY/HTTPS_PROXY")
        # 追加头键名（不打印值，避免泄露）
        if extra_headers:
            log(f"追加请求头键: {', '.join(extra_headers.keys())}")
        log(f"HTTP后端: {'requests' if _HAS_REQUESTS else 'urllib'}")
        log("=== 参数打印完成，开始请求 ===")
    except Exception:
        pass

    # 可选预检：访问用户页，便于种植风控 Cookie 或初始化会话
    if preget:
        try:
            log(f"预检 GET: {referer}")
            s, t = request_get(referer, {k: v for k, v in headers.items() if k.lower() != "cookie"}, timeout, verify)
            log(f"预检响应状态: {s}")
            if log_bytes > 0:
                prev = preview_response(t or "", log_bytes)
                if prev:
                    log("预检内容预览:\n" + prev)
        except Exception as e:
            log(f"预检失败（忽略继续）: {e}")

    attempts = 0
    last_status = 0
    last_text = ""
    while True:
        attempts += 1
        try:
            log(f"开始第 {attempts} 次签到请求: POST {url}")
            status, text = request_post(url, headers, timeout, verify)
            last_status, last_text = status, text
            ok, reason = detect_success(status, text, strict_json)
            log(f"响应状态: {status}, 判定: {'成功' if ok else '失败'}，原因: {reason}")
            if not ok and log_bytes > 0:
                # 控制台输出响应预览，便于排障（不依赖通知）
                preview = preview_response(text or "", log_bytes)
                if preview:
                    log("响应内容预览:\n" + preview)
            if ok:
                notify("AnyRouter 签到：成功", f"状态: {status}<br/>说明: {reason}")
                return 0
        except Exception as e:
            log(f"请求异常: {e}")

        if attempts > retry:
            break
        time.sleep(2)

    # 失败时输出可配置长度的响应预览（JSON 自动美化）
    preview = preview_response(last_text or "", max(0, log_bytes)) if last_text else ""
    notify(
        "AnyRouter 签到：失败",
        f"最后状态: {last_status}<br/>响应预览:<br/><pre>{(preview or '')}</pre>",
    )
    # 同步在控制台输出预览，避免未配置通知时无法排查
    if preview:
        log("最终响应内容预览:\n" + preview)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("用户中断")
        sys.exit(130)
