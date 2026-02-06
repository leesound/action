#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v6 - 修复弹窗内 Turnstile 点击
"""

import os
import time
import asyncio
import aiohttp
import base64
import random
import re
import platform
import subprocess
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
# Turnstile 处理 - 核心修复
# ============================================================
def xdotool_click(x: int, y: int):
    """使用 xdotool 在屏幕坐标点击"""
    print(f"[xdotool] 点击坐标 ({x}, {y})")
    subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True)
    time.sleep(0.3)
    subprocess.run(["xdotool", "click", "1"], check=True)


def find_and_click_turnstile_in_popup(sb) -> bool:
    """
    在弹窗内找到 Turnstile iframe 并通过坐标点击。
    
    Turnstile 的 iframe 通常有以下特征:
    - src 包含 "challenges.cloudflare.com"
    - 或者 id/name 包含 "cf-turnstile"
    """
    
    # 方法1: 通过 JS 获取 Turnstile iframe 的屏幕坐标并用 xdotool 点击
    try:
        # 查找所有可能的 Turnstile iframe
        iframe_selectors = [
            "iframe[src*='challenges.cloudflare.com']",
            "iframe[src*='turnstile']",
            "iframe[id*='cf-']",
            "iframe[name*='cf-']",
            # 弹窗内的 iframe
            "div[class*='Popup'] iframe",
            "div[class*='modal'] iframe",
            "div[class*='dialog'] iframe",
            "div[role='dialog'] iframe",
            # 通用 - 弹窗容器内
            ".Popup iframe",
        ]
        
        for selector in iframe_selectors:
            try:
                iframes = sb.find_elements("css selector", selector)
                if iframes:
                    for iframe in iframes:
                        src = iframe.get_attribute("src") or ""
                        if "challenges.cloudflare.com" in src or "turnstile" in src.lower():
                            # 获取 iframe 在视口中的位置
                            rect = sb.execute_script("""
                                var el = arguments[0];
                                var r = el.getBoundingClientRect();
                                return {x: r.x, y: r.y, width: r.width, height: r.height};
                            """, iframe)
                            
                            if rect and rect.get("width", 0) > 0:
                                # Turnstile checkbox 通常在 iframe 左侧约 (30, 20) 的位置
                                # iframe 本身大约 300x65
                                click_x = int(rect["x"]) + 30
                                click_y = int(rect["y"]) + int(rect["height"] // 2)
                                
                                # 获取浏览器窗口在屏幕上的偏移
                                window_rect = sb.execute_script("""
                                    return {
                                        outerX: window.screenX || window.screenLeft || 0,
                                        outerY: window.screenY || window.screenTop || 0,
                                        innerOffsetY: window.outerHeight - window.innerHeight
                                    };
                                """)
                                
                                screen_x = click_x + window_rect.get("outerX", 0)
                                screen_y = click_y + window_rect.get("outerY", 0) + window_rect.get("innerOffsetY", 0)
                                
                                print(f"[*] Turnstile iframe 位置: {rect}")
                                print(f"[*] 窗口偏移: {window_rect}")
                                print(f"[*] 屏幕点击坐标: ({screen_x}, {screen_y})")
                                
                                xdotool_click(screen_x, screen_y)
                                return True
            except Exception as e:
                print(f"[!] 选择器 {selector} 失败: {e}")
                continue
    except Exception as e:
        print(f"[!] 方法1失败: {e}")
    
    # 方法2: 切换到 iframe 内部，直接点击 checkbox
    try:
        iframes = sb.find_elements("css selector", "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "challenges.cloudflare.com" in src:
                print(f"[*] 找到 Turnstile iframe: {src[:80]}...")
                
                # 切换到 iframe
                sb.driver.switch_to.frame(iframe)
                time.sleep(1)
                
                # 在 iframe 内查找 checkbox
                checkbox_selectors = [
                    "input[type='checkbox']",
                    "#challenge-stage input",
                    ".cb-i",  # Turnstile checkbox class
                    "label",
                    "[role='checkbox']",
                ]
                
                for sel in checkbox_selectors:
                    try:
                        elements = sb.find_elements("css selector", sel)
                        if elements:
                            print(f"[*] 在 iframe 内找到元素: {sel}")
                            elements[0].click()
                            sb.driver.switch_to.default_content()
                            return True
                    except:
                        continue
                
                # 如果找不到具体元素，点击 iframe body
                try:
                    body = sb.find_elements("css selector", "body")
                    if body:
                        body[0].click()
                        sb.driver.switch_to.default_content()
                        return True
                except:
                    pass
                
                sb.driver.switch_to.default_content()
    except Exception as e:
        print(f"[!] 方法2失败: {e}")
        try:
            sb.driver.switch_to.default_content()
        except:
            pass
    
    # 方法3: 使用 SeleniumBase 的 uc_gui_click_captcha 但先确保焦点在弹窗
    try:
        print("[*] 尝试 uc_gui_click_captcha...")
        sb.uc_gui_click_captcha()
        return True
    except Exception as e:
        print(f"[!] 方法3失败: {e}")
    
    return False


def check_turnstile_solved(sb) -> bool:
    """检查 Turnstile 是否已解决"""
    try:
        # 检查隐藏的 turnstile response 字段
        result = sb.execute_script("""
            // 检查 cf-turnstile-response
            var inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].value && inputs[i].value.length > 10) {
                    return true;
                }
            }
            // 检查 turnstile 回调
            var iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
            for (var i = 0; i < iframes.length; i++) {
                var parent = iframes[i].parentElement;
                if (parent) {
                    var response = parent.querySelector('[name="cf-turnstile-response"]');
                    if (response && response.value && response.value.length > 10) {
                        return true;
                    }
                }
            }
            return false;
        """)
        return bool(result)
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
        if get_expiry_from_page(sb) != "Unknown":
            return True
        if sb.is_element_present("//button//span[contains(text(), '시간추가')]"):
            return True
        page_source = sb.get_page_source()
        if "PterodactylUser" in page_source:
            return True
        return False
    except:
        return False


def check_cooldown_popup(sb) -> bool:
    """检查是否有冷却期弹窗 - 아직 연장을 할수없어요"""
    try:
        cooldown_texts = [
            "아직 연장을 할수없어요",
            "조금만 더 기다려주세요",
            "can only renew",
            "wait",
        ]
        page_text = sb.get_page_source()
        for text in cooldown_texts:
            if text in page_text:
                print(f"[*] 检测到冷却期提示: {text}")
                return True
        if sb.is_element_present("//div[@type='error']"):
            return True
    except:
        pass
    return False


def click_popup_button(sb) -> bool:
    """点击弹窗按钮 (Next/확인/OK)"""
    button_texts = ["Next", "확인", "OK", "Close", "닫기"]
    for text in button_texts:
        try:
            xpath = f"//div[contains(@class, 'Popup')]//button//span[contains(text(), '{text}')]/parent::button"
            if sb.is_element_visible(xpath):
                sb.click(xpath)
                return True
            xpath = f"//button//span[contains(text(), '{text}')]"
            if sb.is_element_visible(xpath):
                sb.click(xpath)
                return True
        except:
            pass
    return False


def wait_for_turnstile_and_submit(sb, timeout: int = 60) -> dict:
    """
    等待 Turnstile 出现 → 点击 → 等待验证通过 → 点击提交按钮 → 等待结果
    """
    print(f"[*] 等待 Turnstile 并处理 (最多 {timeout}s)...")

    start = time.time()

    # ---- 阶段1: 等待 Turnstile iframe 出现 ----
    turnstile_found = False
    while time.time() - start < 15:
        try:
            iframes = sb.find_elements("css selector", "iframe[src*='challenges.cloudflare.com']")
            if iframes:
                turnstile_found = True
                print("[+] 检测到 Turnstile iframe")
                break
        except:
            pass

        # 也检查是否直接弹出了冷却期
        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}

        time.sleep(1)

    if not turnstile_found:
        print("[!] 未检测到 Turnstile iframe，可能已自动通过或页面结构不同")
        # 检查是否已经有结果
        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}

    # ---- 阶段2: 点击 Turnstile ----
    sb.save_screenshot("turnstile_before_click.png")

    clicked = False
    for attempt in range(4):
        print(f"[*] Turnstile 点击尝试 ({attempt + 1}/4)")

        if find_and_click_turnstile_in_popup(sb):
            clicked = True
            print("[+] 已尝试点击 Turnstile")
            time.sleep(3)

            sb.save_screenshot(f"turnstile_after_click_{attempt}.png")

            # 检查是否已解决
            if check_turnstile_solved(sb):
                print("[+] Turnstile 已解决!")
                break

            # 检查是否出现了结果弹窗
            if check_cooldown_popup(sb):
                return {"status": "cooldown", "message": "冷却期内"}

        time.sleep(2)

    if not clicked:
        print("[!] 所有 Turnstile 点击尝试均失败")

    # ---- 阶段3: 点击弹窗内的提交/确认按钮 ----
    # Turnstile 通过后，弹窗内可能有一个提交按钮
    submit_clicked = False
    submit_selectors = [
        # 弹窗内的 시간추가 按钮（不是侧边栏的那个）
        "//div[contains(@class, 'Popup')]//button[contains(., '시간추가')]",
        "//div[contains(@class, 'modal')]//button[contains(., '시간추가')]",
        "//div[@role='dialog']//button[contains(., '시간추가')]",
        # 确认/提交按钮
        "//div[contains(@class, 'Popup')]//button[contains(., '확인')]",
        "//div[contains(@class, 'Popup')]//button[contains(., 'Next')]",
        "//div[contains(@class, 'Popup')]//button[contains(., 'Submit')]",
        "//div[contains(@class, 'Popup')]//button[contains(., '연장')]",
        # 通用 - 弹窗内非 DELETE 的按钮
        "//div[contains(@class, 'Popup')]//button[not(contains(., 'DELETE'))]",
    ]

    time.sleep(2)
    for sel in submit_selectors:
        try:
            if sb.is_element_visible(sel):
                btn_text = sb.get_text(sel)
                # 跳过 DELETE 按钮
                if "DELETE" in btn_text.upper():
                    continue
                print(f"[*] 点击弹窗提交按钮: {btn_text.strip()[:30]}")
                sb.click(sel)
                submit_clicked = True
                break
        except:
            continue

    if not submit_clicked:
        print("[!] 未找到弹窗内提交按钮，Turnstile 可能自动提交")

    # ---- 阶段4: 等待最终结果 ----
    print("[*] 等待最终结果...")
    result_start = time.time()
    remaining_time = max(10, timeout - (time.time() - start))

    while time.time() - result_start < remaining_time:
        # 冷却期
        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}

        # 成功提示
        try:
            page_text = sb.get_page_source()
            success_patterns = [
                r"연장.*성공",
                r"성공.*연장",
                r"success",
                r"완료",
                r"extended",
            ]
            for pattern in success_patterns:
                if re.search(pattern, page_text, re.I):
                    return {"status": "success", "message": "续期成功"}
        except:
            pass

        # 弹窗消失 = 可能成功
        try:
            popup_selectors = [
                "div[class*='Popup']",
                "div[role='dialog']",
                "div[class*='modal']",
            ]
            popup_visible = False
            for sel in popup_selectors:
                if sb.is_element_visible(f"css selector:{sel}"):
                    popup_visible = True
                    break
            if not popup_visible and (time.time() - result_start > 5):
                print("[*] 弹窗已消失，可能续期完成")
                return {"status": "success", "message": "弹窗已关闭，可能成功"}
        except:
            pass

        time.sleep(2)

    return {"status": "timeout", "message": "等待超时"}


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
    print("Weirdhost 自动续期 v6")
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

            if not is_logged_in(sb):
                print("[!] 未登录，重试...")
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
                sync_tg_notify_photo("login_failed.png",
                    "🎁 <b>Weirdhost</b>\n\n❌ Cookie 已失效，需要重新登录")
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

            # 侧边栏/页面上的 시간추가 按钮
            renew_button_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
            if not sb.is_element_present(renew_button_xpath):
                renew_button_xpath = "//button[contains(., '시간추가')]"

            if not sb.is_element_present(renew_button_xpath):
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png",
                    f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到续期按钮\n📅 到期: {original_expiry}\n⏳ 剩余: {remaining}")
                return

            sb.click(renew_button_xpath)
            print("[+] 已点击续期按钮，等待弹窗...")
            time.sleep(3)

            sb.save_screenshot("popup_opened.png")

            # ========== 步骤5：处理弹窗内 Turnstile + 提交 ==========
            print(f"\n[步骤4] 处理弹窗内 Turnstile 验证")

            result = wait_for_turnstile_and_submit(sb, timeout=60)
            print(f"[*] 结果: {result}")

            sb.save_screenshot("result.png")

            # 处理冷却期
            if result["status"] == "cooldown":
                click_popup_button(sb)
                time.sleep(1)
                msg = (
                    f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                    f"ℹ️ 暂无需续期（冷却期内）\n"
                    f"📅 到期: {original_expiry}\n"
                    f"⏳ 剩余: {remaining}\n"
                    f"📝 아직 연장을 할수없어요"
                )
                print("\n[*] 冷却期内，无需续期")
                sync_tg_notify(msg)
                return

            # ========== 步骤6：验证续期结果 ==========
            print(f"\n[步骤5] 验证续期结果")
            time.sleep(3)

            # 关闭可能残留的弹窗
            click_popup_button(sb)
            time.sleep(1)

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
                    msg = (
                        f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                        f"✅ 续期成功！\n"
                        f"📅 新到期: {new_expiry}\n"
                        f"⏳ 剩余: {new_remaining}\n"
                        f"📝 延长了 {diff_hours:.1f} 小时"
                    )
                    print(f"\n[+] 续期成功！延长 {diff_hours:.1f} 小时")
                    sync_tg_notify(msg)

                elif new_dt == original_dt:
                    msg = (
                        f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                        f"⚠️ 到期时间未变化\n"
                        f"📅 到期: {original_expiry}\n"
                        f"⏳ 剩余: {remaining}\n"
                        f"📝 Turnstile 可能未通过或冷却期内"
                    )
                    print("\n[*] 时间未变化")
                    sync_tg_notify_photo("final_state.png", msg)

                else:
                    msg = (
                        f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                        f"⚠️ 时间异常\n"
                        f"📅 原: {original_expiry}\n"
                        f"📅 新: {new_expiry}"
                    )
                    sync_tg_notify_photo("final_state.png", msg)

            elif new_expiry != "Unknown":
                msg = (
                    f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                    f"✅ 续期完成\n"
                    f"📅 到期: {new_expiry}\n"
                    f"⏳ 剩余: {new_remaining}"
                )
                sync_tg_notify(msg)

            else:
                msg = (
                    f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                    f"⚠️ 无法获取到期时间\n"
                    f"📅 原到期: {original_expiry}"
                )
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

        for img in ["final_state.png", "result.png", "turnstile_after_click_0.png",
                     "popup_opened.png", "before_renew.png"]:
            if os.path.exists(img):
                sync_tg_notify_photo(img, error_msg)
                break
        else:
            sync_tg_notify(error_msg)


if __name__ == "__main__":
    add_server_time()
