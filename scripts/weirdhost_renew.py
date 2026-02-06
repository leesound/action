#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 v8
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
                days, hours = diff.days, diff.seconds // 3600
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


def sync_tg_notify(msg):
    asyncio.run(tg_notify(msg))


def sync_tg_notify_photo(path, caption=""):
    asyncio.run(tg_notify_photo(path, caption))


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
        return False
    except:
        return False


# ============================================================
# 弹窗检测
# ============================================================
def check_result_popup(sb):
    """
    检测结果弹窗
    返回: ("success", msg) / ("cooldown", msg) / ("error", msg) / None
    """
    try:
        page = sb.get_page_source()
        
        # 成功弹窗: MessageBox 包含 "Success" + "successfully renew"
        if 'class="MessageBox' in page:
            if "Success" in page and "successfully renew" in page:
                return ("success", "You've successfully renew your server")
            
            # 冷却期弹窗: MessageBox 包含 "Error" + 韩文冷却提示
            if "Error" in page:
                if "아직 서버를 갱신할 수 없습니다" in page or "아직 연장을 할수없어요" in page:
                    return ("cooldown", "冷却期内，请稍后再试")
                return ("error", "未知错误")
        
        # 备用检测: 성공! 弹窗
        if re.search(r'성공\s*!', page):
            return ("success", "성공!")
            
    except:
        pass
    return None


def check_turnstile_popup_open(sb):
    """检测 Turnstile 弹窗是否打开（시간연장 弹窗）"""
    try:
        # 检测弹窗特征: 包含 시간연장 + cf-turnstile-response
        page = sb.get_page_source()
        if 'name="cf-turnstile-response"' in page:
            # 确认弹窗可见
            if sb.is_element_visible("css selector:[class*='style-module']") or \
               '시간연장' in page:
                return True
    except:
        pass
    return False


# ============================================================
# Turnstile 处理（核心）
# ============================================================
EXPAND_TURNSTILE_JS = """
(function() {
    // 找到 Turnstile 容器并修复样式
    var containers = document.querySelectorAll('[class*="style-module"], [class*="sc-"]');
    containers.forEach(function(el) {
        if (el.innerHTML && el.innerHTML.includes('cf-turnstile')) {
            el.style.overflow = 'visible';
            el.style.minWidth = '320px';
            el.style.width = 'auto';
            
            // 向上修复父容器
            var parent = el;
            for (var i = 0; i < 10; i++) {
                parent = parent.parentElement;
                if (!parent) break;
                parent.style.overflow = 'visible';
            }
        }
    });
    
    // 修复所有 iframe
    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('cloudflare')) {
            iframe.style.minWidth = '300px';
            iframe.style.minHeight = '65px';
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            
            var parent = iframe;
            for (var i = 0; i < 10; i++) {
                parent = parent.parentElement;
                if (!parent) break;
                parent.style.overflow = 'visible';
                parent.style.minWidth = '300px';
            }
        }
    });
    
    return 'done';
})();
"""


def get_turnstile_checkbox_position(sb):
    """获取 Turnstile checkbox 的位置"""
    try:
        # 方法1: 查找 Cloudflare iframe
        result = sb.execute_script("""
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('cloudflare') || src.includes('turnstile')) {
                    var rect = iframes[i].getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            x: rect.x + 30,  // checkbox 在左侧约30px处
                            y: rect.y + rect.height / 2,
                            width: rect.width,
                            height: rect.height,
                            found: 'iframe'
                        };
                    }
                }
            }
            
            // 方法2: 查找包含 "确认您是真人" 的元素
            var elements = document.querySelectorAll('*');
            for (var i = 0; i < elements.length; i++) {
                var text = elements[i].innerText || '';
                if (text.includes('确认您是真人') || text.includes('Verify you are human')) {
                    var rect = elements[i].getBoundingClientRect();
                    if (rect.width > 50 && rect.height > 20) {
                        return {
                            x: rect.x + 20,
                            y: rect.y + rect.height / 2,
                            width: rect.width,
                            height: rect.height,
                            found: 'text'
                        };
                    }
                }
            }
            
            return null;
        """)
        return result
    except:
        return None


def check_turnstile_solved(sb):
    """检查 Turnstile 是否已通过"""
    try:
        return sb.execute_script("""
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input && input.value && input.value.length > 20) {
                return true;
            }
            return false;
        """)
    except:
        return False


def click_turnstile_with_xdotool(sb):
    """使用 xdotool 点击 Turnstile checkbox"""
    try:
        # 先修复样式
        sb.execute_script(EXPAND_TURNSTILE_JS)
        time.sleep(0.5)
        
        # 获取位置
        pos = get_turnstile_checkbox_position(sb)
        if not pos:
            print("    [!] 无法获取 Turnstile 位置")
            return False
        
        print(f"    [*] Turnstile 位置: x={pos['x']:.0f}, y={pos['y']:.0f} ({pos.get('found', '?')})")
        
        # 计算屏幕坐标
        x = int(pos['x']) + random.randint(-3, 3)
        y = int(pos['y']) + random.randint(-3, 3)
        
        # xdotool 点击
        subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True)
        time.sleep(0.2)
        subprocess.run(["xdotool", "click", "1"], check=True)
        
        print(f"    [+] 已点击 ({x}, {y})")
        return True
        
    except Exception as e:
        print(f"    [!] xdotool 点击失败: {e}")
        return False


def handle_turnstile_in_popup(sb):
    """
    处理弹窗内的 Turnstile
    返回: ("success", msg) / ("cooldown", msg) / ("error", msg) / ("timeout", msg)
    """
    print("\n[阶段1] 等待弹窗和 Turnstile...")
    
    # 等待弹窗出现
    for i in range(20):
        if check_turnstile_popup_open(sb):
            print("[+] 检测到 Turnstile 弹窗")
            break
        
        # 检查是否直接出结果（无需 Turnstile）
        result = check_result_popup(sb)
        if result:
            print(f"[*] 直接获得结果: {result[0]}")
            return result
        
        time.sleep(0.5)
    else:
        print("[!] 未检测到弹窗")
        return ("error", "未检测到弹窗")
    
    # 截图
    sb.save_screenshot("popup_opened.png")
    
    print("\n[阶段2] 修复弹窗样式...")
    sb.execute_script(EXPAND_TURNSTILE_JS)
    time.sleep(0.5)
    sb.save_screenshot("turnstile_expanded.png")
    
    print("\n[阶段3] 点击 Turnstile checkbox...")
    
    # 最多尝试5次
    for attempt in range(1, 6):
        print(f"  尝试 {attempt}/5")
        
        # 检查是否已通过
        if check_turnstile_solved(sb):
            print("  [+] Turnstile 已通过!")
            break
        
        # 检查是否已有结果
        result = check_result_popup(sb)
        if result:
            print(f"  [*] 检测到结果: {result[0]}")
            return result
        
        # 修复样式
        sb.execute_script(EXPAND_TURNSTILE_JS)
        time.sleep(0.3)
        
        # 尝试点击
        try:
            # 方法1: uc_gui_click_captcha
            sb.uc_gui_click_captcha()
            print("  [+] uc_gui_click_captcha 完成")
        except Exception as e:
            print(f"  [*] uc_gui_click_captcha: {e}")
            # 方法2: xdotool
            click_turnstile_with_xdotool(sb)
        
        time.sleep(2)
        
        # 检查结果
        result = check_result_popup(sb)
        if result:
            print(f"  [*] 检测到结果: {result[0]}")
            return result
    
    sb.save_screenshot("after_turnstile_click.png")
    
    print("\n[阶段4] 等待结果弹窗...")
    
    # 等待结果弹窗出现
    for i in range(30):
        result = check_result_popup(sb)
        if result:
            print(f"[+] 结果: {result[0]} - {result[1]}")
            sb.save_screenshot("result_popup.png")
            
            # 点击 NEXT 按钮关闭弹窗
            try:
                if sb.is_element_present("//button[contains(text(), 'NEXT')]"):
                    sb.click("//button[contains(text(), 'NEXT')]")
                    print("[+] 已点击 NEXT")
                elif sb.is_element_present("//button[contains(text(), 'Next')]"):
                    sb.click("//button[contains(text(), 'Next')]")
            except:
                pass
            
            return result
        
        time.sleep(1)
    
    sb.save_screenshot("timeout.png")
    return ("timeout", "等待结果超时")


# ============================================================
# 主函数
# ============================================================
def add_server_time():
    cookie_str = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_SERVER_ID", "").strip()
    
    if not cookie_str:
        print("[!] 未设置 WEIRDHOST_COOKIE")
        return
    
    cookie_name, cookie_value = parse_weirdhost_cookie(cookie_str)
    if not cookie_name or not cookie_value:
        print("[!] Cookie 格式错误")
        return
    
    server_url = build_server_url(server_id)
    if not server_url:
        print("[!] 未设置 WEIRDHOST_SERVER_ID")
        return
    
    print(f"[*] 目标: {server_url}")
    
    with SB(uc=True, headless=False, xvfb=True, 
            locale_code="ko", agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36") as sb:
        
        try:
            print("\n[*] 浏览器已启动")
            
            # ========== 步骤1: 设置 Cookie ==========
            print("\n[步骤1] 设置 Cookie")
            sb.open(f"https://{DOMAIN}/")
            time.sleep(1)
            sb.add_cookie({
                "name": cookie_name,
                "value": cookie_value,
                "domain": DOMAIN,
                "path": "/",
                "secure": True,
                "httpOnly": True
            })
            print("[+] Cookie 已设置")
            
            # ========== 步骤2: 访问服务器页面 ==========
            print("\n[步骤2] 访问服务器页面")
            sb.open(server_url)
            time.sleep(3)
            
            if not is_logged_in(sb):
                sb.save_screenshot("login_failed.png")
                sync_tg_notify_photo("login_failed.png", "🎁 <b>Weirdhost</b>\n\n❌ 登录失败，Cookie 可能已过期")
                return
            
            print("[+] 登录成功")
            
            original_expiry = get_expiry_from_page(sb)
            remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期: {original_expiry}")
            print(f"[*] 剩余: {remaining}")
            
            sb.save_screenshot("before_renew.png")
            
            # ========== 步骤3: 点击续期按钮 ==========
            print("\n[步骤3] 点击续期按钮")
            
            # 侧栏的 시간추가 按钮
            sidebar_btn_selectors = [
                "//a[contains(@href, 'renew')]//span[contains(text(), '시간추가')]",
                "//div[contains(@class, 'sidebar')]//span[contains(text(), '시간추가')]",
                "//span[contains(text(), '시간추가')]",
                "css selector:a[href*='renew']",
            ]
            
            clicked = False
            for selector in sidebar_btn_selectors:
                try:
                    if selector.startswith("css selector:"):
                        sel = selector.replace("css selector:", "")
                        if sb.is_element_present(f"css selector:{sel}"):
                            sb.click(f"css selector:{sel}")
                            clicked = True
                            break
                    else:
                        if sb.is_element_present(selector):
                            sb.click(selector)
                            clicked = True
                            break
                except:
                    continue
            
            if not clicked:
                # 尝试直接访问续期 URL
                renew_url = server_url.rstrip('/') + "/renew"
                sb.open(renew_url)
                time.sleep(2)
            
            print("[+] 已点击，等待弹窗...")
            time.sleep(2)
            
            # ========== 步骤4: 处理 Turnstile ==========
            print("\n[步骤4] 处理弹窗 Turnstile")
            
            result = handle_turnstile_in_popup(sb)
            
            sb.save_screenshot("final_state.png")
            
            # ========== 步骤5: 验证结果 ==========
            print("\n[步骤5] 验证续期结果")
            
            # 刷新页面获取新的到期时间
            time.sleep(2)
            sb.open(server_url)
            time.sleep(3)
            
            new_expiry = get_expiry_from_page(sb)
            new_remaining = calculate_remaining_time(new_expiry)
            
            print(f"[*] 原到期: {original_expiry}")
            print(f"[*] 新到期: {new_expiry}")
            
            original_dt = parse_expiry_to_datetime(original_expiry)
            new_dt = parse_expiry_to_datetime(new_expiry)
            
            # 根据结果发送通知
            if result[0] == "success":
                if original_dt and new_dt and new_dt > original_dt:
                    diff_h = (new_dt - original_dt).total_seconds() / 3600
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"✅ 续期成功！\n📅 新到期: {new_expiry}\n"
                           f"⏳ 剩余: {new_remaining}\n📝 延长 {diff_h:.1f}h")
                    print(f"\n[+] 成功！+{diff_h:.1f}h")
                    sync_tg_notify(msg)
                else:
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"✅ 续期完成\n📅 到期: {new_expiry}\n⏳ 剩余: {new_remaining}")
                    sync_tg_notify(msg)
            
            elif result[0] == "cooldown":
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"⏳ 冷却期内\n📅 到期: {original_expiry}\n"
                       f"⏳ 剩余: {remaining}\n📝 请稍后再试")
                print("\n[*] 冷却期内")
                sync_tg_notify(msg)
            
            elif result[0] == "timeout":
                if original_dt and new_dt:
                    if new_dt > original_dt:
                        diff_h = (new_dt - original_dt).total_seconds() / 3600
                        msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                               f"✅ 续期成功！\n📅 新到期: {new_expiry}\n"
                               f"⏳ 剩余: {new_remaining}\n📝 延长 {diff_h:.1f}h")
                        print(f"\n[+] 成功！+{diff_h:.1f}h")
                        sync_tg_notify(msg)
                    elif new_dt == original_dt:
                        msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                               f"⚠️ 时间未变化\n📅 到期: {original_expiry}\n"
                               f"⏳ 剩余: {remaining}")
                        print("\n[*] 时间未变化")
                        sync_tg_notify_photo("final_state.png", msg)
                else:
                    msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                           f"⚠️ 等待超时\n📅 到期: {new_expiry}")
                    sync_tg_notify_photo("final_state.png", msg)
            
            else:
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"❌ {result[0]}: {result[1]}\n📅 到期: {original_expiry}")
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
            
            for img in ["final_state.png", "result_popup.png", "after_turnstile_click.png",
                        "turnstile_expanded.png", "popup_opened.png", "before_renew.png"]:
                if os.path.exists(img):
                    sync_tg_notify_photo(img, error_msg)
                    break
            else:
                sync_tg_notify(error_msg)


if __name__ == "__main__":
    add_server_time()
