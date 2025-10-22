#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AnyRouter 浏览器版签到脚本（支持配置，支持可视化/静默两种模式）

特性：
- 读取 JSON 配置并可被命令行覆盖；
- Playwright 浏览器自动化完成登录（自动关闭“系统公告”、自动切换“邮箱或用户名登录”）；
- 复用浏览器会话调用签到接口；优先使用 context.request.post，失败自动回退到页面内 fetch（同源 Cookie 更稳）；
- 支持可视化(Headed) 与 静默(Headless) 两种模式；
- 成功/失败可打印响应预览；

依赖：
  pip install playwright
  playwright install chromium

示例：
  python scripts/anyrouter_browser_checkin.py \
    --config scripts/test/checkin_anyrouter_login_test.config.json \
    --headed --slowmo 300 --viewport 1280x900 --log-success-bytes 1500

配置(JSON)建议字段：
- user            必填，登录账号（邮箱/用户名）
- pass            必填，登录密码
- base            可选，站点地址，默认 https://anyrouter.top
- login_url       可选，登录页路径/URL，默认自动探测 /login -> /auth/login -> /signin
- sign_path       可选，签到相对路径或绝对 URL，默认 /api/user/sign_in
- timeout         可选，超时秒，默认 15
- verify          可选，是否校验证书，默认 true
- ua              可选，自定义 User-Agent
- headers         可选，追加请求头(JSON 或 k=v; 换行分隔)
- new_api_user    可选，设置 New-Api-User 头；设为空串/false/0 关闭
- headed          可选，是否可视化(True/False)
- slowmo          可选，慢动作毫秒
- viewport        可选，视窗字符串，如 1280x900
- user_selector / pass_selector / submit_selector 可选，自定义表单选择器
- log_bytes       可选，失败响应预览最大字符数，默认 500
- log_success_bytes 可选，成功响应预览最大字符数，默认 0(不打印)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple, List


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_bool(val: Optional[str | bool], default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


def preview_response(text: str, limit: int) -> str:
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
    if not val:
        return {}
    s = val.strip()
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    result: Dict[str, str] = {}
    for line in s.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
        elif "=" in line:
            k, v = line.split("=", 1)
        elif ";" in line:
            k, v = line.split(";", 1)
        else:
            continue
        result[str(k).strip()] = str(v).strip()
    return result


def detect_success(status: int, text: str) -> Tuple[bool, str]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            if str(obj.get("success", "")).lower() in {"true", "1"}:
                return True, "success=true"
            if obj.get("code") in (0, 200, "0", "200"):
                return True, f"code={obj.get('code')}"
            if obj.get("token") or obj.get("accessToken"):
                return True, "返回 token"
    except Exception:
        pass
    if any(kw in text for kw in ("成功", "已签到", "签到")):
        return True, "文本包含成功关键词"
    if 200 <= status < 300:
        return True, f"状态码 {status}"
    return False, "未匹配成功条件"


def try_import_playwright() -> None:
    try:
        import playwright  # type: ignore
        _ = playwright  # noqa
    except Exception:
        raise RuntimeError("未安装 Playwright，请先执行: pip install playwright && playwright install chromium")


def close_announcement(page: Any, timeout_ms: int = 2000) -> None:
    """关闭“系统公告”弹窗（存在则关闭）。"""
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
            try:
                dialog.first.wait_for(state="hidden", timeout=timeout_ms)
            except Exception:
                pass
    except Exception:
        pass


def switch_to_email_login(page: Any, timeout_ms: int = 3000) -> None:
    """若显示多登录方式，点击“使用 邮箱或用户名 登录”。"""
    try:
        for btn_name in [
            "使用 邮箱或用户名 登录",
            "mail 使用 邮箱或用户名 登录",
            "邮箱或用户名 登录",
        ]:
            btn = page.get_by_role("button", name=btn_name).first
            if btn.is_visible():
                log(f"检测到多方式入口，点击切换至邮箱/用户名登录: {btn_name}")
                btn.click(timeout=timeout_ms)
                break
    except Exception:
        pass


def fill_credentials(page: Any, user: str, pwd: str, timeout_ms: int, user_sel: Optional[str], pass_sel: Optional[str]) -> None:
    """填写账号密码，优先使用可访问性标签，再回退 placeholder/CSS。"""
    user_filled = False
    pass_filled = False
    # label 优先
    try:
        elu = page.get_by_label("用户名或邮箱").first
        if elu.is_visible():
            elu.fill(user, timeout=timeout_ms)
            user_filled = True
    except Exception:
        pass
    try:
        elp = page.get_by_label("密码").first
        if elp.is_visible():
            elp.fill(pwd, timeout=timeout_ms)
            pass_filled = True
    except Exception:
        pass
    # placeholder 回退
    if not user_filled:
        try:
            elu2 = page.get_by_placeholder("用户名或邮箱").first
            if elu2.is_visible():
                elu2.fill(user, timeout=timeout_ms)
                user_filled = True
        except Exception:
            pass
    if not pass_filled:
        try:
            elp2 = page.get_by_placeholder("密码").first
            if elp2.is_visible():
                elp2.fill(pwd, timeout=timeout_ms)
                pass_filled = True
        except Exception:
            pass
    # 自定义选择器 / 常见选择器
    user_selectors: List[Optional[str]] = [user_sel] if user_sel else []
    user_selectors += [
        'input[name="email"]',
        'input[name="username"]',
        'input[type="email"]',
        'input[placeholder*="邮箱"]',
        'input[placeholder*="email"]',
        'input[placeholder*="用户"]',
        'input[placeholder*="账号"]',
        'input[placeholder*="賬號"]',
    ]
    pass_selectors: List[Optional[str]] = [pass_sel] if pass_sel else []
    pass_selectors += [
        'input[name="password"]',
        'input[type="password"]',
        'input[placeholder*="密码"]',
        'input[placeholder*="密碼"]',
    ]
    if not user_filled:
        for sel in user_selectors:
            if not sel:
                continue
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    el.fill(user, timeout=timeout_ms)
                    user_filled = True
                    break
            except Exception:
                pass
    if not pass_filled:
        for sel in pass_selectors:
            if not sel:
                continue
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    el.fill(pwd, timeout=timeout_ms)
                    pass_filled = True
                    break
            except Exception:
                pass
    if not user_filled or not pass_filled:
        # 兜底：前两个可见输入框
        try:
            inputs = page.locator('input').filter(has_not=page.locator('[type="hidden"]'))
            if inputs.count() >= 2:
                inputs.nth(0).fill(user, timeout=timeout_ms)
                inputs.nth(1).fill(pwd, timeout=timeout_ms)
                user_filled = pass_filled = True
        except Exception:
            pass
    if not user_filled or not pass_filled:
        raise RuntimeError("未能自动识别登录表单，请检查页面结构或提供自定义选择器")


def click_submit(page: Any, timeout_ms: int, submit_sel: Optional[str]) -> None:
    # 优先“继续”按钮
    try:
        btn = page.get_by_role("button", name="继续").first
        if btn.is_visible():
            btn.click(timeout=timeout_ms)
            return
    except Exception:
        pass
    # 自定义与常见候选
    candidates: List[Optional[str]] = [submit_sel] if submit_sel else []
    candidates += [
        'button[type="submit"]',
        'button:has-text("登录")',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
        'text=登录',
        'text=Sign in',
        'text=Log in',
    ]
    for sel in candidates:
        if not sel:
            continue
        try:
            el = page.locator(sel).first
            if el.is_visible():
                el.click(timeout=timeout_ms)
                return
        except Exception:
            pass
    # 回车提交
    try:
        page.keyboard.press("Enter")
        return
    except Exception:
        pass
    raise RuntimeError("未找到可点击的提交按钮")


def make_default_headers(base: str, ua: str, referer: str, new_api_user_val: Optional[str]) -> Dict[str, str]:
    headers = {
        "User-Agent": ua,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }
    if new_api_user_val is not None:
        headers["New-Api-User"] = new_api_user_val
    extra = parse_headers(os.getenv("ANYROUTER_HEADERS") or "")
    if extra:
        headers.update(extra)
    return headers


def browser_login_and_sign(
    base: str,
    login_url: Optional[str],
    sign_url: str,
    timeout: int,
    verify: bool,
    ua: str,
    headed: bool,
    slowmo: int,
    viewport: Optional[str],
    selectors: Dict[str, Optional[str]],
    log_success_bytes: int,
) -> Tuple[int, str]:
    """使用浏览器登录并调用签到接口，返回 (status, text)。"""
    try_import_playwright()
    from playwright.sync_api import sync_playwright  # type: ignore

    login_paths: List[str] = []
    if login_url:
        login_paths.append(login_url)
    login_paths.extend(["/login", "/auth/login", "/signin"])  # 依次尝试

    # 解析视窗
    vp_w = vp_h = 0
    if viewport:
        try:
            w_h = str(viewport).lower().split("x", 1)
            if len(w_h) == 2:
                vp_w = int(w_h[0]); vp_h = int(w_h[1])
        except Exception:
            pass

    with sync_playwright() as p:
        # Headless 反检测：为无头模式追加启动参数
        launch_args: List[str] = ["--disable-blink-features=AutomationControlled"]
        # 某些环境需要禁用 sandbox（如容器/CI），本地可忽略
        launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]

        browser = p.chromium.launch(
            headless=not headed,
            slow_mo=slowmo if slowmo > 0 else None,
            args=launch_args,
        )

        context_kwargs: Dict[str, Any] = {
            "ignore_https_errors": not verify,
            "user_agent": ua,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if vp_w > 0 and vp_h > 0:
            context_kwargs["viewport"] = {"width": vp_w, "height": vp_h}
        context = browser.new_context(**context_kwargs)
        # Stealth：隐藏 webdriver 痕迹，补全常见指纹
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            // 模拟常见的 window.chrome 对象
            window.chrome = { runtime: {} };
            // 语言与插件
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4] });
            // WebGL 指纹（简单覆盖）
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
              if (parameter === 37445) return 'Intel Inc.'; // UNMASKED_VENDOR_WEBGL
              if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
              return getParameter.call(this, parameter);
            };
            """
        )
        page = context.new_page()

        # 1) 打开首页（触发 JS 挑战）
        log(f"打开首页: {base}")
        page.goto(base, wait_until="domcontentloaded", timeout=timeout * 1000)

        # 2) 进入登录页
        goto_ok = False
        for path in login_paths:
            full = path if str(path).startswith("http") else f"{base}{path}"
            try:
                log(f"尝试打开登录页: {full}")
                page.goto(full, wait_until="domcontentloaded", timeout=timeout * 1000)
                goto_ok = True
                break
            except Exception as e:
                log(f"登录页尝试失败: {e}")
        if not goto_ok:
            raise RuntimeError("无法打开登录页，请在配置中设置 login_url 试试")

        # 3) 关闭公告；切换到邮箱/用户名登录
        close_announcement(page)
        switch_to_email_login(page)

        # 4) 等待表单渲染（兼容 SPA）
        try:
            page.wait_for_selector(
                'input[type="password"], input[placeholder*="密码"], button:has-text("继续")',
                state="visible",
                timeout=timeout * 1000,
            )
            # 可访问性标题存在性检查（若存在，进一步确认页面状态）
            try:
                page.get_by_role("heading", name="登 录").first.wait_for(timeout=1500)
            except Exception:
                pass
        except Exception:
            pass

        # 5) 填写与提交
        fill_credentials(
            page,
            selectors.get("user") or "",
            selectors.get("pass") or "",
            timeout * 1000,
            selectors.get("user_selector"),
            selectors.get("pass_selector"),
        )
        click_submit(page, timeout * 1000, selectors.get("submit_selector"))

        # 等待登录结果：优先等 URL 进入用户页，或等待设置 session Cookie
        # 尽力等待，不抛错阻断后续逻辑
        try:
            # 先短等 URL 变化（常见 /user、/console、/dashboard）
            import re as _re  # 局部导入避免顶层依赖
            page.wait_for_url(_re.compile(r"/(user|console|dashboard)"), timeout=min(10000, timeout * 1000))
        except Exception:
            try:
                # 退而求其次：等待页面脚本设置 session Cookie
                page.wait_for_function("() => document.cookie.includes('session=')", timeout=min(10000, timeout * 1000))
            except Exception:
                pass

        # 6) 调用签到接口（优先 context.request；失败或未判定成功则回退页面内 fetch）
        headers = make_default_headers(base, ua, referer=f"{base}/user", new_api_user_val=selectors.get("new_api_user"))
        try:
            resp = context.request.post(sign_url, headers=headers, timeout=timeout * 1000)
            status = int(resp.status)
            text = str(resp.text() or "")
        except Exception as e:
            status, text = -1, f"context.request 异常: {e}"

        # 若 context.request 非 2xx 或未判定成功，使用页面内 fetch 回退一次
        ok_req, _ = detect_success(status if isinstance(status, int) else 0, text or "")
        if not ok_req:
            try:
                # 访问用户页，确保会话与 Referer（轻量探测，失败亦可忽略）
                try:
                    page.goto(f"{base}/user", wait_until="domcontentloaded", timeout=min(8000, timeout * 1000))
                except Exception:
                    pass
                js = (
                    "async ({ url, headers }) => {\n"
                    "  const resp = await fetch(url, { method: 'POST', headers, credentials: 'same-origin' });\n"
                    "  const t = await resp.text();\n"
                    "  return [resp.status, t];\n"
                    "}"
                )
                status2, text2 = page.evaluate(js, {"url": sign_url, "headers": headers})
                status2 = int(status2)
                text2 = str(text2 or "")
                status, text = status2, text2
            except Exception as ee:
                # 失败时保存页面快照以便诊断
                try:
                    snap = f"anyrouter_headless_error_{int(time.time())}.png"
                    page.screenshot(path=snap, full_page=True)
                    log(f"已保存错误截图: {snap}")
                except Exception:
                    pass
                raise RuntimeError(f"签到失败并回退 fetch 异常: {ee}；原响应: {status} {text}")
        # 关闭浏览器上下文并返回
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        return status, text


def main() -> int:
    parser = argparse.ArgumentParser(description="AnyRouter 浏览器签到（支持可视化/静默）")
    parser.add_argument("--config", default="scripts/test/checkin_anyrouter_login_test.config.json", help="配置文件路径(JSON)")
    parser.add_argument("--headed", action="store_true", help="以可视化模式运行浏览器（非无头）")
    parser.add_argument("--headless", action="store_true", help="始终无头运行（优先级高于 --headed）")
    parser.add_argument("--slowmo", type=int, default=0, help="慢动作毫秒（演示观察用）")
    parser.add_argument("--viewport", default=None, help="视窗大小，如 1280x900")
    parser.add_argument("--log-success-bytes", type=int, default=None, help="成功时打印响应预览的最大字符数；默认不打印")

    args = parser.parse_args()

    if not os.path.exists(args.config):
        log(f"[错误] 未找到配置文件：{args.config}")
        return 2

    try:
        cfg = load_config(args.config)
    except Exception as e:
        log(f"[错误] 读取配置失败：{e}")
        return 2

    user = str(cfg.get("user") or "").strip()
    password = str(cfg.get("pass") or "").strip()
    if not user or not password:
        log("[错误] 缺少 user/pass（可在配置或命令行提供）")
        return 2

    base = str(cfg.get("base") or "https://anyrouter.top").rstrip("/")
    login_url = str(cfg.get("login_url") or "").strip() or None
    sign_path = str(cfg.get("sign_path") or "/api/user/sign_in")
    sign_url = sign_path if sign_path.startswith("http") else f"{base}{sign_path}"

    timeout = int(cfg.get("timeout") or 15)
    verify = parse_bool(cfg.get("verify"), True)
    ua = str(cfg.get("ua") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ))
    headers_cfg = str(cfg.get("headers") or "")
    new_api_user_cfg = cfg.get("new_api_user")
    if new_api_user_cfg is None:
        new_api_user_val: Optional[str] = "1"
    else:
        v = str(new_api_user_cfg).strip()
        if v.lower() in {"", "0", "false", "no", "off"}:
            new_api_user_val = None
        else:
            new_api_user_val = v

    headed_cfg = cfg.get("headed")
    slowmo_cfg = cfg.get("slowmo")
    viewport_cfg = cfg.get("viewport")
    headed = False
    if args.headless:
        headed = False
    elif args.headed:
        headed = True
    elif headed_cfg is not None:
        headed = parse_bool(headed_cfg, False)
    slowmo = int(args.slowmo if args.slowmo is not None else (slowmo_cfg or 0))
    viewport = args.viewport if args.viewport is not None else (viewport_cfg or None)

    log_bytes = int(cfg.get("log_bytes") or 500)
    log_success_bytes = int(args.log_success_bytes if args.log_success_bytes is not None else (cfg.get("log_success_bytes") or 0))

    selectors: Dict[str, Optional[str]] = {
        "user": user,
        "pass": password,
        "user_selector": (str(cfg.get("user_selector") or "").strip() or None),
        "pass_selector": (str(cfg.get("pass_selector") or "").strip() or None),
        "submit_selector": (str(cfg.get("submit_selector") or "").strip() or None),
        "new_api_user": new_api_user_val,
    }

    # 附加请求头传入环境变量（与仓库其他脚本兼容）
    if headers_cfg:
        os.environ["ANYROUTER_HEADERS"] = headers_cfg

    log("=== 运行参数 ===")
    log(f"Base: {base}")
    log(f"Login URL: {login_url or '(自动)'}  Sign: {sign_url}")
    log(f"UA: {ua[:50]}{'...' if len(ua) > 50 else ''}")
    log(f"Headed: {headed}, SlowMo: {slowmo}ms, Viewport: {viewport or '(默认)'}")

    try:
        status, text = browser_login_and_sign(
            base=base,
            login_url=login_url,
            sign_url=sign_url,
            timeout=timeout,
            verify=verify,
            ua=ua,
            headed=headed,
            slowmo=slowmo,
            viewport=viewport,
            selectors=selectors,
            log_success_bytes=log_success_bytes,
        )
    except Exception as e:
        log(f"[错误] 流程失败：{e}")
        return 1

    ok, reason = detect_success(status, text)
    log(f"签到响应: {status}, 判定: {'成功' if ok else '失败'}，原因: {reason}")
    if ok and log_success_bytes > 0:
        prev = preview_response(text or "", log_success_bytes)
        if prev:
            log("成功响应内容预览:\n" + prev)
    if not ok and log_bytes > 0:
        prev = preview_response(text or "", log_bytes)
        if prev:
            log("响应内容预览:\n" + prev)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("用户中断")
        sys.exit(130)
