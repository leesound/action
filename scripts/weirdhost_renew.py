#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v9 - 简化版
流程: 点击侧栏按钮 → Turnstile 验证 → 自动提交 → 检查日期变化
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
                    return "⚠️ 已过期"
                days = diff.days
                hours = diff.seconds // 3600
                minutes = (diff.seconds % 3600) // 60
                parts = []
                if days > 0:
                    parts.append(f"{days}天")
                if hours > 0:
                    parts.append(f"{hours}小时")
                if minutes > 0 and days == 0:
                    parts.append(f"{minutes}分钟")
                return " ".join(parts) if parts else "不到1分钟"
            except ValueError:
                continue
        return "无法解析"
    except:
        return "计算失败"


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
        except Exception as e:
            print(f"[TG] 发送失败: {e}")


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
        except Exception as e:
            print(f"[TG] 图片发送失败: {e}")


def sync_tg_notify(message):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path, caption=""):
    asyncio.run(tg_notify_photo(photo_path, caption))


# ============================================================
# GitHub Secrets
# ============================================================
def encrypt_secret(public_key, secret_value):
    if not NACL_AVAILABLE:
        return None
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
    """从页面获取到期时间"""
    try:
        page_text = sb.get_page_source()
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        return "Unknown"
    except:
        return "Unknown"


def is_logged_in(sb):
    """检查是否已登录"""
    try:
        url = sb.get_current_url()
        if "/login" in url or "/auth" in url:
            return False
        if get_expiry_from_page(sb) != "Unknown":
            return True
        if sb.is_element_present("//button//span[contains(text(), '시간추가')]"):
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
        c.style.height = '65px';
    });
    
    document.querySelectorAll('iframe').forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
        }
    });
    
    return 'done';
})();
"""


def check_turnstile_exists(sb):
    """检查是否有 Turnstile"""
    try:
        return sb.execute_script(
            "return document.querySelector('input[name=\"cf-turnstile-response\"]') !== null;"
        )
    except:
        return False


def check_turnstile_solved(sb):
    """检查 Turnstile 是否已通过"""
    try:
        return sb.execute_script("""
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            return input && input.value && input.value.length > 20;
        """)
    except:
        return False


def get_turnstile_coords(sb):
    """获取 Turnstile 坐标"""
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
    """xdotool 物理点击"""
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))], 
                      check=True, timeout=5)
        time.sleep(0.1)
        subprocess.run(["xdotool", "click", "1"], check=True, timeout=5)
        return True
    except:
        return False


def click_turnstile(sb):
    """点击 Turnstile"""
    coords = get_turnstile_coords(sb)
    if coords:
        print(f"[*] Turnstile: ({coords['x']:.0f}, {coords['y']:.0f}) {coords['width']:.0f}x{coords['height']:.0f}")
        try:
            win = sb.execute_script("""
                return {
                    screenX: window.screenX || 0,
                    screenY: window.screenY || 0,
                    outerH: window.outerHeight,
                    innerH: window.innerHeight
                };
            """)
            chrome_bar = win["outerH"] - win["innerH"]
            abs_x = coords["click_x"] + win["screenX"]
            abs_y = coords["click_y"] + win["screenY"] + chrome_bar
            print(f"[*] 点击: ({abs_x:.0f}, {abs_y:.0f})")
            if xdotool_click(abs_x, abs_y):
                return True
        except Exception as e:
            print(f"[!] xdotool 失败: {e}")
    
    # 备用方法
    try:
        sb.uc_gui_click_captcha()
        print("[+] uc_gui_click_captcha")
        return True
    except:
        pass
    
    return False


def click_next_button(sb):
    """点击 NEXT 按钮"""
    for sel in ["//button[contains(text(), 'NEXT')]", "//button[contains(text(), 'Next')]"]:
        try:
            if sb.is_element_visible(sel):
                sb.click(sel)
                return True
        except:
            pass
    return False


# ============================================================
# 主流程
# ============================================================

def handle_turnstile_and_submit(sb, timeout=60):
    """
    处理 Turnstile 验证
    Turnstile 通过后网站会自动提交，我们只需等待结果
    """
    start = time.time()
    
    # 等待 Turnstile 出现
    print("[*] 等待 Turnstile...")
    for _ in range(15):
        if check_turnstile_exists(sb):
            print("[+] Turnstile 已出现")
            break
        time.sleep(1)
    else:
        print("[!] 未检测到 Turnstile")
        return "no_turnstile"
    
    # 修复弹窗样式
    print("[*] 修复弹窗样式...")
    for _ in range(3):
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
    
    sb.save_screenshot("popup_fixed.png")
    
    # 点击 Turnstile
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
        else:
            continue
        break
    
    # 等待结果 (Turnstile 通过后网站自动提交)
    print("[*] 等待结果...")
    time.sleep(3)
    sb.save_screenshot("after_turnstile.png")
    
    # 点击可能出现的 NEXT 按钮
    click_next_button(sb)
    time.sleep(1)
    
    return "done"


def add_server_time():
    """主函数"""
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

    original_expiry = "Unknown"

    try:
        with SB(uc=True, test=True, locale="ko", headless=False) as sb:
            print("\n[*] 浏览器已启动")

            # 步骤1: Cookie
            print("\n[步骤1] 设置 Cookie")
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
            time.sleep(2)
            sb.add_cookie({
                "name": cookie_name, "value": cookie_value,
                "domain": DOMAIN, "path": "/"
            })

            # 步骤2: 访问页面
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
                sync_tg_notify_photo("login_failed.png", "🎁 <b>Weirdhost</b>\n\n❌ Cookie 失效")
                return

            print("[+] 登录成功")
            original_expiry = get_expiry_from_page(sb)
            remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期: {original_expiry}")
            print(f"[*] 剩余: {remaining}")

            # 步骤3: 点击续期按钮
            print("\n[步骤3] 点击续期按钮")
            time.sleep(random.uniform(1, 2))
            
            btn_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
            if not sb.is_element_present(btn_xpath):
                btn_xpath = "//button[contains(., '시간추가')]"
            
            if not sb.is_element_present(btn_xpath):
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png", f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到按钮\n📅 {original_expiry}")
                return

            sb.click(btn_xpath)
            print("[+] 已点击，等待弹窗...")
            time.sleep(3)

            # 步骤4: 处理 Turnstile
            print("\n[步骤4] 处理 Turnstile")
            handle_turnstile_and_submit(sb)

            # 步骤5: 验证结果
            print("\n[步骤5] 验证结果")
            time.sleep(2)
            
            # 刷新页面
            sb.uc_open_with_reconnect(server_url, reconnect_time=3)
            time.sleep(3)
            
            new_expiry = get_expiry_from_page(sb)
            new_remaining = calculate_remaining_time(new_expiry)
            sb.save_screenshot("final_state.png")

            print(f"[*] 原到期: {original_expiry}")
            print(f"[*] 新到期: {new_expiry}")

            original_dt = parse_expiry_to_datetime(original_expiry)
            new_dt = parse_expiry_to_datetime(new_expiry)

            # 判断结果并发送通知
            if original_dt and new_dt:
                if new_dt > original_dt:
                    # 成功续期
                    diff_h = (new_dt - original_dt).total_seconds() / 3600
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"✅ 续期成功！\n"
                           f"📅 新到期: {new_expiry}\n"
                           f"⏳ 剩余: {new_remaining}\n"
                           f"📝 延长了 {diff_h:.1f} 小时")
                    print(f"\n[+] 成功！延长 {diff_h:.1f} 小时")
                    sync_tg_notify(msg)
                else:
                    # 时间未变化 = 冷却期
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"ℹ️ 冷却期内，暂时无法续期\n"
                           f"📅 到期: {original_expiry}\n"
                           f"⏳ 剩余: {remaining}")
                    print("\n[*] 冷却期内")
                    sync_tg_notify_photo("popup_fixed.png", msg)
            else:
                # 无法解析时间
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"⚠️ 无法确认结果\n"
                       f"📅 原到期: {original_expiry}\n"
                       f"📅 新到期: {new_expiry}")
                sync_tg_notify_photo("popup_fixed.png", msg)

            # 更新 Cookie
            try:
                cookies = sb.get_cookies()
                for cookie in cookies:
                    if cookie.get("name", "").startswith("remember_web"):
                        new_val = cookie.get("value", "")
                        if new_val and new_val != cookie_value:
                            new_cookie_str = f"{cookie['name']}={new_val}"
                            print(f"\n[*] 检测到新 Cookie")
                            if asyncio.run(update_github_secret("WEIRDHOST_COOKIE", new_cookie_str)):
                                print("[+] Cookie 已更新")
                            break
            except Exception as e:
                print(f"[!] Cookie 检查失败: {e}")

    except Exception as e:
        import traceback
        print(f"\n[!] 异常: {repr(e)}")
        traceback.print_exc()
        
        error_msg = f"🎁 <b>Weirdhost</b>\n\n❌ 脚本异常\n\n<code>{repr(e)}</code>"
        
        # 发送最近截图
        for img in ["popup_fixed.png", "after_turnstile.png", "final_state.png"]:
            if os.path.exists(img):
                sync_tg_notify_photo(img, error_msg)
                break
        else:
            sync_tg_notify(error_msg)


if __name__ == "__main__":
    add_server_time()
