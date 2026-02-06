#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v9 - 简化版
流程: 点击侧栏按钮 → Turnstile 通过后自动提交 → 检查结果
"""

import os
import time
import asyncio
import aiohttp
import base64
import random
import re
import subprocess
from datetime import datetime
from urllib.parse import unquote

from seleniumbase import SB

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

BASE_URL = "https://hub.weirdhost.xyz/server/"
DOMAIN = "hub.weirdhost.xyz"


# ============================================================
# 工具函数
# ============================================================
def parse_weirdhost_cookie(cookie_str):
    if not cookie_str:
        return (None, None)
    cookie_str = cookie_str.strip()
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            return (parts[0].strip(), unquote(parts[1].strip()))
    return (None, None)


def build_server_url(server_id):
    if not server_id:
        return None
    server_id = server_id.strip()
    return server_id if server_id.startswith("http") else f"{BASE_URL}{server_id}"


def calculate_remaining_time(expiry_str):
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                diff = expiry_dt - datetime.now()
                if diff.total_seconds() < 0:
                    return "已过期"
                days = diff.days
                hours = diff.seconds // 3600
                parts = []
                if days > 0:
                    parts.append(f"{days}天")
                if hours > 0:
                    parts.append(f"{hours}小时")
                return " ".join(parts) if parts else "不到1小时"
            except ValueError:
                continue
        return "未知"
    except:
        return "未知"


def parse_expiry_to_datetime(expiry_str):
    if not expiry_str or expiry_str == "Unknown":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(expiry_str.strip(), fmt)
        except ValueError:
            continue
    return None


# ============================================================
# Telegram
# ============================================================
async def tg_notify(message):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
        except:
            pass


async def tg_notify_photo(photo_path, caption=""):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id or not os.path.exists(photo_path):
        return
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
                await session.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data)
        except:
            pass


def sync_tg_notify(message):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path, caption=""):
    asyncio.run(tg_notify_photo(photo_path, caption))


# ============================================================
# GitHub Secrets
# ============================================================
def encrypt_secret(public_key, secret_value):
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name, secret_value):
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
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
                "encrypted_value": encrypted_value, "key_id": pk_data["key_id"]
            }) as resp:
                return resp.status in (201, 204)
        except:
            return False


# ============================================================
# 页面解析
# ============================================================
def get_expiry_from_page(sb):
    try:
        page_text = sb.get_page_source()
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        return "Unknown"
    except:
        return "Unknown"


def is_logged_in(sb):
    try:
        url = sb.get_current_url()
        if "/login" in url or "/auth" in url:
            return False
        if get_expiry_from_page(sb) != "Unknown":
            return True
        return False
    except:
        return False


# ============================================================
# Turnstile 处理
# ============================================================

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return 'no turnstile';
    
    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    
    document.querySelectorAll('[class*="sc-fKFyDc"], [class*="nwOmR"]').forEach(function(c) {
        c.style.overflow = 'visible';
        c.style.width = '300px';
        c.style.minWidth = '300px';
    });
    
    return 'done';
})();
"""


def check_turnstile_exists(sb):
    try:
        return sb.execute_script(
            "return document.querySelector('input[name=\"cf-turnstile-response\"]') !== null;"
        )
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


def get_turnstile_coords(sb):
    try:
        return sb.execute_script("""
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
            return null;
        """)
    except:
        return None


def xdotool_click(x, y):
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))], 
                      check=True, timeout=5)
        time.sleep(0.1)
        subprocess.run(["xdotool", "click", "1"], check=True, timeout=5)
        return True
    except:
        return False


def click_turnstile(sb):
    """点击 Turnstile checkbox"""
    coords = get_turnstile_coords(sb)
    if not coords:
        print("[!] 无法获取 Turnstile 坐标")
        return False
    
    print(f"[*] Turnstile: ({coords['x']:.0f}, {coords['y']:.0f}) {coords['width']:.0f}x{coords['height']:.0f}")
    
    try:
        window_info = sb.execute_script("""
            return {
                screenX: window.screenX || 0,
                screenY: window.screenY || 0,
                outerHeight: window.outerHeight,
                innerHeight: window.innerHeight
            };
        """)
        
        chrome_bar = window_info["outerHeight"] - window_info["innerHeight"]
        abs_x = coords["click_x"] + window_info["screenX"]
        abs_y = coords["click_y"] + window_info["screenY"] + chrome_bar
        
        print(f"[*] 点击: ({abs_x:.0f}, {abs_y:.0f})")
        return xdotool_click(abs_x, abs_y)
    except:
        return False


def check_result_popup(sb):
    """检查结果弹窗 - 返回 'success' / 'cooldown' / None"""
    try:
        page = sb.get_page_source()
        
        # 成功
        if "successfully renew" in page.lower() or "성공" in page:
            if "Success" in page:
                return "success"
        
        # 冷却期
        cooldown_texts = [
            "아직 연장을 할수없어요",
            "아직 서버를 갱신할 수 없습니다",
            "남은 시간이 더 줄어들 때까지"
        ]
        for text in cooldown_texts:
            if text in page:
                return "cooldown"
        
        # Error 弹窗
        if sb.is_element_present("//span[contains(@class, 'title') and text()='Error']"):
            return "cooldown"
        
        return None
    except:
        return None


# ============================================================
# 主流程
# ============================================================

def handle_turnstile_and_wait(sb, timeout=60):
    """
    处理 Turnstile 并等待结果
    Turnstile 通过后会自动提交，只需等待结果弹窗
    """
    start = time.time()
    
    # 1. 等待 Turnstile 出现
    print("[*] 等待 Turnstile...")
    for _ in range(15):
        if check_turnstile_exists(sb):
            print("[+] Turnstile 已出现")
            break
        if check_result_popup(sb):
            return check_result_popup(sb)
        time.sleep(1)
    else:
        print("[!] 未检测到 Turnstile")
        return None
    
    # 2. 修复弹窗样式
    print("[*] 修复弹窗样式...")
    for _ in range(2):
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
    
    sb.save_screenshot("popup_fixed.png")
    
    # 3. 点击 Turnstile
    print("[*] 点击 Turnstile...")
    for attempt in range(5):
        print(f"  尝试 {attempt + 1}/5")
        
        if check_turnstile_solved(sb):
            print("[+] Turnstile 已通过!")
            break
        
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
        click_turnstile(sb)
        
        # 等待验证
        for _ in range(6):
            time.sleep(0.5)
            if check_turnstile_solved(sb):
                print("[+] Turnstile 已通过!")
                break
            # 检查是否已有结果
            result = check_result_popup(sb)
            if result:
                return result
        
        if check_turnstile_solved(sb):
            break
    
    # 4. 等待结果弹窗 (Turnstile 通过后自动提交)
    print("[*] 等待结果...")
    remaining = timeout - (time.time() - start)
    
    for _ in range(int(remaining)):
        result = check_result_popup(sb)
        if result:
            print(f"[+] 结果: {result}")
            sb.save_screenshot("result.png")
            return result
        time.sleep(1)
    
    print("[!] 等待超时")
    sb.save_screenshot("timeout.png")
    return "timeout"


def add_server_time():
    weirdhost_cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    weirdhost_id = os.environ.get("WEIRDHOST_ID", "").strip()

    cookie_name, cookie_value = parse_weirdhost_cookie(weirdhost_cookie)
    server_url = build_server_url(weirdhost_id)

    if not cookie_name or not cookie_value:
        sync_tg_notify("🎁 <b>Weirdhost</b>\n\n❌ WEIRDHOST_COOKIE 未设置")
        return
    if not server_url:
        sync_tg_notify("🎁 <b>Weirdhost</b>\n\n❌ WEIRDHOST_ID 未设置")
        return

    print("=" * 60)
    print("Weirdhost 自动续期 v9")
    print("=" * 60)
    print(f"[*] URL: {server_url}")
    print("=" * 60)

    original_expiry = "Unknown"

    try:
        with SB(uc=True, test=True, locale="ko", headless=False) as sb:
            print("\n[*] 浏览器已启动")

            # 步骤1: 设置 Cookie
            print("\n[步骤1] 设置 Cookie")
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
            time.sleep(2)
            sb.add_cookie({
                "name": cookie_name, "value": cookie_value,
                "domain": DOMAIN, "path": "/"
            })

            # 步骤2: 访问服务器页面
            print("\n[步骤2] 访问服务器页面")
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)

            if not is_logged_in(sb):
                sb.add_cookie({
                    "name": cookie_name, "value": cookie_value,
                    "domain": DOMAIN, "path": "/"
                })
                sb.uc_open_with_reconnect(server_url, reconnect_time=5)
                time.sleep(3)

            if not is_logged_in(sb):
                sb.save_screenshot("login_failed.png")
                sync_tg_notify_photo("login_failed.png",
                    "🎁 <b>Weirdhost</b>\n\n❌ Cookie 失效")
                return

            print("[+] 登录成功")
            original_expiry = get_expiry_from_page(sb)
            original_remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期: {original_expiry} ({original_remaining})")

            # 步骤3: 点击续期按钮
            print("\n[步骤3] 点击续期按钮")
            time.sleep(random.uniform(1, 2))

            btn_xpath = "//button[contains(., '시간추가')]"
            if not sb.is_element_present(btn_xpath):
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png",
                    f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到按钮\n📅 {original_expiry}")
                return

            sb.click(btn_xpath)
            print("[+] 已点击，等待弹窗...")
            time.sleep(3)

            # 步骤4: 处理 Turnstile
            print("\n[步骤4] 处理 Turnstile")
            result = handle_turnstile_and_wait(sb, timeout=60)
            print(f"[*] 结果: {result}")

            # 步骤5: 检查到期时间变化
            print("\n[步骤5] 检查结果")
            time.sleep(2)
            
            # 刷新页面
            sb.uc_open_with_reconnect(server_url, reconnect_time=3)
            time.sleep(3)
            
            new_expiry = get_expiry_from_page(sb)
            new_remaining = calculate_remaining_time(new_expiry)
            sb.save_screenshot("final.png")

            print(f"[*] 原: {original_expiry}")
            print(f"[*] 新: {new_expiry}")

            original_dt = parse_expiry_to_datetime(original_expiry)
            new_dt = parse_expiry_to_datetime(new_expiry)

            # 发送通知
            if result == "cooldown":
                msg = (f"🎁 <b>Weirdhost</b>\n\n"
                       f"ℹ️ 冷却期内\n"
                       f"📅 到期: {original_expiry}\n"
                       f"⏳ 剩余: {original_remaining}")
                sync_tg_notify_photo("popup_fixed.png", msg)

            elif original_dt and new_dt and new_dt > original_dt:
                diff_h = (new_dt - original_dt).total_seconds() / 3600
                msg = (f"🎁 <b>Weirdhost</b>\n\n"
                       f"✅ 续期成功!\n"
                       f"📅 到期: {new_expiry}\n"
                       f"⏳ 剩余: {new_remaining}\n"
                       f"📝 +{diff_h:.1f}h")
                print(f"\n[+] 成功! +{diff_h:.1f}h")
                sync_tg_notify(msg)

            elif original_dt and new_dt and new_dt == original_dt:
                msg = (f"🎁 <b>Weirdhost</b>\n\n"
                       f"⚠️ 时间未变化\n"
                       f"📅 到期: {original_expiry}\n"
                       f"⏳ 剩余: {original_remaining}\n"
                       f"📝 状态: {result or 'unknown'}")
                sync_tg_notify_photo("popup_fixed.png", msg)

            else:
                msg = (f"🎁 <b>Weirdhost</b>\n\n"
                       f"⚠️ 结果未知\n"
                       f"📅 原: {original_expiry}\n"
                       f"📅 新: {new_expiry}\n"
                       f"📝 状态: {result or 'unknown'}")
                sync_tg_notify_photo("popup_fixed.png", msg)

            # 更新 Cookie
            try:
                for cookie in sb.get_cookies():
                    if cookie.get("name", "").startswith("remember_web"):
                        new_val = cookie.get("value", "")
                        if new_val and new_val != cookie_value:
                            new_str = f"{cookie['name']}={new_val}"
                            if asyncio.run(update_github_secret("WEIRDHOST_COOKIE", new_str)):
                                print("[+] Cookie 已更新")
                            break
            except:
                pass

    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = f"🎁 <b>Weirdhost</b>\n\n❌ 异常\n<code>{repr(e)[:200]}</code>"
        if os.path.exists("popup_fixed.png"):
            sync_tg_notify_photo("popup_fixed.png", msg)
        else:
            sync_tg_notify(msg)


if __name__ == "__main__":
    add_server_time()

