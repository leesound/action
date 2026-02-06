#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v7 - 修复弹窗裁剪 Turnstile
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


def random_delay(min_sec=0.5, max_sec=2.0):
    time.sleep(random.uniform(min_sec, max_sec))


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
            print("[TG] 通知已发送")
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
# 弹窗样式修复 + Turnstile 处理（核心）
# ============================================================

EXPAND_POPUP_JS = """
(function() {
    // 1. 扩大所有可能的弹窗容器
    var selectors = [
        '[class*="Popup"]', '[class*="popup"]',
        '[class*="Modal"]', '[class*="modal"]',
        '[class*="Dialog"]', '[class*="dialog"]',
        '[role="dialog"]', '[role="alertdialog"]',
        '[class*="overlay"]', '[class*="Overlay"]'
    ];
    
    selectors.forEach(function(sel) {
        document.querySelectorAll(sel).forEach(function(el) {
            el.style.overflow = 'visible';
            el.style.minWidth = '400px';
            el.style.maxWidth = 'none';
            el.style.width = 'auto';
        });
    });
    
    // 2. 找到 Turnstile 的父容器，逐层向上修复 overflow
    var iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
    iframes.forEach(function(iframe) {
        var el = iframe;
        for (var i = 0; i < 15; i++) {
            el = el.parentElement;
            if (!el) break;
            var style = window.getComputedStyle(el);
            if (style.overflow === 'hidden' || style.overflowX === 'hidden') {
                el.style.overflow = 'visible';
            }
            el.style.minWidth = 'auto';
            el.style.maxWidth = 'none';
        }
        
        // 确保 iframe 本身足够大
        iframe.style.minWidth = '300px';
        iframe.style.minHeight = '65px';
        iframe.style.width = '300px';
        iframe.style.height = '65px';
    });
    
    // 3. 修复 cf-turnstile 容器
    document.querySelectorAll('[class*="cf-turnstile"], .cf-turnstile').forEach(function(el) {
        el.style.overflow = 'visible';
        el.style.minWidth = '300px';
        el.style.width = '300px';
        el.style.height = '65px';
        el.style.position = 'relative';
    });
    
    return 'done';
})();
"""


def expand_popup_for_turnstile(sb):
    """修复弹窗样式，确保 Turnstile 完全可见"""
    try:
        result = sb.execute_script(EXPAND_POPUP_JS)
        print(f"[+] 弹窗样式已修复: {result}")
        time.sleep(0.5)
    except Exception as e:
        print(f"[!] 修复弹窗样式失败: {e}")


def get_turnstile_screen_coords(sb):
    """获取 Turnstile checkbox 的屏幕绝对坐标"""
    try:
        coords = sb.execute_script("""
            var iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
            if (iframes.length === 0) return null;
            
            var iframe = iframes[0];
            var rect = iframe.getBoundingClientRect();
            
            // checkbox 在 iframe 内左侧约 (30, 中间) 的位置
            return {
                iframe_x: rect.x,
                iframe_y: rect.y,
                iframe_w: rect.width,
                iframe_h: rect.height,
                click_x: Math.round(rect.x + 30),
                click_y: Math.round(rect.y + rect.height / 2),
                screen_x: window.screenX || window.screenLeft || 0,
                screen_y: window.screenY || window.screenTop || 0,
                outer_h: window.outerHeight,
                inner_h: window.innerHeight
            };
        """)
        return coords
    except:
        return None


def xdotool_click(x, y):
    """xdotool 物理点击"""
    print(f"[xdotool] click ({x}, {y})")
    subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], check=True)
    time.sleep(0.2)
    subprocess.run(["xdotool", "click", "1"], check=True)


def click_turnstile_checkbox(sb):
    """点击 Turnstile checkbox - 多种方法"""

    # --- 方法A: 坐标点击 (xdotool) ---
    coords = get_turnstile_screen_coords(sb)
    if coords:
        print(f"[*] Turnstile iframe: ({coords['iframe_x']:.0f}, {coords['iframe_y']:.0f}) "
              f"{coords['iframe_w']:.0f}x{coords['iframe_h']:.0f}")

        chrome_bar = coords["outer_h"] - coords["inner_h"]
        abs_x = coords["click_x"] + coords["screen_x"]
        abs_y = coords["click_y"] + coords["screen_y"] + chrome_bar

        print(f"[*] chrome_bar={chrome_bar}, abs=({abs_x}, {abs_y})")
        xdotool_click(abs_x, abs_y)
        return True

    # --- 方法B: Selenium ActionChains 点击 iframe 元素 ---
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        iframes = sb.find_elements("css selector", "iframe[src*='challenges.cloudflare.com']")
        if iframes:
            iframe = iframes[0]
            # 点击 iframe 左侧 1/5 处（checkbox 位置）
            w = iframe.size.get("width", 300)
            h = iframe.size.get("height", 65)
            offset_x = -int(w / 2) + 30  # 从中心偏移到左侧30px
            offset_y = 0
            ActionChains(sb.driver).move_to_element_with_offset(
                iframe, offset_x, offset_y
            ).click().perform()
            print("[+] ActionChains 点击 iframe")
            return True
    except Exception as e:
        print(f"[!] ActionChains 失败: {e}")

    # --- 方法C: uc_gui_click_captcha ---
    try:
        sb.uc_gui_click_captcha()
        print("[+] uc_gui_click_captcha 完成")
        return True
    except Exception as e:
        print(f"[!] uc_gui_click_captcha 失败: {e}")

    return False


def check_turnstile_solved(sb):
    """检查 Turnstile 是否已通过"""
    try:
        return sb.execute_script("""
            var inputs = document.querySelectorAll(
                'input[name="cf-turnstile-response"], [name*="turnstile"]'
            );
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].value && inputs[i].value.length > 20) return true;
            }
            return false;
        """)
    except:
        return False


# ============================================================
# 页面解析
# ============================================================
def get_expiry_from_page(sb):
    try:
        try:
            elements = sb.find_elements("xpath", "//*[contains(text(), '유통기한')]")
            for el in elements:
                match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', el.text)
                if match:
                    return match.group(1).strip()
        except:
            pass
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
    try:
        url = sb.get_current_url()
        if "/login" in url or "/auth" in url:
            return False
        if get_expiry_from_page(sb) != "Unknown":
            return True
        if sb.is_element_present("//button//span[contains(text(), '시간추가')]"):
            return True
        if "PterodactylUser" in sb.get_page_source():
            return True
        return False
    except:
        return False


def check_cooldown_popup(sb):
    try:
        cooldown_texts = ["아직 연장을 할수없어요", "조금만 더 기다려주세요"]
        page_text = sb.get_page_source()
        for text in cooldown_texts:
            if text in page_text:
                print(f"[*] 冷却期: {text}")
                return True
    except:
        pass
    return False


def click_popup_button(sb):
    for text in ["Next", "확인", "OK", "Close", "닫기"]:
        try:
            xpath = f"//button//span[contains(text(), '{text}')]"
            if sb.is_element_visible(xpath):
                sb.click(xpath)
                return True
        except:
            pass
    return False


# ============================================================
# 主流程
# ============================================================
def handle_turnstile_popup(sb, timeout=60):
    """
    完整处理弹窗内 Turnstile:
    1. 等待 Turnstile iframe 出现
    2. 修复弹窗样式（展开 overflow）
    3. 点击 Turnstile checkbox
    4. 等待验证通过
    5. 点击弹窗内提交按钮
    6. 等待结果
    """
    start = time.time()

    # ---- 阶段1: 等待 Turnstile 出现 ----
    print("[阶段1] 等待 Turnstile iframe...")
    turnstile_found = False
    while time.time() - start < 15:
        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}
        try:
            iframes = sb.find_elements("css selector", "iframe[src*='challenges.cloudflare.com']")
            if iframes:
                turnstile_found = True
                print("[+] Turnstile iframe 已出现")
                break
        except:
            pass
        time.sleep(1)

    if not turnstile_found:
        print("[!] 未检测到 Turnstile，检查是否已有结果...")
        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}
        # 可能不需要验证，直接检查提交按钮
        time.sleep(2)

    # ---- 阶段2: 修复弹窗样式 ----
    print("[阶段2] 修复弹窗样式...")
    expand_popup_for_turnstile(sb)
    time.sleep(1)
    # 再次修复（有些框架会重新渲染）
    expand_popup_for_turnstile(sb)
    time.sleep(0.5)

    sb.save_screenshot("turnstile_expanded.png")

    # ---- 阶段3: 点击 Turnstile ----
    print("[阶段3] 点击 Turnstile checkbox...")
    solved = False

    for attempt in range(5):
        print(f"  尝试 {attempt + 1}/5")

        # 每次点击前都修复样式
        expand_popup_for_turnstile(sb)
        time.sleep(0.5)

        click_turnstile_checkbox(sb)
        time.sleep(3)

        sb.save_screenshot(f"turnstile_attempt_{attempt}.png")

        if check_turnstile_solved(sb):
            print("[+] Turnstile 已通过!")
            solved = True
            break

        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}

        # 检查页面是否有成功提示
        try:
            page = sb.get_page_source()
            if re.search(r'연장.*성공|success|완료', page, re.I):
                return {"status": "success", "message": "续期成功"}
        except:
            pass

        time.sleep(2)

    if not solved:
        print("[!] Turnstile 未确认通过，继续尝试提交...")

    # ---- 阶段4: 点击弹窗内提交按钮 ----
    print("[阶段4] 查找并点击提交按钮...")
    time.sleep(1)

    submit_selectors = [
        "//div[contains(@class,'Popup')]//button[contains(.,'시간추가')]",
        "//div[contains(@class,'modal')]//button[contains(.,'시간추가')]",
        "//div[@role='dialog']//button[contains(.,'시간추가')]",
        "//div[contains(@class,'Popup')]//button[contains(.,'확인')]",
        "//div[contains(@class,'Popup')]//button[contains(.,'연장')]",
        "//div[contains(@class,'Popup')]//button[contains(.,'Next')]",
        "//div[contains(@class,'Popup')]//button[contains(.,'Submit')]",
    ]

    submitted = False
    for sel in submit_selectors:
        try:
            if sb.is_element_visible(sel):
                btn_text = sb.get_text(sel).strip()
                if "DELETE" in btn_text.upper():
                    continue
                print(f"  点击: {btn_text[:30]}")
                sb.click(sel)
                submitted = True
                break
        except:
            continue

    if not submitted:
        # 尝试点击弹窗底部的 시간추가 按钮（截图中可见）
        try:
            buttons = sb.find_elements("css selector", "button")
            for btn in buttons:
                txt = btn.text.strip()
                if "시간추가" in txt and btn.is_displayed():
                    # 确认这不是侧边栏的按钮（通过位置判断）
                    rect = sb.execute_script(
                        "return arguments[0].getBoundingClientRect();", btn
                    )
                    # 弹窗按钮通常在页面中间区域
                    if rect and rect.get("x", 0) > 150:
                        print(f"  点击弹窗内按钮: {txt}")
                        btn.click()
                        submitted = True
                        break
        except Exception as e:
            print(f"[!] 备用按钮查找失败: {e}")

    if not submitted:
        print("[!] 未找到提交按钮")

    sb.save_screenshot("after_submit.png")

    # ---- 阶段5: 等待最终结果 ----
    print("[阶段5] 等待结果...")
    remaining_timeout = max(15, timeout - (time.time() - start))
    result_start = time.time()

    while time.time() - result_start < remaining_timeout:
        if check_cooldown_popup(sb):
            return {"status": "cooldown", "message": "冷却期内"}

        try:
            page = sb.get_page_source()
            if re.search(r'연장.*성공|성공.*연장|success|완료', page, re.I):
                return {"status": "success", "message": "续期成功"}
        except:
            pass

        # 弹窗消失 = 可能成功
        try:
            popup_gone = True
            for sel in ["[class*='Popup']", "[role='dialog']", "[class*='modal']"]:
                if sb.is_element_visible(f"css selector:{sel}"):
                    popup_gone = False
                    break
            if popup_gone and (time.time() - result_start > 5):
                return {"status": "success", "message": "弹窗已关闭"}
        except:
            pass

        time.sleep(2)

    return {"status": "timeout", "message": "等待超时"}


def add_server_time():
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
    print("Weirdhost 自动续期 v7")
    print("=" * 60)
    print(f"[*] Cookie: {cookie_name}")
    print(f"[*] URL: {server_url}")
    print(f"[*] 系统: {platform.system()}")
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
            print("[+] Cookie 已设置")

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
            remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期: {original_expiry}")
            print(f"[*] 剩余: {remaining}")
            sb.save_screenshot("before_renew.png")

            # 步骤3: 点击续期按钮
            print("\n[步骤3] 点击续期按钮")
            random_delay(1.0, 2.0)

            renew_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
            if not sb.is_element_present(renew_xpath):
                renew_xpath = "//button[contains(., '시간추가')]"
            if not sb.is_element_present(renew_xpath):
                sb.save_screenshot("no_button.png")
                sync_tg_notify_photo("no_button.png",
                    f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到续期按钮\n📅 {original_expiry}")
                return

            sb.click(renew_xpath)
            print("[+] 已点击，等待弹窗...")
            time.sleep(3)
            sb.save_screenshot("popup_opened.png")

            # 步骤4: 处理弹窗 Turnstile
            print("\n[步骤4] 处理弹窗 Turnstile")
            result = handle_turnstile_popup(sb, timeout=60)
            print(f"[*] 结果: {result}")
            sb.save_screenshot("result.png")

            if result["status"] == "cooldown":
                click_popup_button(sb)
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"ℹ️ 冷却期内\n📅 到期: {original_expiry}\n⏳ 剩余: {remaining}")
                sync_tg_notify(msg)
                return

            # 步骤5: 验证结果
            print("\n[步骤5] 验证续期结果")
            time.sleep(3)
            click_popup_button(sb)
            time.sleep(1)            if original_dt and new_dt:
                if new_dt > original_dt:
                    diff_h = (new_dt - original_dt).total_seconds() / 3600
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"✅ 续期成功！\n📅 新到期: {new_expiry}\n"
                           f"⏳ 剩余: {new_remaining}\n📝 延长 {diff_h:.1f}h")
                    print(f"\n[+] 成功！+{diff_h:.1f}h")
                    sync_tg_notify(msg)

                elif new_dt == original_dt:
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"⚠️ 到期时间未变化\n📅 到期: {original_expiry}\n"
                           f"⏳ 剩余: {remaining}\n"
                           f"📝 Turnstile 可能未通过或冷却期内")
                    print("\n[*] 时间未变化")
                    sync_tg_notify_photo("final_state.png", msg)

                else:
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"⚠️ 时间异常\n📅 原: {original_expiry}\n📅 新: {new_expiry}")
                    sync_tg_notify_photo("final_state.png", msg)

            elif new_expiry != "Unknown":
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"✅ 续期完成\n📅 到期: {new_expiry}\n⏳ 剩余: {new_remaining}")
                sync_tg_notify(msg)

            else:
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"⚠️ 无法获取到期时间\n📅 原到期: {original_expiry}")
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

        for img in ["final_state.png", "result.png", "after_submit.png",
                     "turnstile_expanded.png", "popup_opened.png", "before_renew.png"]:
            if os.path.exists(img):
                sync_tg_notify_photo(img, error_msg)
                break
        else:
            sync_tg_notify(error_msg)


if __name__ == "__main__":
    add_server_time()
