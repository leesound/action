#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本
使用 SeleniumBase UC Mode 绕过 Cloudflare Turnstile
"""

import os
import sys
import time
import asyncio
import aiohttp
import base64
import random
import platform
from datetime import datetime
from urllib.parse import unquote
from typing import Optional, Tuple

# SeleniumBase
from seleniumbase import SB

# 图像处理
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# GitHub Secrets 加密
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
    """解析 WEIRDHOST_COOKIE 格式: name=value"""
    if not cookie_str:
        return (None, None)
    cookie_str = cookie_str.strip()
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            name = parts[0].strip()
            value = unquote(parts[1].strip())
            return (name, value)
    return (None, None)


def build_server_url(server_id: str) -> Optional[str]:
    """构建服务器 URL"""
    if not server_id:
        return None
    server_id = server_id.strip()
    if server_id.startswith("http"):
        return server_id
    return f"{BASE_URL}{server_id}"


def calculate_remaining_time(expiry_str: str) -> str:
    """计算剩余时间"""
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return "无法解析"
        
        diff = expiry_dt - datetime.now()
        if diff.total_seconds() < 0:
            return "⚠️ 已过期"
        
        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes = remainder // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes}分钟")
        return " ".join(parts) if parts else "不到1分钟"
    except:
        return "计算失败"


def random_delay(min_sec: float = 0.5, max_sec: float = 2.0):
    """随机延迟，模拟人类行为"""
    time.sleep(random.uniform(min_sec, max_sec))


# ============================================================
# Telegram 通知
# ============================================================
async def tg_notify(message: str):
    """发送 Telegram 文本通知"""
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        print("[TG] 未配置 Telegram")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            })
            print("[TG] 通知已发送")
        except Exception as e:
            print(f"[TG] 发送失败: {e}")


async def tg_notify_photo(photo_path: str, caption: str = ""):
    """发送 Telegram 图片通知"""
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id or not os.path.exists(photo_path):
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
                await session.post(url, data=data)
        except Exception as e:
            print(f"[TG] 图片发送失败: {e}")


def sync_tg_notify(message: str):
    """同步版本的 Telegram 通知"""
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path: str, caption: str = ""):
    """同步版本的 Telegram 图片通知"""
    asyncio.run(tg_notify_photo(photo_path, caption))


# ============================================================
# GitHub Secrets 更新
# ============================================================
def encrypt_secret(public_key: str, secret_value: str) -> str:
    """加密 Secret"""
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name: str, secret_value: str) -> bool:
    """更新 GitHub Secret"""
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
            # 获取公钥
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                pk_data = await resp.json()
            
            # 加密并更新
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            payload = {"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}
            
            async with session.put(secret_url, headers=headers, json=payload) as resp:
                return resp.status in (201, 204)
        except:
            return False


# ============================================================
# 核心续期逻辑
# ============================================================
def get_expiry_from_page(sb) -> str:
    """从页面提取到期时间"""
    try:
        page_text = sb.get_page_source()
        import re
        # 韩文格式: 유통기한 2026-02-13 00:06:57
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)', page_text)
        if match:
            return match.group(1).strip()
        # 英文格式
        match = re.search(r'Expir(?:y|es?)\s*[:\s]*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)', page_text, re.I)
        if match:
            return match.group(1).strip()
        return "Unknown"
    except:
        return "Unknown"


def find_and_click_renew_button(sb) -> bool:
    """查找并点击续期按钮"""
    button_texts = ["시간추가", "Add Time", "Renew", "续期"]
    
    for text in button_texts:
        try:
            # 使用 SeleniumBase 的文本选择器
            selector = f"button:contains('{text}')"
            if sb.is_element_visible(selector):
                print(f"[*] 找到按钮: {text}")
                random_delay(0.3, 0.8)
                sb.click(selector)
                return True
        except:
            pass
        
        # 备用: XPath
        try:
            xpath = f"//button[contains(text(), '{text}')]"
            if sb.is_element_present(xpath):
                print(f"[*] 通过 XPath 找到按钮: {text}")
                random_delay(0.3, 0.8)
                sb.click(xpath)
                return True
        except:
            pass
    
    return False


def handle_turnstile_challenge(sb) -> bool:
    """
    处理 Cloudflare Turnstile 验证
    使用 SeleniumBase UC Mode 的 GUI 点击功能
    """
    print("[*] 检测 Turnstile 验证...")
    
    # 等待 Turnstile iframe 加载
    time.sleep(3)
    
    # 检测是否存在 Turnstile
    turnstile_indicators = [
        "iframe[src*='challenges.cloudflare']",
        "iframe[src*='turnstile']",
        "[data-sitekey]",
        ".cf-turnstile"
    ]
    
    turnstile_found = False
    for selector in turnstile_indicators:
        try:
            if sb.is_element_present(selector):
                turnstile_found = True
                print(f"[*] 检测到 Turnstile: {selector}")
                break
        except:
            pass
    
    if not turnstile_found:
        # 检查页面文本
        try:
            page_text = sb.get_page_source().lower()
            if any(x in page_text for x in ["verify you are human", "checking", "just a moment"]):
                turnstile_found = True
                print("[*] 通过页面文本检测到 Turnstile")
        except:
            pass
    
    if not turnstile_found:
        print("[*] 未检测到 Turnstile，继续...")
        return True
    
    print("[*] 尝试使用 UC Mode 点击 Turnstile...")
    
    try:
        # 方法1: SeleniumBase 内置的 Turnstile 处理
        sb.uc_gui_click_captcha()
        print("[+] UC GUI 点击完成")
        time.sleep(3)
        return True
    except Exception as e:
        print(f"[!] UC GUI 点击失败: {e}")
    
    try:
        # 方法2: 手动定位 iframe 并点击
        iframes = sb.find_elements("iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "challenge" in src or "turnstile" in src:
                print(f"[*] 找到 Turnstile iframe: {src[:50]}...")
                
                # 获取 iframe 位置
                location = iframe.location
                size = iframe.size
                
                # 计算点击坐标 (iframe 中心偏左上，通常是 checkbox 位置)
                click_x = location['x'] + 30
                click_y = location['y'] + size['height'] // 2
                
                print(f"[*] 点击坐标: ({click_x}, {click_y})")
                
                # 使用 pyautogui 进行 OS 级点击
                try:
                    import pyautogui
                    pyautogui.click(click_x, click_y)
                    print("[+] PyAutoGUI 点击完成")
                    time.sleep(3)
                    return True
                except ImportError:
                    print("[!] PyAutoGUI 未安装")
                except Exception as e:
                    print(f"[!] PyAutoGUI 点击失败: {e}")
                
                break
    except Exception as e:
        print(f"[!] 手动定位失败: {e}")
    
    try:
        # 方法3: 键盘 Tab 导航
        print("[*] 尝试键盘导航...")
        for _ in range(10):
            sb.send_keys("body", "\t")
            time.sleep(0.2)
        sb.send_keys("body", " ")  # 空格键选中
        time.sleep(3)
        return True
    except Exception as e:
        print(f"[!] 键盘导航失败: {e}")
    
    return False


def check_renew_result(sb, original_expiry: str) -> dict:
    """检查续期结果"""
    result = {
        "success": False,
        "new_expiry": None,
        "message": ""
    }
    
    time.sleep(3)
    
    # 刷新页面获取最新状态
    try:
        sb.refresh()
        time.sleep(5)
        
        # 处理可能的 CF 验证
        handle_turnstile_challenge(sb)
        time.sleep(2)
    except:
        pass
    
    # 获取新的到期时间
    new_expiry = get_expiry_from_page(sb)
    result["new_expiry"] = new_expiry
    
    # 检查页面是否有错误信息
    try:
        page_text = sb.get_page_source().lower()
        
        # 冷却期错误
        cooldown_keywords = ["can only once", "already renewed", "cannot renew", "can't renew", "too soon"]
        if any(kw in page_text for kw in cooldown_keywords):
            result["message"] = "冷却期内，暂无需续期"
            return result
        
        # 成功标志
        success_keywords = ["success", "renewed", "extended", "완료"]
        if any(kw in page_text for kw in success_keywords):
            result["success"] = True
            result["message"] = "续期成功"
            return result
    except:
        pass
    
    # 比较到期时间
    if new_expiry != "Unknown" and original_expiry != "Unknown":
        try:
            from datetime import datetime
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                try:
                    new_dt = datetime.strptime(new_expiry.strip(), fmt)
                    old_dt = datetime.strptime(original_expiry.strip(), fmt)
                    if new_dt > old_dt:
                        result["success"] = True
                        result["message"] = "到期时间已延长"
                    break
                except:
                    continue
        except:
            pass
    
    if not result["message"]:
        result["message"] = "状态未知"
    
    return result


def add_server_time():
    """主函数: 执行续期操作"""
    # 解析环境变量
    weirdhost_cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    weirdhost_id = os.environ.get("WEIRDHOST_ID", "").strip()
    
    cookie_name, cookie_value = parse_weirdhost_cookie(weirdhost_cookie)
    server_url = build_server_url(weirdhost_id)
    
    # 验证配置
    if not cookie_name or not cookie_value:
        sync_tg_notify("🎁 <b>Weirdhost 续订报告</b>\n\n❌ WEIRDHOST_COOKIE 未设置或格式错误\n\n格式: remember_web_xxxxx=eyJpdiI6...")
        return
    
    if not server_url:
        sync_tg_notify("🎁 <b>Weirdhost 续订报告</b>\n\n❌ WEIRDHOST_ID 未设置\n\n格式: 8a8db3cc")
        return
    
        print("=" * 60)
    print("Weirdhost 自动续期 - SeleniumBase UC Mode")
    print("=" * 60)
    print(f"[*] Cookie Name: {cookie_name}")
    print(f"[*] Cookie Value: {cookie_value[:50]}...")
    print(f"[*] Server URL: {server_url}")
    print(f"[*] 系统: {platform.system()} {platform.release()}")
    print("=" * 60)
    
    expiry_time = "Unknown"
    remaining_time = "Unknown"
    
    try:
        # 使用 SeleniumBase UC Mode
        with SB(uc=True, test=True, locale="en", headless=False) as sb:
            print("\n[*] 浏览器已启动 (UC Mode)")
            
            # 先访问域名以便设置 Cookie
            print(f"[*] 访问域名: https://{DOMAIN}")
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=5)
            time.sleep(2)
            
            # 处理初始 CF 验证
            handle_turnstile_challenge(sb)
            time.sleep(2)
            
            # 添加 Cookie
            print(f"[*] 设置 Cookie: {cookie_name}")
            sb.add_cookie({
                "name": cookie_name,
                "value": cookie_value,
                "domain": DOMAIN,
                "path": "/"
            })
            
            # 访问目标服务器页面
            print(f"\n[*] 访问服务器页面: {server_url}")
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)
            
            # 处理 CF 验证
            handle_turnstile_challenge(sb)
            time.sleep(2)
            
            # 检查是否需要登录
            current_url = sb.get_current_url()
            if "/auth/login" in current_url or "/login" in current_url:
                print("[!] Cookie 已失效，需要重新登录")
                sb.save_screenshot("login_required.png")
                sync_tg_notify_photo("login_required.png", 
                    "🎁 <b>Weirdhost 续订报告</b>\n\n❌ Cookie 已失效，请手动更新")
                return
            
            print("[+] 登录成功！")
            
            # 获取当前到期时间
            expiry_time = get_expiry_from_page(sb)
            remaining_time = calculate_remaining_time(expiry_time)
            print(f"[*] 当前到期时间: {expiry_time}")
            print(f"[*] 剩余时间: {remaining_time}")
            
            # 截图当前状态
            sb.save_screenshot("before_renew.png")
            
            # 查找并点击续期按钮
            print("\n" + "=" * 50)
            print("[*] 开始续期操作")
            print("=" * 50)
            
            random_delay(1.0, 2.0)  # 模拟人类思考时间
            
            if not find_and_click_renew_button(sb):
                print("[!] 未找到续期按钮")
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png",
                    f"🎁 <b>Weirdhost 续订报告</b>\n\n⚠️ 未找到续期按钮\n📅 到期: {expiry_time}\n⏳ 剩余: {remaining_time}")
                return
            
            print("[+] 已点击续期按钮")
            time.sleep(3)
            
            # 处理点击后可能出现的 Turnstile 验证
            print("[*] 检查是否需要 Turnstile 验证...")
            sb.save_screenshot("after_click.png")
            
            # 等待并处理 Turnstile
            for attempt in range(3):
                print(f"[*] Turnstile 处理尝试 {attempt + 1}/3")
                
                if handle_turnstile_challenge(sb):
                    print("[+] Turnstile 处理完成")
                else:
                    print("[!] Turnstile 处理可能失败")
                
                time.sleep(3)
                
                # 检查是否有复选框需要点击
                try:
                    checkbox_selectors = [
                        'input[type="checkbox"]',
                        '.checkbox',
                        '[role="checkbox"]'
                    ]
                    for selector in checkbox_selectors:
                        if sb.is_element_visible(selector):
                            print(f"[*] 找到复选框: {selector}")
                            random_delay(0.5, 1.0)
                            sb.click(selector)
                            print("[+] 已点击复选框")
                            time.sleep(2)
                            break
                except Exception as e:
                    print(f"[*] 复选框处理: {e}")
                
                # 检查是否有确认按钮
                try:
                    confirm_texts = ["확인", "Confirm", "OK", "Submit", "确认"]
                    for text in confirm_texts:
                        try:
                            btn = f"button:contains('{text}')"
                            if sb.is_element_visible(btn):
                                print(f"[*] 找到确认按钮: {text}")
                                random_delay(0.3, 0.8)
                                sb.click(btn)
                                print("[+] 已点击确认按钮")
                                time.sleep(2)
                                break
                        except:
                            pass
                except:
                    pass
                
                # 检查页面状态
                try:
                    page_source = sb.get_page_source().lower()
                    if "success" in page_source or "완료" in page_source:
                        print("[+] 检测到成功标志")
                        break
                    if "error" in page_source or "fail" in page_source:
                        print("[!] 检测到错误标志")
                        break
                except:
                    pass
            
            # 截图最终状态
            sb.save_screenshot("after_renew.png")
            
            # 检查续期结果
            print("\n[*] 检查续期结果...")
            result = check_renew_result(sb, expiry_time)
            
            new_expiry = result.get("new_expiry", "Unknown")
            new_remaining = calculate_remaining_time(new_expiry) if new_expiry != "Unknown" else "Unknown"
            
            # 发送通知
            if result["success"]:
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

✅ 续期成功！
📅 新到期时间: {new_expiry}
⏳ 剩余时间: {new_remaining}
🔗 {server_url}"""
                print(f"\n[+] {result['message']}")
                print(f"[+] 新到期时间: {new_expiry}")
                sync_tg_notify(msg)
            
            elif "冷却" in result["message"] or "暂无需" in result["message"]:
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

ℹ️ 暂无需续期（冷却期内）
📅 到期时间: {expiry_time}
⏳ 剩余时间: {remaining_time}"""
                print(f"\n[*] {result['message']}")
                sync_tg_notify(msg)
            
            else:
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

⚠️ 续期状态未知
📝 {result['message']}
📅 到期时间: {new_expiry if new_expiry != 'Unknown' else expiry_time}
⏳ 剩余时间: {new_remaining if new_remaining != 'Unknown' else remaining_time}"""
                print(f"\n[?] {result['message']}")
                sync_tg_notify_photo("after_renew.png", msg)
            
            # 尝试更新 Cookie
            try:
                cookies = sb.get_cookies()
                for cookie in cookies:
                    if cookie.get("name", "").startswith("remember_web"):
                        new_cookie_value = cookie.get("value", "")
                        if new_cookie_value and new_cookie_value != cookie_value:
                            new_cookie_str = f"{cookie['name']}={new_cookie_value}"
                            print(f"\n[*] 检测到新 Cookie，尝试更新...")
                            updated = asyncio.run(update_github_secret("WEIRDHOST_COOKIE", new_cookie_str))
                            if updated:
                                print("[+] Cookie 已自动更新到 GitHub Secrets")
                            break
            except Exception as e:
                print(f"[!] Cookie 更新检查失败: {e}")
    
    except Exception as e:
        error_msg = f"🎁 <b>Weirdhost 续订报告</b>\n\n❌ 运行异常\n\n<code>{repr(e)}</code>"
        print(f"\n[!] 异常: {repr(e)}")
        
        import traceback
        traceback.print_exc()
        
        # 尝试截图
        try:
            if 'sb' in dir():
                sb.save_screenshot("error.png")
                sync_tg_notify_photo("error.png", error_msg)
            else:
                sync_tg_notify(error_msg)
        except:
            sync_tg_notify(error_msg)


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    add_server_time()

