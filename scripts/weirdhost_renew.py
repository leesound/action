#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v2
修复 Turnstile 检测和成功判断逻辑
"""

import os
import sys
import time
import asyncio
import aiohttp
import base64
import random
import re
import platform
from datetime import datetime
from urllib.parse import unquote
from typing import Optional, Tuple, Dict

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
    """将到期时间字符串转换为 datetime 对象"""
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
    """从页面提取到期时间"""
    try:
        page_text = sb.get_page_source()
        # 韩文: 유통기한 2026-02-13 00:06:57
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)', page_text)
        if match:
            return match.group(1).strip()
        # 英文
        match = re.search(r'Expir(?:y|es?)\s*[:\s]*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)', page_text, re.I)
        if match:
            return match.group(1).strip()
        return "Unknown"
    except:
        return "Unknown"


def detect_turnstile(sb) -> Dict:
    """
    检测 Turnstile 验证 - 改进版
    返回: {"found": bool, "type": str, "element": any}
    """
    result = {"found": False, "type": None, "element": None}
    
    # 方法1: 检测 iframe (包括动态加载的)
    try:
        iframes = sb.find_elements("iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            name = iframe.get_attribute("name") or ""
            # Turnstile iframe 特征
            if any(x in src.lower() for x in ["challenge", "turnstile", "cloudflare"]):
                result = {"found": True, "type": "iframe_src", "element": iframe}
                print(f"[*] 检测到 Turnstile iframe (src): {src[:60]}...")
                return result
            if "cf-" in name.lower() or "turnstile" in name.lower():
                result = {"found": True, "type": "iframe_name", "element": iframe}
                print(f"[*] 检测到 Turnstile iframe (name): {name}")
                return result
            # 检查 iframe 尺寸 (Turnstile 通常是 300x65 左右)
            try:
                size = iframe.size
                if 250 <= size.get('width', 0) <= 350 and 50 <= size.get('height', 0) <= 80:
                    result = {"found": True, "type": "iframe_size", "element": iframe}
                    print(f"[*] 检测到疑似 Turnstile iframe (尺寸: {size})")
                    return result
            except:
                pass
    except Exception as e:
        print(f"[!] iframe 检测异常: {e}")
    
    # 方法2: 检测 Turnstile 容器
    try:
        containers = [
            "[data-sitekey]",
            ".cf-turnstile",
            "#cf-turnstile",
            "div[class*='turnstile']",
            "div[id*='turnstile']"
        ]
        for selector in containers:
            if sb.is_element_present(selector):
                result = {"found": True, "type": "container", "element": selector}
                print(f"[*] 检测到 Turnstile 容器: {selector}")
                return result
    except:
        pass
    
    # 方法3: 通过 JavaScript 检测 Shadow DOM
    try:
        has_turnstile = sb.execute_script("""
            // 检查是否有 Turnstile 相关的全局变量
            if (window.turnstile) return 'global';
            
            // 检查所有 iframe
            const iframes = document.querySelectorAll('iframe');
            for (let iframe of iframes) {
                const src = iframe.src || '';
                const name = iframe.name || '';
                if (src.includes('challenge') || src.includes('turnstile') || 
                    name.includes('cf-') || name.includes('turnstile')) {
                    return 'iframe';
                }
            }
            
            // 检查 Shadow DOM
            const allElements = document.querySelectorAll('*');
            for (let el of allElements) {
                if (el.shadowRoot) {
                    try {
                        const shadowContent = el.shadowRoot.innerHTML || '';
                        if (shadowContent.includes('turnstile') || shadowContent.includes('challenge')) {
                            return 'shadow';
                        }
                    } catch(e) {}
                }
            }
            
            return null;
        """)
        if has_turnstile:
            result = {"found": True, "type": f"js_{has_turnstile}", "element": None}
            print(f"[*] 通过 JS 检测到 Turnstile: {has_turnstile}")
            return result
    except Exception as e:
        print(f"[!] JS 检测异常: {e}")
    
    # 方法4: 检测页面文本
    try:
        page_text = sb.get_page_source().lower()
        indicators = ["verify you are human", "checking your browser", "just a moment", "please wait"]
        if any(x in page_text for x in indicators):
            result = {"found": True, "type": "page_text", "element": None}
            print("[*] 通过页面文本检测到验证")
            return result
    except:
        pass
    
    return result


def handle_turnstile(sb, max_attempts: int = 3) -> bool:
    """
    处理 Turnstile 验证 - 改进版
    """
    detection = detect_turnstile(sb)
    
    if not detection["found"]:
        print("[*] 未检测到 Turnstile")
        return True
    
    print(f"[*] Turnstile 类型: {detection['type']}")
    
    for attempt in range(max_attempts):
        print(f"\n[*] Turnstile 处理尝试 {attempt + 1}/{max_attempts}")
        
        # 方法1: SeleniumBase UC Mode 内置处理
        try:
            print("[*] 尝试 UC GUI 点击...")
            sb.uc_gui_click_captcha()
            time.sleep(3)
            
            # 验证是否成功
            new_detection = detect_turnstile(sb)
            if not new_detection["found"]:
                print("[+] Turnstile 已通过 (UC GUI)")
                return True
        except Exception as e:
            print(f"[!] UC GUI 失败: {e}")
        
        # 方法2: 定位 iframe 并点击
        if detection["element"] and detection["type"].startswith("iframe"):
            try:
                iframe = detection["element"]
                location = iframe.location
                size = iframe.size
                
                # Checkbox 通常在 iframe 左侧
                click_x = location['x'] + 25
                click_y = location['y'] + size['height'] // 2
                
                print(f"[*] 尝试点击 iframe 内 checkbox: ({click_x}, {click_y})")
                
                # 使用 ActionChains 移动并点击
                from selenium.webdriver.common.action_chains import ActionChains
                actions = ActionChains(sb.driver)
                actions.move_by_offset(click_x, click_y).click().perform()
                actions.reset_actions()
                
                time.sleep(3)
                
                new_detection = detect_turnstile(sb)
                if not new_detection["found"]:
                    print("[+] Turnstile 已通过 (iframe click)")
                    return True
            except Exception as e:
                print(f"[!] iframe 点击失败: {e}")
        
        # 方法3: 切换到 iframe 内部操作
        try:
            iframes = sb.find_elements("iframe")
            for iframe in iframes:
                try:
                    sb.switch_to_frame(iframe)
                    
                    # 尝试找到并点击 checkbox
                    checkbox_selectors = [
                        'input[type="checkbox"]',
                        '[role="checkbox"]',
                        '.ctp-checkbox-label',
                        '#challenge-stage input'
                    ]
                    
                    for selector in checkbox_selectors:
                        try:
                            if sb.is_element_present(selector):
                                print(f"[*] 在 iframe 内找到: {selector}")
                                sb.click(selector)
                                time.sleep(2)
                                break
                        except:
                            pass
                    
                    sb.switch_to_default_content()
                except:
                    sb.switch_to_default_content()
        except Exception as e:
            print(f"[!] iframe 内部操作失败: {e}")
        
        time.sleep(2)
    
    print("[!] Turnstile 处理可能未成功")
    return False


def find_and_click_renew_button(sb) -> bool:
    """查找并点击续期按钮"""
    button_texts = ["시간추가", "Add Time", "Renew", "续期", "연장"]
    
    for text in button_texts:
        # CSS 选择器
        try:
            selector = f"button:contains('{text}')"
            if sb.is_element_visible(selector):
                print(f"[*] 找到按钮: {text}")
                random_delay(0.5, 1.0)
                sb.click(selector)
                return True
        except:
            pass
        
        # XPath
        try:
            xpath = f"//button[contains(text(), '{text}')]"
            if sb.is_element_present(xpath):
                print(f"[*] 通过 XPath 找到按钮: {text}")
                random_delay(0.5, 1.0)
                sb.click(xpath)
                return True
        except:
            pass
        
        # 链接形式
        try:
            xpath = f"//a[contains(text(), '{text}')]"
            if sb.is_element_present(xpath):
                print(f"[*] 找到链接按钮: {text}")
                random_delay(0.5, 1.0)
                sb.click(xpath)
                return True
        except:
            pass
    
    return False


def wait_for_api_response(sb, timeout: int = 30) -> Dict:
    """
    等待并捕获 API 响应
    通过监控网络请求或页面变化来判断结果
    """
    result = {"success": None, "message": "", "is_cooldown": False}
    
    print("[*] 等待 API 响应...")
    
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            # 检查页面是否有错误/成功提示
            page_source = sb.get_page_source()
            page_lower = page_source.lower()
            
            # 冷却期错误检测
            cooldown_patterns = [
                r"can only once",
                r"already renewed",
                r"cannot renew",
                r"can't renew",
                r"too soon",
                r"wait.*hour",
                r"wait.*minute",
                r"한 번만",  # 韩文: 只能一次
                r"이미.*갱신",  # 韩文: 已经续期
            ]
            for pattern in cooldown_patterns:
                if re.search(pattern, page_lower):
                    result["is_cooldown"] = True
                    result["message"] = "冷却期内"
                    print(f"[*] 检测到冷却期: {pattern}")
                    return result
            
            # 成功检测 - 需要更严格的匹配
            success_patterns = [
                r"successfully\s+renewed",
                r"renewal\s+success",
                r"time\s+added",
                r"extended\s+successfully",
                r"갱신.*완료",  # 韩文: 续期完成
                r"시간.*추가.*완료",  # 韩文: 时间添加完成
            ]
            for pattern in success_patterns:
                if re.search(pattern, page_lower):
                    result["success"] = True
                    result["message"] = "续期成功"
                    print(f"[*] 检测到成功: {pattern}")
                    return result
            
            # 错误检测
            error_patterns = [
                r"error",
                r"failed",
                r"실패",  # 韩文: 失败
            ]
            for pattern in error_patterns:
                if re.search(pattern, page_lower):
                    # 排除误报 (如 "error" 在其他上下文)
                    context = re.search(rf'.{{0,50}}{pattern}.{{0,50}}', page_lower)
                    if context:
                        context_text = context.group(0)
                        # 如果是明确的错误消息
                        if any(x in context_text for x in ["renew", "time", "갱신", "시간"]):
                            result["success"] = False
                            result["message"] = f"续期失败: {context_text[:50]}"
                            return result
            
        except Exception as e:
            print(f"[!] 检查异常: {e}")
        
        time.sleep(1)
        print(f"[*] 等待中... ({int(time.time() - start_time)}s)")
    
    result["message"] = "超时，未检测到明确结果"
    return result


def verify_renewal_by_time(sb, original_expiry: str) -> Dict:
    """
    通过比较到期时间来验证续期是否成功
    这是最可靠的验证方法
    """
    result = {"success": False, "new_expiry": None, "message": ""}
    
    # 刷新页面
    print("[*] 刷新页面以获取最新状态...")
    sb.refresh()
    time.sleep(5)
    
    # 处理可能的 CF 验证
    handle_turnstile(sb, max_attempts=1)
    time.sleep(2)
    
    # 获取新的到期时间
    new_expiry = get_expiry_from_page(sb)
    result["new_expiry"] = new_expiry
    
    print(f"[*] 原到期时间: {original_expiry}")
    print(f"[*] 新到期时间: {new_expiry}")
    
    # 比较时间
    original_dt = parse_expiry_to_datetime(original_expiry)
    new_dt = parse_expiry_to_datetime(new_expiry)
    
    if original_dt and new_dt:
        if new_dt > original_dt:
            diff = new_dt - original_dt
            diff_hours = diff.total_seconds() / 3600
            result["success"] = True
            result["message"] = f"到期时间延长了 {diff_hours:.1f} 小时"
            print(f"[+] {result['message']}")
        elif new_dt == original_dt:
            result["message"] = "到期时间未变化"
            print(f"[*] {result['message']}")
        else:
            result["message"] = "到期时间异常减少"
            print(f"[!] {result['message']}")
    else:
        result["message"] = "无法比较到期时间"
        print(f"[!] {result['message']}")
    
    return result


def add_server_time():
    """主函数"""
    # 解析环境变量
    weirdhost_cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    weirdhost_id = os.environ.get("WEIRDHOST_ID", "").strip()
    
    cookie_name, cookie_value = parse_weirdhost_cookie(weirdhost_cookie)
    server_url = build_server_url(weirdhost_id)
    
    if not cookie_name or not cookie_value:
        sync_tg_notify("🎁 <b>Weirdhost 续订报告</b>\n\n❌ WEIRDHOST_COOKIE 未设置或格式错误")
        return
    
    if not server_url:
        sync_tg_notify("🎁 <b>Weirdhost 续订报告</b>\n\n❌ WEIRDHOST_ID 未设置")
        return
    
    print("=" * 60)
    print("Weirdhost 自动续期 v2")
    print("=" * 60)
    print(f"[*] Cookie: {cookie_name}")
    print(f"[*] URL: {server_url}")
    print(f"[*] 系统: {platform.system()}")
    print("=" * 60)
    
    original_expiry = "Unknown"
    
    try:
        with SB(uc=True, test=True, locale="en", headless=False) as sb:
            print("\n[*] 浏览器已启动")
            
            # 访问域名
            print(f"[*] 访问: https://{DOMAIN}")
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=5)
            time.sleep(2)
            handle_turnstile(sb)
            
            # 设置 Cookie
            print(f"[*] 设置 Cookie")
            sb.add_cookie({"name": cookie_name, "value": cookie_value, "domain": DOMAIN, "path": "/"})
            
            # 访问服务器页面
            print(f"\n[*] 访问服务器页面")
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)
            handle_turnstile(sb)
            
            # 检查登录状态
            if "/login" in sb.get_current_url():
                sb.save_screenshot("login_failed.png")
                sync_tg_notify_photo("login_failed.png", "🎁 <b>Weirdhost</b>\n\n❌ Cookie 已失效")
                return
            
            print("[+] 登录成功")
            
            # 获取当前到期时间
            original_expiry = get_expiry_from_page(sb)
            remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期时间: {original_expiry}")
            print(f"[*] 剩余: {remaining}")
            
            sb.save_screenshot("before_renew.png")
            
            # 点击续期按钮
            print("\n" + "=" * 50)
            print("[*] 开始续期")
            print("=" * 50)
            
            random_delay(1.0, 2.0)
            
            if not find_and_click_renew_button(sb):
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png", f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到续期按钮\n📅 到期: {original_expiry}")
                return
            
            print("[+] 已点击续期按钮")
            time.sleep(2)
            sb.save_screenshot("after_click.png")
            
            # 处理 Turnstile
            print("\n[*] 处理可能的 Turnstile 验证...")
            handle_turnstile(sb, max_attempts=3)
            
            # 处理确认复选框
            try:
                checkbox_selectors = ['input[type="checkbox"]:not([disabled])', '.modal input[type="checkbox"]']
                for selector in checkbox_selectors:
                    if sb.is_element_visible(selector):
                        print(f"[*] 点击复选框: {selector}")
                        random_delay(0.3, 0.8)
                        sb.click(selector)
                        time.sleep(1)
                        break
            except:
                pass
            
            # 处理确认按钮
            try:
                confirm_texts = ["확인", "Confirm", "OK", "Submit"]
                for text in confirm_texts:
                    try:
                        btn = f"button:contains('{text}')"
                        if sb.is_element_visible(btn):
                            print(f"[*] 点击确认: {text}")
                            random_delay(0.3, 0.8)
                            sb.click(btn)
                            time.sleep(2)
                            break
                    except:
                        pass
            except:
                pass
            
            time.sleep(3)
            sb.save_screenshot("after_confirm.png")
            
            # 等待 API 响应
            api_result = wait_for_api_response(sb, timeout=15)
            
            # 最终验证: 通过时间比较
            print("\n[*] 验证续期结果...")
            time_result = verify_renewal_by_time(sb, original_expiry)
            
            sb.save_screenshot("final_state.png")
            
            # 综合判断结果
            new_expiry = time_result["new_expiry"] or original_expiry
            new_remaining = calculate_remaining_time(new_expiry)
            
            if time_result["success"]:
                # 时间确实延长了
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

✅ 续期成功！
📅 新到期: {new_expiry}
⏳ 剩余: {new_remaining}
📝 {time_result['message']}"""
                print(f"\n[+] 续期成功！")
                sync_tg_notify(msg)
            
            elif api_result["is_cooldown"]:
                # 冷却期
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

ℹ️ 暂无需续期（冷却期内）
📅 到期: {original_expiry}
⏳ 剩余: {remaining}"""
                print(f"\n[*] 冷却期内")
                sync_tg_notify(msg)
            
            else:
                # 状态未知 - 发送截图
                msg = f"""🎁 <b>Weirdhost 续订报告</b>

⚠️ 续期状态未知
📅 到期: {new_expiry}
⏳ 剩余: {new_remaining}
📝 API: {api_result['message']}
📝 时间: {time_result['message']}"""
                print(f"\n[?] 状态未知")
                sync_tg_notify_photo("final_state.png", msg)
            
            # 尝试更新 Cookie
            try:
                cookies = sb.get_cookies()
                for cookie in cookies:
                    if cookie.get("name", "").startswith("remember_web"):
                        new_cookie_value = cookie.get("value", "")
                        if new_cookie_value and new_cookie_value != cookie_value:
                            new_cookie_str = f"{cookie['name']}={new_cookie_value}"
                            print(f"\n[*] 检测到新 Cookie")
                            updated = asyncio.run(update_github_secret("WEIRDHOST_COOKIE", new_cookie_str))
                            if updated:
                                print("[+] Cookie 已更新到 GitHub Secrets")
                            break
            except Exception as e:
                print(f"[!] Cookie 更新失败: {e}")
    
    except Exception as e:
        import traceback
        error_msg = f"🎁 <b>Weirdhost 续订报告</b>\n\n❌ 运行异常\n\n<code>{repr(e)}</code>"
        print(f"\n[!] 异常: {repr(e)}")
        traceback.print_exc()
        
        try:
            if os.path.exists("final_state.png"):
                sync_tg_notify_photo("final_state.png", error_msg)
            elif os.path.exists("after_click.png"):
                sync_tg_notify_photo("after_click.png", error_msg)
            else:
                sync_tg_notify(error_msg)
        except:
            sync_tg_notify(error_msg)


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    add_server_time()

