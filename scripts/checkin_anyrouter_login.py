#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AnyRouter 账号密码登录 + 签到脚本（可本地/青龙/CI 环境运行）

站点： https://anyrouter.top
接口： /api/user/sign_in

功能：
- 使用账号密码自动登录（优先采用浏览器自动化以绕过 JS 反爬），随后调用签到接口。
- 也支持纯 API 登录（不建议，目标站存在 JS 挑战/风控，常被拦截）。

环境变量：
- ANYROUTER_USER           必填，登录账号（邮箱/用户名）
- ANYROUTER_PASS           必填，登录密码
- ANYROUTER_BASE           可选，基础地址，默认：https://anyrouter.top
- ANYROUTER_SIGN_PATH      可选，签到路径，默认：/api/user/sign_in
- ANYROUTER_LOGIN_MODE     可选，登录模式：browser/api/auto，默认：browser
- ANYROUTER_LOGIN_URL      可选，登录页路径（browser 模式使用），默认自动探测（/login -> /auth/login -> /signin）
- ANYROUTER_LOGIN_API      可选，API 登录地址（api 模式使用），默认：/api/auth/login（如站点不同请覆盖）
- ANYROUTER_LOGIN_FIELD_USER  可选，API 登录字段名（账号），默认：email
- ANYROUTER_LOGIN_FIELD_PASS  可选，API 登录字段名（密码），默认：password
- ANYROUTER_TIMEOUT        可选，请求超时秒数，默认：15
- ANYROUTER_VERIFY         可选，是否校验证书，默认：true
- ANYROUTER_RETRY          可选，签到失败重试次数，默认：1（总尝试=1+重试）
- ANYROUTER_HEADERS        可选，追加请求头，支持 JSON 或 k=v;k=v / 换行分隔
- ANYROUTER_UA             可选，自定义 User-Agent
- ANYROUTER_PREGET         可选，签到前是否 GET 用户页预热会话，默认：false
- ANYROUTER_NEW_API_USER   可选，是否携带 New-Api-User 头，未设默认 "1"；设为 0/false/off/空 关闭
\n+浏览器模式定制：
- ANYROUTER_USER_SELECTOR   可选，自定义账号输入框选择器（例如 input[name="username"]）
- ANYROUTER_PASS_SELECTOR   可选，自定义密码输入框选择器
- ANYROUTER_SUBMIT_SELECTOR 可选，自定义提交按钮选择器
- ANYROUTER_HEADLESS        可选，是否无头（true/false），默认 true
- ANYROUTER_SLOWMO          可选，慢动作毫秒数，用于演示观察
- ANYROUTER_VIEWPORT_WIDTH/ANYROUTER_VIEWPORT_HEIGHT 可选，视窗宽/高

通知（可选其一或都不配）：
- PUSHPLUS_TOKEN           PushPlus 的 token
- BARK_URL                 Bark 完整推送地址，如 https://api.day.app/XXXXXXX

依赖：
- requests（自动检测，无则降级 urllib，建议安装 requests）
- browser 模式需要 Playwright：
  pip install playwright
  playwright install chromium

用法示例：
- 本地运行：
  set ANYROUTER_USER=you@example.com
  set ANYROUTER_PASS=your_password
  python scripts/checkin_anyrouter_login.py

- 青龙定时：
  task scripts/checkin_anyrouter_login.py

注意：目标站首页/接口存在 JS 反爬（含 arg1、acw_sc__v2 等），纯 HTTP 请求常被 JS 挑战拦截。
      因此默认登录模式为 browser。若你所在环境无法使用浏览器，请尝试 ANYROUTER_LOGIN_MODE=api 并
      覆盖 ANYROUTER_LOGIN_API/字段名，以匹配站点真实登录 API；但不能保证绕过风控。
"""

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

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


def detect_success(status: int, text: str) -> Tuple[bool, str]:
    # 优先尝试 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
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


def make_default_headers(base: str, ua: str, referer: str, new_api_user_val: Optional[str]) -> Dict[str, str]:
    headers = {
        "User-Agent": ua,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }
    if new_api_user_val is not None:
        headers["New-Api-User"] = new_api_user_val
    extra_headers = parse_headers(os.getenv("ANYROUTER_HEADERS"))
    if extra_headers:
        headers.update(extra_headers)
    return headers


def session_get(session: Any, url: str, headers: Dict[str, str], timeout: int, verify: bool) -> Tuple[int, str]:
    if _HAS_REQUESTS:
        try:
            r = session.get(url, headers=headers, timeout=timeout, verify=verify)
            return r.status_code, r.text
        except Exception as e:
            raise RuntimeError(f"GET 失败: {e}")
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
            raise RuntimeError(f"GET 失败: {e}")


def session_post(session: Any, url: str, headers: Dict[str, str], timeout: int, verify: bool, json_body: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
    if _HAS_REQUESTS:
        try:
            if json_body is None:
                r = session.post(url, headers=headers, timeout=timeout, verify=verify)
            else:
                r = session.post(url, headers=headers, timeout=timeout, verify=verify, json=json_body)
            return r.status_code, r.text
        except Exception as e:
            raise RuntimeError(f"POST 失败: {e}")
    else:
        try:
            data = None
            req = urllib.request.Request(url=url, method="POST")
            if json_body is not None:
                data = json.dumps(json_body).encode("utf-8")
                req.add_header("Content-Type", "application/json")
            for k, v in headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                text = resp.read().decode("utf-8", errors="ignore")
                return status, text
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
            return e.code, text
        except Exception as e:
            raise RuntimeError(f"POST 失败: {e}")


def try_api_login(session: Any, base: str, headers: Dict[str, str], timeout: int, verify: bool) -> bool:
    """尝试使用 API 登录。注意：若站点有 JS 挑战/WAF，可能直接返回 HTML/被拦截。"""
    login_api_path = os.getenv("ANYROUTER_LOGIN_API", "/api/auth/login")
    login_api = f"{base}{login_api_path}" if not login_api_path.startswith("http") else login_api_path
    user_field = os.getenv("ANYROUTER_LOGIN_FIELD_USER", "email")
    pass_field = os.getenv("ANYROUTER_LOGIN_FIELD_PASS", "password")
    user = os.getenv("ANYROUTER_USER")
    pwd = os.getenv("ANYROUTER_PASS")
    if not user or not pwd:
        log("[错误] 未配置 ANYROUTER_USER / ANYROUTER_PASS")
        return False

    body = {user_field: user, pass_field: pwd}
    h = dict(headers)
    h.setdefault("Content-Type", "application/json;charset=utf-8")
    # API 登录不应携带 Cookie
    h.pop("Cookie", None)

    log(f"尝试 API 登录: {login_api} (字段: {user_field}/{pass_field})")
    status, text = session_post(session, login_api, h, timeout, verify, json_body=body)
    # 反爬常返回 HTML 含 arg1 / acw_sc__v2
    if re.search(r"var\s+arg1\s*=\s*'", text or ""):
        log("检测到 JS 挑战（arg1），API 登录被拦截")
        return False
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if str(data.get("success", "")).lower() in {"true", "1"}:
                log("API 登录成功: success=true")
                return True
            if data.get("code") in (0, 200, "0", "200"):
                log(f"API 登录成功: code={data.get('code')}")
                return True
            if data.get("token") or data.get("accessToken"):
                log("API 登录可能成功（返回 token），后续以 Cookie 验证")
                return True
        log(f"API 登录未判定成功，状态={status}，响应预览:\n{preview_response(text, 500)}")
    except Exception:
        log(f"API 登录非 JSON 响应，状态={status}，响应预览:\n{preview_response(text, 500)}")
    return False


def browser_login_and_sign(base: str, sign_url: str, timeout: int, verify: bool, ua: str, log_bytes: int, new_api_user_val: Optional[str]) -> Tuple[int, str]:
    """通过 Playwright 浏览器自动化登录并调用签到接口。返回 (status, text)。"""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        raise RuntimeError(
            "未安装 Playwright，请执行: pip install playwright && playwright install chromium"
        )

    login_paths: List[str] = []
    # 用户可显式指定登录页
    env_login_url = os.getenv("ANYROUTER_LOGIN_URL")
    if env_login_url:
        login_paths.append(env_login_url)
    # 常见候选路径
    login_paths.extend(["/login", "/auth/login", "/signin"])  # 依次尝试

    with sync_playwright() as p:
        # 允许通过环境变量控制是否无头、慢动作与视窗尺寸
        headless = parse_bool(os.getenv("ANYROUTER_HEADLESS"), True)
        slow_mo = get_env_int("ANYROUTER_SLOWMO", 0)
        vp_w = get_env_int("ANYROUTER_VIEWPORT_WIDTH", 0)
        vp_h = get_env_int("ANYROUTER_VIEWPORT_HEIGHT", 0)

        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo if slow_mo > 0 else None)
        # Playwright 的 new_context 默认就是“无痕/临时会话”（非持久化），不会复用磁盘用户数据目录
        context_kwargs: Dict[str, Any] = {
            "ignore_https_errors": not verify,
            "user_agent": ua,
        }
        if vp_w > 0 and vp_h > 0:
            context_kwargs["viewport"] = {"width": vp_w, "height": vp_h}
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # 打开首页，触发 JS 挑战并种植必要 Cookie
        log(f"打开首页: {base}")
        page.goto(base, wait_until="domcontentloaded", timeout=timeout * 1000)

        # 寻找/打开登录页
        goto_ok = False
        for path in login_paths:
            full = path if path.startswith("http") else f"{base}{path}"
            try:
                log(f"尝试打开登录页: {full}")
                page.goto(full, wait_until="domcontentloaded", timeout=timeout * 1000)
                goto_ok = True
                break
            except Exception as e:
                log(f"登录页尝试失败: {e}")
        if not goto_ok:
            # 尝试页面内点击“登录”按钮
            try:
                log("尝试点击页面内“登录/Sign in/Log in”链接")
                # 可能的按钮文本
                for text_sel in ["登录", "Sign in", "Log in", "Login", "去登录"]:
                    if page.get_by_role("link", name=text_sel).first.is_visible():
                        page.get_by_role("link", name=text_sel).first.click()
                        break
                page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
                goto_ok = True
            except Exception as e:
                log(f"页面内点击登录失败: {e}")
        if not goto_ok:
            raise RuntimeError("无法打开登录页，请通过 ANYROUTER_LOGIN_URL 指定登录地址")

        # 处理可能出现的“系统公告”弹窗，避免遮挡表单
        try:
            dialog = page.get_by_role("dialog", name="系统公告")
            if dialog.is_visible():
                for btn_name in ["关闭公告", "今日关闭", "close"]:
                    try:
                        btn = dialog.get_by_role("button", name=btn_name)
                        if btn.is_visible():
                            log(f"检测到系统公告弹窗，点击 {btn_name} 关闭")
                            btn.click()
                            break
                    except Exception:
                        pass
                # 等待弹窗消失（尽量不阻塞太久）
                try:
                    dialog.first.wait_for(state="hidden", timeout=2000)
                except Exception:
                    pass
        except Exception:
            pass

        # 填写表单（尽量兼容常见站点字段）
        user = os.getenv("ANYROUTER_USER", "")
        pwd = os.getenv("ANYROUTER_PASS", "")
        if not user or not pwd:
            raise RuntimeError("未配置 ANYROUTER_USER/ANYROUTER_PASS")

        user_filled = False
        pass_filled = False

        # 若页面存在明显的输入框占位符/名称，逐个尝试
        # 允许通过环境变量自定义选择器，便于针对特殊页面结构适配
        custom_user_sel = os.getenv("ANYROUTER_USER_SELECTOR")
        custom_pass_sel = os.getenv("ANYROUTER_PASS_SELECTOR")
        custom_submit_sel = os.getenv("ANYROUTER_SUBMIT_SELECTOR")

        # 优先尝试基于可访问性名称的稳定定位（AnyRouter 登录页）
        try:
            page.get_by_role("heading", name="登 录").first.wait_for(timeout=2000)
        except Exception:
            pass
        try:
            elu = page.get_by_label("用户名或邮箱").first
            if elu.is_visible():
                elu.fill(user, timeout=timeout * 1000)
                user_filled = True
        except Exception:
            pass
        try:
            elp = page.get_by_label("密码").first
            if elp.is_visible():
                elp.fill(pwd, timeout=timeout * 1000)
                pass_filled = True
        except Exception:
            pass
        # 若 label 定位失败，尝试 placeholder 定位
        if not user_filled:
            try:
                elu2 = page.get_by_placeholder("用户名或邮箱").first
                if elu2.is_visible():
                    elu2.fill(user, timeout=timeout * 1000)
                    user_filled = True
            except Exception:
                pass
        if not pass_filled:
            try:
                elp2 = page.get_by_placeholder("密码").first
                if elp2.is_visible():
                    elp2.fill(pwd, timeout=timeout * 1000)
                    pass_filled = True
            except Exception:
                pass

        user_selectors = ([custom_user_sel] if custom_user_sel else []) + [
            'input[name="email"]',
            'input[name="username"]',
            'input[type="email"]',
            'input[placeholder*="邮箱"]',
            'input[placeholder*="email"]',
            'input[placeholder*="用户"]',
            'input[placeholder*="账号"]',
            'input[placeholder*="賬號"]',
        ]
        pass_selectors = ([custom_pass_sel] if custom_pass_sel else []) + [
            'input[name="password"]',
            'input[type="password"]',
            'input[placeholder*="密码"]',
            'input[placeholder*="密碼"]',
        ]

        if not user_filled:
            for sel in user_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible():
                        el.fill(user, timeout=timeout * 1000)
                        user_filled = True
                        break
                except Exception:
                    pass
        if not pass_filled:
            for sel in pass_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible():
                        el.fill(pwd, timeout=timeout * 1000)
                        pass_filled = True
                        break
                except Exception:
                    pass

        if not user_filled or not pass_filled:
            # 退而求其次：尝试表单中的前两个可见输入框
            try:
                inputs = page.locator('input').filter(has_not=page.locator('[type="hidden"]'))
                if inputs.count() >= 2:
                    inputs.nth(0).fill(user, timeout=timeout * 1000)
                    inputs.nth(1).fill(pwd, timeout=timeout * 1000)
                    user_filled = pass_filled = True
            except Exception:
                pass

        if not user_filled or not pass_filled:
            raise RuntimeError("未能自动识别登录表单，请通过 ANYROUTER_LOGIN_URL/页面结构调整后再试")

        # 提交表单
        submit_clicked = False
        # 优先尝试可访问性名称“继续”按钮
        try:
            btn = page.get_by_role("button", name="继续").first
            if btn.is_visible():
                btn.click(timeout=timeout * 1000)
                submit_clicked = True
        except Exception:
            pass

        submit_candidates = ([custom_submit_sel] if custom_submit_sel else []) + [
            'button[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'text=登录',
            'text=Sign in',
            'text=Log in',
        ]
        if not submit_clicked:
            for sel in submit_candidates:
                try:
                    el = page.locator(sel).first
                    if el.is_visible():
                        el.click(timeout=timeout * 1000)
                        submit_clicked = True
                        break
                except Exception:
                    pass
        if not submit_clicked:
            # 回车提交
            try:
                page.keyboard.press("Enter")
                submit_clicked = True
            except Exception:
                pass

        if not submit_clicked:
            raise RuntimeError("未找到可点击的提交按钮")

        # 等待跳转/接口完成（常见登录后会跳到 /user）
        try:
            page.wait_for_load_state("networkidle", timeout=max(2000, timeout * 1000))
        except Exception:
            pass

        # 使用 Page 的 Request API 调用签到接口（复用 Cookie/会话）
        headers = make_default_headers(base, ua, referer=f"{base}/user", new_api_user_val=new_api_user_val)
        try:
            resp = context.request.post(sign_url, headers=headers, timeout=timeout * 1000)
            status = resp.status
            text = resp.text()
        except Exception as e:
            # 兜底：用页面环境 fetch（在浏览器上下文内调用，完全复用同源 Cookie）
            try:
                js = (
                    "return fetch(arguments[0], {method:'POST', headers: arguments[1]})"
                    ".then(r=>r.text().then(t=>[r.status,t]))"
                )
                status, text = page.evaluate(js, sign_url, headers)
            except Exception as ee:
                raise RuntimeError(f"浏览器内发起签到失败: {e}; 兜底失败: {ee}")
        finally:
            context.close()
            browser.close()

        return int(status), str(text or "")


def main() -> int:
    user = os.getenv("ANYROUTER_USER")
    pwd = os.getenv("ANYROUTER_PASS")
    if not user or not pwd:
        log("[错误] 未配置 ANYROUTER_USER / ANYROUTER_PASS")
        return 2

    base = os.getenv("ANYROUTER_BASE", "https://anyrouter.top").rstrip("/")
    sign_path = os.getenv("ANYROUTER_SIGN_PATH", "/api/user/sign_in")
    sign_url = f"{base}{sign_path}" if not sign_path.startswith("http") else sign_path

    timeout = int(os.getenv("ANYROUTER_TIMEOUT", "15") or 15)
    retry = int(os.getenv("ANYROUTER_RETRY", "1") or 1)
    verify = parse_bool(os.getenv("ANYROUTER_VERIFY"), True)
    log_bytes = get_env_int("ANYROUTER_LOG_BYTES", 500)
    login_mode = os.getenv("ANYROUTER_LOGIN_MODE", "browser").strip().lower()  # browser/api/auto

    ua = os.getenv(
        "ANYROUTER_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36",
    )

    # 兼容可配置的新用户标识头
    _new_api_user_env = os.getenv("ANYROUTER_NEW_API_USER")
    if _new_api_user_env is None:
        new_api_user_val: Optional[str] = "1"
    else:
        _v = str(_new_api_user_env).strip()
        if _v.lower() in {"", "0", "false", "no", "off"}:
            new_api_user_val = None
        else:
            new_api_user_val = _v

    # 打印环境参数（脱敏）
    try:
        log("=== 运行环境参数 ===")
        log(f"Base: {base}")
        log(f"签到接口: {sign_url}")
        log(f"User: {mask_secret(user)}")
        log(f"Timeout: {timeout}s, Retry: {retry}, Verify: {verify}")
        log(f"LoginMode: {login_mode}, HTTP后端: {'requests' if _HAS_REQUESTS else 'urllib'}")
        hp = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        hps = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if hp or hps:
            log(f"系统代理: http={hp or ''}, https={hps or ''}")
        else:
            log("系统代理: 未检测到 HTTP_PROXY/HTTPS_PROXY")
        log("=== 参数打印完成 ===")
    except Exception:
        pass

    # 模式分支
    attempts = 0
    last_status = 0
    last_text = ""

    while True:
        attempts += 1
        try:
            log(f"开始第 {attempts} 次流程: 登录 -> 签到")

            if login_mode in ("browser", "auto"):
                # 使用浏览器自动化（推荐）
                status, text = browser_login_and_sign(
                    base=base,
                    sign_url=sign_url,
                    timeout=timeout,
                    verify=verify,
                    ua=ua,
                    log_bytes=log_bytes,
                    new_api_user_val=new_api_user_val,
                )
                last_status, last_text = status, text
                ok, reason = detect_success(status, text)
                log(f"签到响应: {status}, 判定: {'成功' if ok else '失败'}，原因: {reason}")
                if not ok and log_bytes > 0:
                    prev = preview_response(text or "", log_bytes)
                    if prev:
                        log("响应内容预览:\n" + prev)
                if ok:
                    notify("AnyRouter 签到：成功", f"状态: {status}<br/>说明: {reason}")
                    return 0
                # browser 模式失败可考虑不再尝试 api；这里仅在 auto 下继续 api
                if login_mode == "browser":
                    raise RuntimeError("浏览器模式签到失败")

            if login_mode in ("api", "auto"):
                # 纯 API 流程（可能被 JS 挑战拦截，仅在无风控时可用）
                if _HAS_REQUESTS:
                    session: Any = requests.Session()
                else:
                    # 简单模拟：无 requests 时使用 None 占位，内部走 urllib
                    session = object()

                headers = make_default_headers(base, ua, referer=f"{base}/user", new_api_user_val=new_api_user_val)

                # 可选预热：访问用户页（不带 Cookie），部分站点用于初始化会话 / 下发 JS 挑战 Cookie
                try:
                    log(f"预热 GET: {base}/user")
                    s, t = session_get(session, f"{base}/user", {k: v for k, v in headers.items() if k.lower() != 'cookie'}, timeout, verify)
                    log(f"预热状态: {s}")
                except Exception as e:
                    log(f"预热失败（忽略）: {e}")

                # 尝试 API 登录
                if not try_api_login(session, base, headers, timeout, verify):
                    raise RuntimeError("API 登录失败或被拦截")

                # 登录成功后调用签到
                log(f"POST 签到: {sign_url}")
                status, text = session_post(session, sign_url, headers, timeout, verify)
                last_status, last_text = status, text
                ok, reason = detect_success(status, text)
                log(f"签到响应: {status}, 判定: {'成功' if ok else '失败'}，原因: {reason}")
                if not ok and log_bytes > 0:
                    prev = preview_response(text or "", log_bytes)
                    if prev:
                        log("响应内容预览:\n" + prev)
                if ok:
                    notify("AnyRouter 签到：成功", f"状态: {status}<br/>说明: {reason}")
                    return 0

        except Exception as e:
            log(f"本次流程异常: {e}")

        if attempts > retry:
            break
        time.sleep(2)

    # 失败收尾
    preview = preview_response(last_text or "", max(0, log_bytes)) if last_text else ""
    notify(
        "AnyRouter 签到：失败",
        f"最后状态: {last_status}<br/>响应预览:<br/><pre>{(preview or '')}</pre>",
    )
    if preview:
        log("最终响应内容预览:\n" + preview)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("用户中断")
        sys.exit(130)
