#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v5
"""

import os
import time
import asyncio
import aiohttp
import base64
import random
import re
import platform
from datetime import datetime
from urllib.parse import unquote
from typing import Optional, Tuple

from seleniumbase import SB

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

# ============================================================
# 配置
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz/server/"
DOMAIN = "hub.weirdhost.xyz"


# ============================================================
# 工具函数
# ============================================================
def parse_weirdhost_cookie(cookie_str: str) -> Tuple[Optional[str], Optional[str]]:
    if not cookie_str:
        return (None, None)
    cookie_str = cookie_str.strip()
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            return (parts[0].strip(), unquote(parts[1].strip()))
    return (None, None)


def build_server_url(server_id: str) -> Optional[str]:
    if not server_id:
        return None
    server_id = server_id.strip()
    return server_id if server_id.startswith("http") else f"{BASE_URL}{server_id}"


def calculate_remaining_time(expiry_str: str) -> str:
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


def parse_expiry_to_datetime(expiry_str: str) -> Optional[datetime]:
    if not expiry_str or expiry_str == "Unknown":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(expiry_str.strip(), fmt)
        except ValueError:
            continue
    return None


def random_delay(min_sec: float = 0.5, max_sec: float = 2.0):
    time.sleep(random.uniform(min_sec, max_sec))


# ============================================================
# Telegram 通知
# ============================================================
async def tg_notify(message: str):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        print("[TG] 未配置")
        return
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
            print("[TG] 通知已发送")
        except Exception as e:
            print(f"[TG] 发送失败: {e}")


async def tg_notify_photo(photo_path: str, caption: str = ""):
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


def sync_tg_notify(message: str):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path: str, caption: str = ""):
    asyncio.run(tg_notify_photo(photo_path, caption))


# ============================================================
# GitHub Secrets 更新
# ============================================================
def encrypt_secret(public_key: str, secret_value: str) -> str:
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name: str, secret_value: str) -> bool:
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
            async with session.put(secret_url, headers=headers, json={"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}) as resp:
                return resp.status in (201, 204)
        except:
            return False


# ============================================================
# 核心逻辑
# ============================================================
def get_expiry_from_page(sb) -> str:
    """从页面提取到期时间 - 유통기한 2026-02-13 00:06:57"""
    try:
        # 方法1: 直接查找包含 유통기한 的元素
        try:
            elements = sb.find_elements("xpath", "//*[contains(text(), '유통기한')]")
            for el in elements:
                text = el.text
                match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', text)
                if match:
                    return match.group(1).strip()
        except:
            pass
        
        # 方法2: 从页面源码提取
        page_text = sb.get_page_source()
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        
        # 方法3: 通用日期格式
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', page_text)
        if match:
            return match.group(1).strip()
        
        return "Unknown"
    except:
        return "Unknown"


def is_logged_in(sb) -> bool:
    """检查是否已登录"""
    try:
        current_url = sb.get_current_url()
        if "/login" in current_url or "/auth" in current_url:
            return False
        
        # 检查是否有到期时间显示
        if get_expiry_from_page(sb) != "Unknown":
            return True
        
        # 检查是否有续期按钮
        if sb.is_element_present("//button//span[contains(text(), '시간추가')]"):
            return True
        
        # 检查是否有用户信息
        page_source = sb.get_page_source()
        if "PterodactylUser" in page_source:
            return True
        
        return False
    except:
        return False


def check_cooldown_message(sb) -> bool:
    """检查是否有冷却期提示"""
    try:
        page_text = sb.get_page_source()
        cooldown_patterns = [
            r"can only renew",
            r"already renewed",
            r"wait.*hour",
            r"wait.*minute",
            r"한 번만",
            r"이미.*갱신",
            r"남은.*시간",
            r"too soon",
        ]
        for pattern in cooldown_patterns:
            if re.search(pattern, page_text, re.I):
                return True
    except:
        pass
    return False


def add_server_time():
    """主函数"""
    weirdhost_cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    weirdhost_id = os.environ.get("WEIRDHOST_ID", "").strip()
    
    cookie_name, cookie_value = parse_weirdhost_cookie(weirdhost_cookie)
    server_url = build_server_url(weirdhost_id)
    
    if not cookie_name or not cookie_value:
        sync_tg_notify("🎁 <b>Weirdhost</b>\n\n❌ WEIRDHOST_COOKIE 未设置或格式错误")
        return
    
    if not server_url:
        sync_tg_notify("🎁 <b>Weirdhost</b>\n\n❌ WEIRDHOST_ID 未设置")
        return
    
    print("=" * 60)
    print("Weirdhost 自动续期 v5")
    print("=" * 60)
    print(f"[*] Cookie: {cookie_name}")
    print(f"[*] URL: {server_url}")
    print(f"[*] 系统: {platform.system()}")
    print("=" * 60)
    
    original_expiry = "Unknown"
    
    try:
        with SB(uc=True, test=True, locale="ko", headless=False) as sb:
            print("\n[*] 浏览器已启动")
            
            # ========== 步骤1：访问首页设置 Cookie ==========
            print(f"\n[步骤1] 访问首页设置 Cookie")
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
            time.sleep(2)
            
            sb.add_cookie({
                "name": cookie_name,
                "value": cookie_value,
                "domain": DOMAIN,
                "path": "/"
            })
            print("[+] Cookie 已设置")
            
            # ========== 步骤2：访问服务器页面 ==========
            print(f"\n[步骤2] 访问服务器页面")
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)
            
            # 检查登录状态
            if not is_logged_in(sb):
                print("[!] 未登录，尝试重新设置 Cookie...")
                sb.add_cookie({
                    "name": cookie_name,
                    "value": cookie_value,
                    "domain": DOMAIN,
                    "path": "/"
                })
                sb.uc_open_with_reconnect(server_url, reconnect_time=5)
                time.sleep(3)
            
            if not is_logged_in(sb):
                sb.save_screenshot("login_failed.png")
                sync_tg_notify_photo("login_failed.png", "🎁 <b>Weirdhost</b>\n\n❌ Cookie 已失效，需要重新登录")
                return
            
            print("[+] 登录成功")
            
            # ========== 步骤3：获取到期时间 ==========
            original_expiry = get_expiry_from_page(sb)
            remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期时间: {original_expiry}")
            print(f"[*] 剩余: {remaining}")
            
            sb.save_screenshot("before_renew.png")
            
            # ========== 步骤4：点击续期按钮 ==========
            print(f"\n[步骤3] 点击续期按钮 (시간추가)")
            random_delay(1.0, 2.0)
            
            # 查找并点击 시간추가 按钮
            renew_button_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
            
            if not sb.is_element_present(renew_button_xpath):
                # 尝试其他选择器
                renew_button_xpath = "//button[contains(., '시간추가')]"
            
            if not sb.is_element_present(renew_button_xpath):
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png", f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到续期按钮\n📅 到期: {original_expiry}\n⏳ 剩余: {remaining}")
                return
            
            sb.click(renew_button_xpath)
            print("[+] 已点击续期按钮")
            time.sleep(3)
            
            # 检查冷却期
            if check_cooldown_message(sb):
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

ℹ️ 暂无需续期（冷却期内）
📅 到期: {original_expiry}
⏳ 剩余: {remaining}"""
                print("\n[*] 冷却期内")
                sync_tg_notify(msg)
                return
            
            sb.save_screenshot("after_click.png")
            
            # ========== 步骤5：处理 Turnstile 验证 ==========
            print(f"\n[步骤4] 处理 Turnstile 验证")
            
            # 等待 Turnstile iframe 出现
            time.sleep(2)
            
            # 使用 uc_gui_click_captcha 处理验证码
            for attempt in range(3):
                print(f"[*] 尝试处理验证码 ({attempt + 1}/3)")
                try:
                    sb.uc_gui_click_captcha()
                    print("[+] 验证码处理完成")
                    time.sleep(3)
                    break
                except Exception as e:
                    print(f"[!] 尝试 {attempt + 1} 失败: {e}")
                    time.sleep(2)
            
            sb.save_screenshot("after_captcha.png")
            
            # 再次检查冷却期
            if check_cooldown_message(sb):
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

ℹ️ 暂无需续期（冷却期内）
📅 到期: {original_expiry}
⏳ 剩余: {remaining}"""
                print("\n[*] 冷却期内")
                sync_tg_notify(msg)
                return
            
            # ========== 步骤6：验证续期结果 ==========
            print(f"\n[步骤5] 验证续期结果")
            time.sleep(5)
            
            # 重新访问页面获取最新状态
            print("[*] 重新访问服务器页面...")
            sb.add_cookie({
                "name": cookie_name,
                "value": cookie_value,
                "domain": DOMAIN,
                "path": "/"
            })
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)
            
            new_expiry = get_expiry_from_page(sb)
            new_remaining = calculate_remaining_time(new_expiry)
            
            print(f"[*] 原到期: {original_expiry}")
            print(f"[*] 新到期: {new_expiry}")
            
            sb.save_screenshot("final_state.png")
            
            # 比较时间
            original_dt = parse_expiry_to_datetime(original_expiry)
            new_dt = parse_expiry_to_datetime(new_expiry)
            
            if original_dt and new_dt:
                if new_dt > original_dt:
                    diff_hours = (new_dt - original_dt).total_seconds() / 3600
                    msg = f"""🎁 <b>Weirdhost 续订报告</b>

✅ 续期成功！
📅 新到期: {new_expiry}
⏳ 剩余: {new_remaining}
📝 延长了 {diff_hours:.1f} 小时"""
                    print(f"\n[+] 续期成功！延长 {diff_hours:.1f} 小时")
                    sync_tg_notify(msg)
                
                elif new_dt == original_dt:
                    msg = f"""🎁 <b>Weirdhost 续订报告</b>

ℹ️ 到期时间未变化
📅 到期: {original_expiry}
⏳ 剩余: {remaining}
📝 可能在冷却期内或验证未通过"""
                    print("\n[*] 时间未变化")
                    sync_tg_notify_photo("final_state.png", msg)
                
                else:
                    msg = f"""🎁 <b>Weirdhost 续订报告</b>

⚠️ 时间异常
📅 原: {original_expiry}
📅 新: {new_expiry}"""
                    sync_tg_notify_photo("final_state.png", msg)
            
            elif new_expiry != "Unknown":
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

✅ 续期完成
📅 到期: {new_expiry}
⏳ 剩余: {new_remaining}"""
                sync_tg_notify(msg)
            
            else:
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

⚠️ 无法获取到期时间
📅 原到期: {original_expiry}"""
                sync_tg_notify_photo("final_state.png", msg)
            
            # 更新 Cookie
            try:
                cookies = sb.get_cookies()
                for cookie in cookies:
                    if cookie.get("name", "").startswith("remember_web"):
                        new_val = cookie.get("value", "")
                        if new_val and new_val != cookie_value:
                            new_str = f"{cookie['name']}={new_val}"
                            print(f"\n[*] 检测到新 Cookie")
                            if asyncio.run(update_github_secret("WEIRDHOST_COOKIE", new_str)):
                                print("[+] Cookie 已更新")
                            break
            except:
                pass
    
    except Exception as e:
        import traceback
        error_msg = f"🎁 <b>Weirdhost</b>\n\n❌ 异常\n\n<code>{repr(e)}</code>"
        print(f"\n[!] 异常: {repr(e)}")
        traceback.print_exc()
        
        for img in ["final_state.png", "after_captcha.png", "after_click.png", "before_renew.png"]:
            if os.path.exists(img):
                sync_tg_notify_photo(img, error_msg)
                break
        else:
            sync_tg_notify(error_msg)


if __name__ == "__main__":
    add_server_time()
