#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Billing Kerit 自动续订脚本
支持代理模式绕过 IP 限制
"""

import os
import sys
import time
import random
import subprocess
import asyncio
import aiohttp
import base64
import platform
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote, quote
from pathlib import Path

try:
    from seleniumbase import SB
    SELENIUMBASE_AVAILABLE = True
except ImportError:
    SELENIUMBASE_AVAILABLE = False
    print("[ERROR] seleniumbase 未安装")

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

try:
    from pyvirtualdisplay import Display
    PYVIRTUALDISPLAY_AVAILABLE = True
except ImportError:
    PYVIRTUALDISPLAY_AVAILABLE = False

# ==================== 配置 ====================
BASE_URL = "https://billing.kerit.cloud"
SESSION_URL = f"{BASE_URL}/session"
FREE_PANEL_URL = f"{BASE_URL}/free_panel"
DOMAIN = "billing.kerit.cloud"
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CN_TZ = timezone(timedelta(hours=8))


# ==================== 工具函数 ====================

def cn_now() -> datetime:
    return datetime.now(CN_TZ)


def cn_time_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return cn_now().strftime(fmt)


def log(level: str, msg: str):
    timestamp = cn_time_str()
    print(f"[{timestamp}] [{level}] {msg}")


def env_or_throw(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def env_or_default(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def screenshot_path(name: str) -> str:
    return str(OUTPUT_DIR / f"{name}.png")


def mask(s: str, show: int = 2) -> str:
    """脱敏显示"""
    if not s:
        return "***"
    s = str(s)
    if len(s) <= show * 2:
        return s[:show] + "***"
    return s[:show] + "***" + s[-show:]


def mask_ip(ip: str) -> str:
    """IP 地址脱敏"""
    if not ip:
        return "***"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.***.***.{parts[3]}"
    return mask(ip)


def is_linux():
    return platform.system().lower() == "linux"


def setup_display():
    """设置虚拟显示"""
    if is_linux() and not os.environ.get("DISPLAY"):
        if PYVIRTUALDISPLAY_AVAILABLE:
            try:
                d = Display(visible=False, size=(1920, 1080))
                d.start()
                os.environ["DISPLAY"] = d.new_display_var
                log("INFO", "虚拟显示已启动")
                return d
            except Exception as e:
                log("ERROR", f"虚拟显示失败: {e}")
        else:
            log("WARN", "pyvirtualdisplay 未安装")
    return None


def parse_cookie_string(cookie_str: str):
    """解析 Cookie 字符串，去重处理"""
    if not cookie_str:
        return []
    
    cookies_dict = {}  # 使用字典去重，后面的会覆盖前面的
    
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        
        eq_index = item.index("=")
        name = item[:eq_index].strip()
        value = item[eq_index + 1:].strip()
        
        try:
            value = unquote(value)
        except:
            pass
        
        # 使用字典去重
        cookies_dict[name] = value
    
    cookies = [{"name": k, "value": v} for k, v in cookies_dict.items()]
    cookie_names = [c["name"] for c in cookies]
    log("INFO", f"解析到 {len(cookies)} 个 Cookie: {', '.join(cookie_names)}")
    return cookies


def test_proxy(proxy_url: str) -> bool:
    """测试代理连接（不显示完整 IP）"""
    if not proxy_url:
        return False
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=10)
        ip = resp.text.strip()
        log("INFO", f"代理 IP: {mask_ip(ip)}")  # 脱敏显示
        return True
    except Exception as e:
        log("WARN", f"代理测试失败: {e}")
        return False


# ==================== Telegram 通知 ====================

async def tg_notify(message):
    token = env_or_default("TG_BOT_TOKEN")
    chat_id = env_or_default("TG_CHAT_ID")
    if not token or not chat_id:
        log("WARN", "Telegram 配置不完整")
        return
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
            log("INFO", "Telegram 通知已发送")
        except Exception as e:
            log("WARN", f"Telegram 发送失败: {e}")


async def tg_notify_photo(photo_path, caption=""):
    token = env_or_default("TG_BOT_TOKEN")
    chat_id = env_or_default("TG_CHAT_ID")
    if not token or not chat_id or not Path(photo_path).exists():
        return
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=Path(photo_path).name)
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
                await session.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data)
            log("INFO", "Telegram 图片已发送")
        except Exception as e:
            log("WARN", f"Telegram 图片发送失败: {e}")


def sync_tg_notify(message):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path, caption=""):
    asyncio.run(tg_notify_photo(photo_path, caption))


def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_file: str = None):
    status = "✅ 成功" if ok else "❌ 失败"
    text_lines = [
        f"📋 <b>Billing Kerit 自动续订</b>",
        f"",
        f"状态: {status}",
        f"阶段: {stage}",
    ]
    if msg:
        text_lines.append(f"")
        text_lines.append(msg)
    text_lines.append(f"")
    text_lines.append(f"⏰ {cn_time_str()}")
    
    caption = "\n".join(text_lines)
    
    if screenshot_file and Path(screenshot_file).exists():
        sync_tg_notify_photo(screenshot_file, caption)
    else:
        sync_tg_notify(caption)


# ==================== GitHub Secret 更新 ====================

def encrypt_secret(public_key, secret_value):
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret_async(secret_name, secret_value):
    repo_token = env_or_default("REPO_TOKEN")
    repository = env_or_default("GITHUB_REPOSITORY")
    
    if not repo_token or not repository or not NACL_AVAILABLE:
        return False
    
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                pk_data = await resp.json()
            
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            
            async with session.put(secret_url, headers=headers, json={
                "encrypted_value": encrypted_value,
                "key_id": pk_data["key_id"]
            }) as resp:
                return resp.status in (201, 204)
        except Exception as e:
            log("ERROR", f"更新 GitHub Secret 失败: {e}")
            return False


def update_github_secret(secret_name: str, secret_value: str) -> bool:
    result = asyncio.run(update_github_secret_async(secret_name, secret_value))
    if result:
        log("INFO", "GitHub Secret 已更新")
    return result


def save_cookies_for_update(sb) -> str:
    try:
        cookies = sb.get_cookies()
        important_names = ["session_id", "cf_clearance"]
        
        # 使用字典去重
        cookie_dict = {}
        for c in cookies:
            name = c.get("name", "")
            if name in important_names:
                cookie_dict[name] = c.get("value", "")
        
        if not cookie_dict:
            log("WARN", "未找到关键 Cookie")
            return ""
        
        cookie_string = "; ".join([f"{k}={quote(str(v), safe='')}" for k, v in cookie_dict.items()])
        
        cookie_file = OUTPUT_DIR / "new_cookies.txt"
        with open(cookie_file, "w") as f:
            f.write(cookie_string)
        log("INFO", f"新 Cookie 已保存 ({len(cookie_dict)} 个)")
        
        return cookie_string
    except Exception as e:
        log("ERROR", f"保存 Cookie 失败: {e}")
        return ""


# ==================== Turnstile 处理 ====================

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return 'no turnstile input';

    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }

    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
            iframe.style.visibility = 'visible';
            iframe.style.opacity = '1';
        }
    });

    return 'done';
})();
"""


def check_turnstile_exists(sb):
    try:
        return sb.execute_script("""
            return document.querySelector('input[name="cf-turnstile-response"]') !== null;
        """)
    except:
        return False


def check_turnstile_solved(sb):
    try:
        return sb.execute_script("""
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            return input && input.value && input.value.length > 20;
        """)
    except:
        return False


def get_turnstile_checkbox_coords(sb):
    try:
        coords = sb.execute_script("""
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('cloudflare') || src.includes('turnstile')) {
                    var rect = iframes[i].getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            x: rect.x, y: rect.y,
                            width: rect.width, height: rect.height,
                            click_x: Math.round(rect.x + 30),
                            click_y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                }
            }
            var container = document.getElementById('turnstile-container');
            if (container) {
                var rect = container.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    return {
                        x: rect.x, y: rect.y,
                        width: rect.width, height: rect.height,
                        click_x: Math.round(rect.x + 30),
                        click_y: Math.round(rect.y + rect.height / 2)
                    };
                }
            }
            return null;
        """)
        return coords
    except:
        return None


def activate_browser_window():
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True, text=True, timeout=3
        )
        window_ids = result.stdout.strip().split('\n')
        if window_ids and window_ids[0]:
            subprocess.run(
                ["xdotool", "windowactivate", window_ids[0]],
                timeout=2, stderr=subprocess.DEVNULL
            )
            time.sleep(0.2)
            return True
    except:
        pass
    return False


def xdotool_click(x, y):
    x, y = int(x), int(y)
    activate_browser_window()
    try:
        subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=2, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
        return True
    except:
        pass
    try:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")
        return True
    except:
        return False


def click_turnstile_checkbox(sb):
    coords = get_turnstile_checkbox_coords(sb)
    if not coords:
        log("WARN", "无法获取 Turnstile 坐标")
        return False

    log("INFO", f"Turnstile 位置: ({coords['x']:.0f}, {coords['y']:.0f}) {coords['width']:.0f}x{coords['height']:.0f}")

    try:
        window_info = sb.execute_script("""
            return {
                screenX: window.screenX || 0,
                screenY: window.screenY || 0,
                outerHeight: window.outerHeight,
                innerHeight: window.innerHeight
            };
        """)
        chrome_bar_height = window_info["outerHeight"] - window_info["innerHeight"]
        abs_x = coords["click_x"] + window_info["screenX"]
        abs_y = coords["click_y"] + window_info["screenY"] + chrome_bar_height
        log("INFO", f"点击坐标: ({abs_x:.0f}, {abs_y:.0f})")
        return xdotool_click(abs_x, abs_y)
    except Exception as e:
        log("ERROR", f"坐标计算失败: {e}")
        return False


def handle_turnstile(sb, max_attempts=6):
    log("INFO", "开始处理 Turnstile 验证...")
    
    for _ in range(10):
        if check_turnstile_exists(sb):
            log("INFO", "检测到 Turnstile")
            break
        time.sleep(1)
    else:
        log("WARN", "未检测到 Turnstile")
        return False
    
    for _ in range(3):
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.5)
    
    for attempt in range(max_attempts):
        log("INFO", f"Turnstile 尝试 {attempt + 1}/{max_attempts}")
        
        if check_turnstile_solved(sb):
            log("INFO", "✅ Turnstile 已通过!")
            return True
        
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
        click_turnstile_checkbox(sb)
        
        for _ in range(10):
            time.sleep(0.5)
            if check_turnstile_solved(sb):
                log("INFO", "✅ Turnstile 已通过!")
                return True
    
    return check_turnstile_solved(sb)


def check_renew_button_enabled(sb):
    try:
        return sb.execute_script("""
            var btn = document.getElementById('renewBtn');
            if (!btn) return false;
            return !btn.disabled && !btn.hasAttribute('disabled');
        """)
    except:
        return False


def check_renewal_result(sb):
    try:
        page_text = sb.get_page_source()
        if "success" in page_text.lower() or "renewed" in page_text.lower():
            return "success"
        if "Cannot exceed" in page_text or "limit" in page_text.lower():
            return "limit_reached"
        return None
    except:
        return None


def check_access_blocked(sb) -> bool:
    """检查是否被 IP 限制"""
    try:
        page_source = sb.get_page_source()
        blocked_keywords = [
            "Access Restricted",
            "Access Blocked",
            "unusual network activity",
            "VPN or Proxy Detected",
            "datacenter IPs"
        ]
        for keyword in blocked_keywords:
            if keyword in page_source:
                return True
        
        current_url = sb.get_current_url()
        if "/error" in current_url:
            return True
        
        return False
    except:
        return False


def check_redirect_loop(sb) -> bool:
    """检查是否发生重定向循环"""
    try:
        page_source = sb.get_page_source()
        if "ERR_TOO_MANY_REDIRECTS" in page_source or "redirected you too many times" in page_source:
            return True
        return False
    except:
        return False


def clear_cookies_and_retry(sb):
    """清除 Cookie 并重新获取 cf_clearance"""
    log("INFO", "🧹 清除 Cookie，准备重新获取...")
    try:
        sb.delete_all_cookies()
        time.sleep(1)
        return True
    except Exception as e:
        log("ERROR", f"清除 Cookie 失败: {e}")
        return False


# ==================== 主逻辑 ====================

def main():
    log("INFO", "=" * 50)
    log("INFO", "🚀 Billing Kerit 自动续订脚本启动")
    log("INFO", "=" * 50)
    
    if not SELENIUMBASE_AVAILABLE:
        notify_telegram(False, "初始化失败", "seleniumbase 未安装")
        sys.exit(1)
    
    # 检查代理配置
    proxy_socks5 = env_or_default("PROXY_SOCKS5")
    proxy_http = env_or_default("PROXY_HTTP")
    
    if proxy_socks5:
        log("INFO", f"🌐 使用代理: {mask(proxy_socks5)}")
        if test_proxy(proxy_socks5):
            log("INFO", "✅ 代理连接正常")
        else:
            log("WARN", "⚠️ 代理测试失败，继续尝试...")
    else:
        log("INFO", "⚠️ 未配置代理，直连模式（可能被 IP 限制）")
    
    try:
        preset_cookies = env_or_throw("BILLING_KERIT_COOKIES")
    except ValueError as e:
        log("ERROR", str(e))
        notify_telegram(False, "初始化失败", "Cookie 环境变量未设置")
        sys.exit(1)
    
    cookies = parse_cookie_string(preset_cookies)
    if not cookies:
        log("ERROR", "Cookie 解析失败")
        notify_telegram(False, "初始化失败", "Cookie 解析失败")
        sys.exit(1)
    
    # 设置虚拟显示
    display = setup_display()
    
    final_screenshot = None
    renewal_count = "未知"
    status_text = "未知"
    
    log("INFO", "🌐 启动浏览器...")
    
    try:
        # 构建浏览器选项
        sb_options = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headed": not is_linux(),
            "chromium_arg": "--disable-dev-shm-usage,--no-sandbox,--disable-gpu"
        }
        
        # 添加代理
        if proxy_socks5:
            sb_options["proxy"] = proxy_socks5
            log("INFO", "浏览器将使用代理")
        
        with SB(**sb_options) as sb:
            log("INFO", "浏览器已启动")
            
            try:
                # 1. 先访问网站（不带旧 Cookie），让 CF 生成新的 cf_clearance
                log("INFO", "🌐 首次访问网站，获取 Cloudflare 验证...")
                sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=8)
                time.sleep(5)
                
                # 检查是否被阻止
                if check_access_blocked(sb):
                    sp_blocked = screenshot_path("00-access-blocked")
                    sb.save_screenshot(sp_blocked)
                    log("ERROR", "❌ 访问被阻止 - IP 被限制")
                    notify_telegram(False, "访问被阻止", "IP 被限制，请更换代理节点", sp_blocked)
                    sys.exit(1)
                
                # 尝试通过 Cloudflare 验证
                try:
                    sb.uc_gui_click_captcha()
                    time.sleep(3)
                except:
                    pass
                
                sp_cf = screenshot_path("00-cf-challenge")
                sb.save_screenshot(sp_cf)
                
                # 等待 CF 验证完成
                for i in range(30):
                    current_url = sb.get_current_url()
                    if "/session" in current_url or "/login" in current_url or "/free_panel" in current_url:
                        log("INFO", "✅ Cloudflare 验证通过")
                        break
                    if check_access_blocked(sb):
                        sp_blocked = screenshot_path("00-blocked")
                        sb.save_screenshot(sp_blocked)
                        log("ERROR", "❌ 访问被阻止")
                        notify_telegram(False, "访问被阻止", "IP 被限制", sp_blocked)
                        sys.exit(1)
                    time.sleep(1)
                
                # 2. 现在注入 session_id Cookie（只要 session_id，不要旧的 cf_clearance）
                log("INFO", "🍪 注入 session_id Cookie...")
                
                for c in cookies:
                    if c["name"] == "session_id":  # 只注入 session_id
                        sb.add_cookie({
                            "name": c["name"],
                            "value": c["value"],
                            "domain": DOMAIN,
                            "path": "/"
                        })
                        log("INFO", f"已注入 Cookie: {c['name']}")
                
                # 3. 访问 session 页面
                log("INFO", f"🔗 访问 {SESSION_URL}...")
                sb.uc_open_with_reconnect(SESSION_URL, reconnect_time=8)
                time.sleep(5)
                
                current_url = sb.get_current_url()
                log("INFO", f"当前 URL: {current_url}")
                
                # 检查重定向循环
                if check_redirect_loop(sb):
                    sp_redirect = screenshot_path("01-redirect-loop")
                    sb.save_screenshot(sp_redirect)
                    log("ERROR", "❌ 重定向循环 - Cookie 可能已失效")
                    notify_telegram(False, "Cookie 失效", "发生重定向循环，请更新 session_id Cookie", sp_redirect)
                    sys.exit(1)
                
                # 检查是否被阻止
                if check_access_blocked(sb):
                    sp_blocked = screenshot_path("01-access-blocked")
                    sb.save_screenshot(sp_blocked)
                    log("ERROR", "❌ 访问被阻止 - IP 被限制")
                    notify_telegram(False, "访问被阻止", "IP 被限制", sp_blocked)
                    sys.exit(1)
                
                sp_home = screenshot_path("01-homepage")
                sb.save_screenshot(sp_home)
                final_screenshot = sp_home
                
                # 检查登录状态
                if "/login" in current_url or "/auth" in current_url:
                    log("ERROR", "❌ session_id Cookie 已失效，需要重新登录")
                    notify_telegram(False, "登录检查", "session_id Cookie 已失效，请更新 Cookie", sp_home)
                    sys.exit(1)
                
                log("INFO", "✅ Cookie 有效，已登录")
                
                # 4. 进入 Free Plans 页面
                log("INFO", "🎁 进入 Free Plans 页面...")
                sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=8)
                time.sleep(5)
                
                current_url = sb.get_current_url()
                log("INFO", f"当前 URL: {current_url}")
                
                # 检查重定向循环
                if check_redirect_loop(sb):
                    sp_redirect = screenshot_path("02-redirect-loop")
                    sb.save_screenshot(sp_redirect)
                    log("ERROR", "❌ 重定向循环")
                    notify_telegram(False, "重定向循环", "请更新 Cookie", sp_redirect)
                    sys.exit(1)
                
                # 检查是否被阻止
                if check_access_blocked(sb):
                    sp_blocked = screenshot_path("02-access-blocked")
                    sb.save_screenshot(sp_blocked)
                    log("ERROR", "❌ 访问被阻止 - IP 被限制")
                    notify_telegram(False, "访问被阻止", "IP 被限制", sp_blocked)
                    sys.exit(1)
                
                # 检查是否成功进入 free_panel
                if "/free_panel" not in current_url:
                    log("WARN", f"未能进入 free_panel 页面，当前: {current_url}")
                    # 再试一次
                    time.sleep(2)
                    sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=8)
                    time.sleep(5)
                    current_url = sb.get_current_url()
                
                sp_free = screenshot_path("02-free-plans")
                sb.save_screenshot(sp_free)
                final_screenshot = sp_free
                
                # 5. 获取续订信息
                log("INFO", "🔍 检查续订状态...")
                
                try:
                    renewal_count = sb.execute_script("""
                        var el = document.getElementById('renewal-count');
                        return el ? el.textContent : '未知';
                    """) or "未知"
                    log("INFO", f"本周已续订次数: {renewal_count}/7")
                except:
                    pass
                
                try:
                    status_text = sb.execute_script("""
                        var el = document.getElementById('renewal-status-text');
                        return el ? el.textContent : '未知';
                    """) or "未知"
                    log("INFO", f"续订状态: {status_text}")
                except:
                    pass
                
                # 6. 检查续订按钮
                renew_btn_disabled = sb.execute_script("""
                    var btn = document.getElementById('renewServerBtn');
                    if (!btn) return true;
                    return btn.disabled || btn.hasAttribute('disabled');
                """)
                
                log("INFO", f"续订按钮 disabled: {renew_btn_disabled}")
                
                if renew_btn_disabled:
                    log("INFO", "⏭️ 续订按钮已禁用，跳过续订")
                    result_message = f"续订次数: {renewal_count}/7\n状态: {status_text}\n\n⏭️ 已达到续订限制或未到续订时间"
                    
                    new_cookie_str = save_cookies_for_update(sb)
                    if new_cookie_str:
                        update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                    
                    notify_telegram(True, "检查完成", result_message, final_screenshot)
                else:
                    # 7. 开始续订流程
                    log("INFO", "✨ 续订按钮可用，开始续订流程...")
                    
                    sb.execute_script("""
                        var btn = document.getElementById('renewServerBtn');
                        if (btn) btn.click();
                    """)
                    log("INFO", "已点击续订按钮，等待模态框...")
                    time.sleep(2)
                    
                    sp_modal = screenshot_path("03-renewal-modal")
                    sb.save_screenshot(sp_modal)
                    final_screenshot = sp_modal
                    
                    modal_visible = sb.execute_script("""
                        var modal = document.getElementById('renewalModal');
                        if (!modal) return false;
                        var style = window.getComputedStyle(modal);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    """)
                    
                    if not modal_visible:
                        log("WARN", "模态框可能未打开，继续尝试...")
                    else:
                        log("INFO", "📋 续订模态框已打开")
                    
                    # 8. 处理 Turnstile 验证
                    log("INFO", "⏳ 处理 Turnstile 验证...")
                    
                    # 尝试 SeleniumBase 内置方法
                    try:
                        sb.uc_gui_click_captcha()
                        time.sleep(3)
                    except Exception as e:
                        log("WARN", f"uc_gui_click_captcha 失败: {e}")
                    
                    turnstile_passed = handle_turnstile(sb)
                    
                    if not turnstile_passed:
                        log("WARN", "⚠️ Turnstile 验证可能未通过，继续尝试...")
                    else:
                        log("INFO", "✅ Turnstile 验证通过")
                    
                    sp_after_turnstile = screenshot_path("04-after-turnstile")
                    sb.save_screenshot(sp_after_turnstile)
                    final_screenshot = sp_after_turnstile
                    
                    # 9. 点击广告横幅（在新标签页中打开，不影响当前页面）
                    log("INFO", "🖱️ 点击广告横幅...")
                    
                    # 记录当前窗口
                    main_window = sb.driver.current_window_handle
                    original_windows = set(sb.driver.window_handles)
                    
                    # 点击广告
                    sb.execute_script("""
                        var adBanner = document.getElementById('adBanner');
                        if (adBanner) {
                            var parent = adBanner.closest('[onclick]');
                            if (parent) {
                                parent.click();
                            } else {
                                adBanner.click();
                            }
                        }
                    """)
                    
                    time.sleep(3)
                    
                    # 10. 处理广告新窗口（关闭它，回到主窗口）
                    current_windows = set(sb.driver.window_handles)
                    new_windows = current_windows - original_windows
                    
                    if new_windows:
                        log("INFO", f"检测到 {len(new_windows)} 个新窗口，正在关闭...")
                        
                        for new_win in new_windows:
                            try:
                                sb.driver.switch_to.window(new_win)
                                time.sleep(0.5)
                                sp_ad = screenshot_path("05-ad-window")
                                sb.save_screenshot(sp_ad)
                                sb.driver.close()
                                log("INFO", "已关闭广告窗口")
                            except Exception as e:
                                log("WARN", f"关闭窗口失败: {e}")
                        
                        # 切回主窗口
                        sb.driver.switch_to.window(main_window)
                        log("INFO", "已切回主窗口")
                    
                    time.sleep(2)
                    
                    # 确认还在续订页面
                    current_url = sb.get_current_url()
                    log("INFO", f"当前 URL: {current_url}")
                    
                    if "/free_panel" not in current_url:
                        log("WARN", "页面发生跳转，重新打开续订页面...")
                        sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=5)
                        time.sleep(3)
                        
                        # 重新点击续订按钮
                        sb.execute_script("""
                            var btn = document.getElementById('renewServerBtn');
                            if (btn) btn.click();
                        """)
                        time.sleep(2)
                        
                        # 重新处理 Turnstile
                        try:
                            sb.uc_gui_click_captcha()
                            time.sleep(2)
                        except:
                            pass
                        handle_turnstile(sb, max_attempts=3)
                    
                    # 11. 点击续订按钮
                    log("INFO", "🔘 点击最终续订按钮...")
                    
                    sp_before_renew = screenshot_path("06-before-renew")
                    sb.save_screenshot(sp_before_renew)
                    final_screenshot = sp_before_renew
                    
                    # 检查 renewBtn 是否可点击
                    renew_btn_ready = sb.execute_script("""
                        var btn = document.getElementById('renewBtn');
                        if (!btn) return {exists: false};
                        return {
                            exists: true,
                            disabled: btn.disabled,
                            visible: btn.offsetParent !== null
                        };
                    """)
                    
                    log("INFO", f"续订按钮状态: {renew_btn_ready}")
                    
                    if renew_btn_ready and renew_btn_ready.get("exists") and not renew_btn_ready.get("disabled"):
                        sb.execute_script("""
                            var btn = document.getElementById('renewBtn');
                            if (btn && !btn.disabled) {
                                btn.click();
                            }
                        """)
                        log("INFO", "已点击 renewBtn")
                    else:
                        log("WARN", "renewBtn 不可用，尝试其他方式...")
                        # 尝试直接提交
                        sb.execute_script("""
                            var form = document.querySelector('form');
                            if (form) form.submit();
                        """)
                    
                    time.sleep(5)
                    
                    # 最终截图
                    sp_final = screenshot_path("07-renewal-complete")
                    sb.save_screenshot(sp_final)
                    final_screenshot = sp_final
                    
                    # 12. 检查结果
                    result = check_renewal_result(sb)
                    log("INFO", f"续订结果检查: {result}")
                    
                    # 重新获取续订信息
                    try:
                        sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=5)
                        time.sleep(3)
                        
                        new_renewal_count = sb.execute_script("""
                            var el = document.getElementById('renewal-count');
                            return el ? el.textContent : '未知';
                        """) or "未知"
                        
                        new_status_text = sb.execute_script("""
                            var el = document.getElementById('renewal-status-text');
                            return el ? el.textContent : '未知';
                        """) or "未知"
                        
                        sp_result = screenshot_path("08-final-status")
                        sb.save_screenshot(sp_result)
                        final_screenshot = sp_result
                        
                        log("INFO", f"续订后次数: {new_renewal_count}/7")
                        log("INFO", f"续订后状态: {new_status_text}")
                        
                    except Exception as e:
                        log("WARN", f"获取续订后状态失败: {e}")
                        new_renewal_count = "未知"
                        new_status_text = "未知"
                    
                    # 13. 判断续订是否成功
                    if result == "limit_reached":
                        log("INFO", "⚠️ 已达到续订限制")
                        result_message = f"续订次数: {new_renewal_count}/7\n状态: {new_status_text}\n\n⚠️ 已达到每周续订限制"
                        notify_telegram(True, "续订完成", result_message, final_screenshot)
                    elif result == "success" or (renewal_count != "未知" and new_renewal_count != "未知" and renewal_count != new_renewal_count):
                        log("INFO", "🎉 续订成功!")
                        result_message = f"续订次数: {new_renewal_count}/7\n状态: {new_status_text}\n\n✅ 服务器续订成功！"
                        notify_telegram(True, "续订成功", result_message, final_screenshot)
                    else:
                        log("INFO", "续订操作已完成")
                        result_message = f"续订次数: {new_renewal_count}/7\n状态: {new_status_text}\n\n操作已完成，请检查续订状态"
                        notify_telegram(True, "操作完成", result_message, final_screenshot)
                    
                    # 14. 保存并更新 Cookie
                    log("INFO", "💾 保存 Cookie...")
                    new_cookie_str = save_cookies_for_update(sb)
                    if new_cookie_str:
                        update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                
                log("INFO", "✅ 脚本执行完成")
                
            except Exception as e:
                log("ERROR", f"💥 执行过程中发生异常: {e}")
                import traceback
                traceback.print_exc()
                
                try:
                    sp_error = screenshot_path("99-error")
                    sb.save_screenshot(sp_error)
                    final_screenshot = sp_error
                except:
                    pass
                
                try:
                    new_cookie_str = save_cookies_for_update(sb)
                    if new_cookie_str:
                        update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                except:
                    pass
                
                notify_telegram(
                    False,
                    "脚本异常",
                    str(e)[:200],
                    final_screenshot if final_screenshot and Path(final_screenshot).exists() else None
                )
                sys.exit(1)
    
    except Exception as e:
        log("ERROR", f"💥 浏览器启动失败: {e}")
        import traceback
        traceback.print_exc()
        notify_telegram(False, "启动失败", f"浏览器启动失败: {str(e)[:100]}")
        sys.exit(1)
    
    finally:
        if display:
            try:
                display.stop()
            except:
                pass
    
    log("INFO", "🔒 浏览器已关闭")


if __name__ == "__main__":
    main()
