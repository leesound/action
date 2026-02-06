#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
  
    var turnstileContainers = document.querySelectorAll('[class*="sc-fKFyDc"], [class*="nwOmR"]');
    turnstileContainers.forEach(function(container) {
        container.style.overflow = 'visible';
        container.style.width = '300px';
        container.style.minWidth = '300px';
        container.style.height = '65px';
    });
  
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
    """检查页面是否有 Turnstile"""
    try:
        return sb.execute_script("""
            return document.querySelector('input[name="cf-turnstile-response"]') !== null;
        """)
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


def get_turnstile_checkbox_coords(sb):
    """获取 Turnstile checkbox 的坐标"""
    try:
        coords = sb.execute_script("""
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('cloudflare') || src.includes('turnstile')) {
                    var rect = iframes[i].getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            click_x: Math.round(rect.x + 30),
                            click_y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                }
            }
          
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input) {
                var container = input.parentElement;
                for (var j = 0; j < 5; j++) {
                    if (!container) break;
                    var rect = container.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 30) {
                        return {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            click_x: Math.round(rect.x + 30),
                            click_y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                    container = container.parentElement;
                }
            }
          
            return null;
        """)
        return coords
    except:
        return None


def xdotool_click(x, y):
    """使用 xdotool 进行物理点击"""
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(int(x)), str(int(y))], 
                      check=True, timeout=5)
        time.sleep(0.1)
        subprocess.run(["xdotool", "click", "1"], check=True, timeout=5)
        return True
    except Exception as e:
        print(f"[!] xdotool 点击失败: {e}")
        return False


def click_turnstile_checkbox(sb):
    """使用 xdotool 点击 Turnstile checkbox"""
    coords = get_turnstile_checkbox_coords(sb)
    if not coords:
        print("[!] 无法获取 Turnstile 坐标")
        return False
  
    print(f"[*] Turnstile 位置: ({coords['x']:.0f}, {coords['y']:.0f}) "
          f"{coords['width']:.0f}x{coords['height']:.0f}")
  
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
      
        print(f"[*] 点击坐标: ({abs_x:.0f}, {abs_y:.0f})")
        return xdotool_click(abs_x, abs_y)
    except Exception as e:
        print(f"[!] 坐标计算失败: {e}")
        return False


# ============================================================
# 结果检测 (修复版)
# ============================================================

def check_success_popup(sb):
    """
    检查是否出现成功弹窗 (성공!)
    注意：必须是结果弹窗，不是 Turnstile 验证通过
    """
    try:
        # 方法1: 检查 MessageBox 样式的成功弹窗
        # 成功弹窗通常有特定的容器类
        result = sb.execute_script("""
            // 检查是否有 MessageBox 类型的成功弹窗
            var messageBoxes = document.querySelectorAll('[class*="MessageBox"], [class*="message-box"], [class*="modal"]');
            for (var i = 0; i < messageBoxes.length; i++) {
                var box = messageBoxes[i];
                var text = box.innerText || '';
                // 成功弹窗应该包含 Success 标题和成功相关内容
                if (text.includes('Success') && (text.includes('성공') || text.includes('renew') || text.includes('갱신'))) {
                    return true;
                }
            }
            
            // 检查是否有绿色成功图标 + 成功消息
            var successElements = document.querySelectorAll('[class*="success"], [class*="Success"]');
            for (var j = 0; j < successElements.length; j++) {
                var el = successElements[j];
                // 排除 Turnstile iframe 内的元素
                if (el.closest('iframe')) continue;
                var text = el.innerText || '';
                if (text.includes('성공') || text.includes('successfully')) {
                    // 确保不是 Turnstile 相关
                    if (!text.includes('Cloudflare') && !text.includes('verification')) {
                        return true;
                    }
                }
            }
            
            return false;
        """)
        
        if result:
            return True
        
        # 方法2: 检查页面中是否有明确的成功消息（排除 Turnstile）
        page = sb.get_page_source()
        
        # 这些是真正的续期成功消息
        success_patterns = [
            "서버가 성공적으로",      # 服务器成功地...
            "연장되었습니다",          # 已延长
            "갱신되었습니다",          # 已更新
            "successfully renew",     # 成功续期
            "renewal successful",     # 续期成功
        ]
        
        for pattern in success_patterns:
            if pattern.lower() in page.lower():
                return True
        
        return False
    except:
        return False


def check_cooldown_popup(sb):
    """检查是否出现冷却期弹窗 (아직...)"""
    try:
        cooldown_texts = [
            "아직 연장을 할수없어요",
            "아직 서버를 갱신할 수 없습니다",
            "남은 시간이 더 줄어들",
            "아직",  # 放在最后，作为兜底
        ]
        page = sb.get_page_source()
        for text in cooldown_texts:
            if text in page:
                # 确保是在弹窗中，不是其他地方
                # 检查是否有 Error 类型的 MessageBox
                if sb.execute_script("""
                    var boxes = document.querySelectorAll('[class*="MessageBox"], [class*="modal"]');
                    for (var i = 0; i < boxes.length; i++) {
                        if (boxes[i].innerText.includes('아직')) return true;
                    }
                    return false;
                """):
                    return True
                # 如果明确包含冷却期消息，也返回 true
                if "연장을 할수없어요" in page or "갱신할 수 없습니다" in page:
                    return True
        return False
    except:
        return False


def check_result_popup_appeared(sb):
    """检查是否出现了结果弹窗（成功或失败）"""
    try:
        # 检查是否有 NEXT 按钮（结果弹窗通常有这个按钮）
        if sb.is_element_visible("//button[contains(text(), 'NEXT')]"):
            return True
        if sb.is_element_visible("//button[contains(text(), 'Next')]"):
            return True
        
        # 检查 MessageBox
        result = sb.execute_script("""
            var boxes = document.querySelectorAll('[class*="MessageBox"]');
            for (var i = 0; i < boxes.length; i++) {
                var text = boxes[i].innerText || '';
                if (text.includes('Success') || text.includes('Error') || text.includes('성공') || text.includes('아직')) {
                    return true;
                }
            }
            return false;
        """)
        return result
    except:
        return False


def click_next_button(sb):
    """点击 NEXT 按钮关闭结果弹窗"""
    try:
        next_selectors = [
            "//button[contains(text(), 'NEXT')]",
            "//button[contains(text(), 'Next')]",
            "//button//span[contains(text(), 'NEXT')]",
        ]
        for sel in next_selectors:
            if sb.is_element_visible(sel):
                sb.click(sel)
                print("[+] 已点击 NEXT 按钮")
                return True
    except:
        pass
    return False


def click_popup_submit_button(sb):
    """点击弹窗内的 시간추가 提交按钮"""
    try:
        # 查找弹窗内的 시간추가 按钮
        result = sb.execute_script("""
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                var text = btn.innerText || '';
                
                // 必须包含 시간추가
                if (!text.includes('시간추가')) continue;
                
                // 排除包含 DELETE 的按钮
                if (text.toUpperCase().includes('DELETE')) continue;
                
                // 检查位置 - 弹窗按钮 x > 200
                var rect = btn.getBoundingClientRect();
                if (rect.x > 200 && rect.width > 0) {
                    btn.click();
                    return {success: true, x: rect.x, y: rect.y};
                }
            }
            return {success: false};
        """)
        
        if result and result.get("success"):
            print(f"[+] 已点击弹窗内 시간추가 按钮 (x={result['x']:.0f})")
            return True
        
        print("[!] 未找到弹窗内的 시간추가 按钮")
        return False
    except Exception as e:
        print(f"[!] 点击提交按钮失败: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def handle_renewal_popup(sb, timeout=90):
    """
    处理续期弹窗流程:
    1. 等待弹窗和 Turnstile 出现
    2. 修复弹窗样式
    3. 点击 Turnstile checkbox
    4. Turnstile 通过后，点击弹窗内 시간추가 按钮
    5. 等待结果弹窗 (Success/Cooldown)
    """
    start_time = time.time()
  
    # ========== 阶段1: 等待弹窗和 Turnstile ==========
    print("\n[阶段1] 等待弹窗和 Turnstile...")
  
    turnstile_ready = False
    for _ in range(20):
        if check_turnstile_exists(sb):
            turnstile_ready = True
            print("[+] 检测到 Turnstile")
            break
      
        # 检查是否直接显示冷却期（无需 Turnstile）
        if check_cooldown_popup(sb):
            print("[*] 检测到冷却期弹窗")
            sb.save_screenshot("popup_fixed.png")
            return {"status": "cooldown"}
      
        time.sleep(1)
  
    if not turnstile_ready:
        print("[!] 未检测到 Turnstile")
        sb.save_screenshot("popup_fixed.png")
        return {"status": "error", "message": "未检测到 Turnstile"}
  
    # ========== 阶段2: 修复弹窗样式 ==========
    print("\n[阶段2] 修复弹窗样式...")
  
    for _ in range(3):
        result = sb.execute_script(EXPAND_POPUP_JS)
        print(f"[*] 样式修复: {result}")
        time.sleep(0.5)
  
    sb.save_screenshot("popup_fixed.png")
  
    # ========== 阶段3: 点击 Turnstile ==========
    print("\n[阶段3] 点击 Turnstile checkbox...")
  
    turnstile_solved = False
  
    for attempt in range(6):
        print(f"\n  --- 尝试 {attempt + 1}/6 ---")
      
        # 检查是否已通过
        if check_turnstile_solved(sb):
            print("[+] Turnstile 已通过!")
            turnstile_solved = True
            break
      
        # 修复样式
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
      
        # xdotool 点击
        click_turnstile_checkbox(sb)
      
        # 等待验证
        print("[*] 等待 Turnstile 验证...")
        for _ in range(8):
            time.sleep(0.5)
            if check_turnstile_solved(sb):
                print("[+] Turnstile 已通过!")
                turnstile_solved = True
                break
      
        if turnstile_solved:
            break
      
        sb.save_screenshot(f"turnstile_attempt_{attempt}.png")
  
    if not turnstile_solved:
        print("[!] Turnstile 未能通过验证")
        sb.save_screenshot("popup_fixed.png")
        return {"status": "error", "message": "Turnstile 未通过"}
  
    # ========== 阶段4: 点击弹窗内 시간추가 按钮 ==========
    print("\n[阶段4] 点击弹窗内 시간추가 按钮...")
    
    time.sleep(1)  # 等待 Turnstile 动画完成
    sb.save_screenshot("after_turnstile.png")
    
    if not click_popup_submit_button(sb):
        # 尝试用 xdotool 点击
        print("[*] 尝试 xdotool 点击提交按钮...")
        btn_coords = sb.execute_script("""
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                var text = btn.innerText || '';
                if (text.includes('시간추가') && !text.toUpperCase().includes('DELETE')) {
                    var rect = btn.getBoundingClientRect();
                    if (rect.x > 200) {
                        return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                    }
                }
            }
            return null;
        """)
        
        if btn_coords:
            window_info = sb.execute_script("""
                return {
                    screenX: window.screenX || 0,
                    screenY: window.screenY || 0,
                    outerHeight: window.outerHeight,
                    innerHeight: window.innerHeight
                };
            """)
            chrome_bar_height = window_info["outerHeight"] - window_info["innerHeight"]
            abs_x = btn_coords["x"] + window_info["screenX"]
            abs_y = btn_coords["y"] + window_info["screenY"] + chrome_bar_height
            print(f"[*] 提交按钮坐标: ({abs_x:.0f}, {abs_y:.0f})")
            xdotool_click(abs_x, abs_y)
        else:
            print("[!] 未能点击提交按钮")
            sb.save_screenshot("popup_fixed.png")
            return {"status": "error", "message": "未能点击提交按钮"}
  
    # ========== 阶段5: 等待结果 ==========
    print("\n[阶段5] 等待结果...")
  
    result_timeout = 30
    result_start = time.time()
  
    while time.time() - result_start < result_timeout:
        # 先检查是否有结果弹窗出现
        if check_result_popup_appeared(sb):
            sb.save_screenshot("popup_fixed.png")
            
            # 检查是成功还是冷却期
            if check_success_popup(sb):
                print("[+] 检测到成功弹窗 (성공!)")
                time.sleep(1)
                click_next_button(sb)
                return {"status": "success"}
            
            if check_cooldown_popup(sb):
                print("[*] 检测到冷却期弹窗 (아직...)")
                time.sleep(1)
                click_next_button(sb)
                return {"status": "cooldown"}
            
            # 有弹窗但无法判断类型
            print("[*] 检测到弹窗，等待内容加载...")
      
        time.sleep(1)
  
    print("[!] 等待结果超时")
    sb.save_screenshot("popup_fixed.png")
    return {"status": "timeout"}


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
    print("Weirdhost 自动续期 v10")
    print("=" * 60)
    print(f"[*] Cookie: {cookie_name}")
    print(f"[*] URL: {server_url}")
    print("=" * 60)

    original_expiry = "Unknown"

    try:
        with SB(uc=True, test=True, locale="ko", headless=False) as sb:
            print("\n[*] 浏览器已启动")

            # ===== 步骤1: 设置 Cookie =====
            print("\n[步骤1] 设置 Cookie")
            sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
            time.sleep(2)
            sb.add_cookie({
                "name": cookie_name, "value": cookie_value,
                "domain": DOMAIN, "path": "/"
            })
            print("[+] Cookie 已设置")

            # ===== 步骤2: 访问服务器页面 =====
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
                sb.save_screenshot("popup_fixed.png")
                sync_tg_notify_photo("popup_fixed.png",
                    "🎁 <b>Weirdhost</b>\n\n❌ Cookie 失效，请更新")
                return

            print("[+] 登录成功")

            original_expiry = get_expiry_from_page(sb)
            remaining = calculate_remaining_time(original_expiry)
            print(f"[*] 到期: {original_expiry}")
            print(f"[*] 剩余: {remaining}")

            # ===== 步骤3: 点击侧栏续期按钮 =====
            print("\n[步骤3] 点击侧栏续期按钮")
            random_delay(1.0, 2.0)

            sidebar_btn_xpath = "//button//span[contains(text(), '시간추가')]/parent::button"
            if not sb.is_element_present(sidebar_btn_xpath):
                sidebar_btn_xpath = "//button[contains(., '시간추가')]"
            
            if not sb.is_element_present(sidebar_btn_xpath):
                sb.save_screenshot("popup_fixed.png")
                sync_tg_notify_photo("popup_fixed.png",
                    f"🎁 <b>Weirdhost</b>\n\n⚠️ 未找到续期按钮\n📅 到期: {original_expiry}")
                return

            sb.click(sidebar_btn_xpath)
            print("[+] 已点击侧栏按钮，等待弹窗...")
            time.sleep(3)

            # ===== 步骤4: 处理续期弹窗 =====
            print("\n[步骤4] 处理续期弹窗")
            result = handle_renewal_popup(sb, timeout=90)
            print(f"\n[*] 处理结果: {result}")

            # ===== 步骤5: 验证续期结果 =====
            print("\n[步骤5] 验证续期结果")
            time.sleep(3)
            
            sb.uc_open_with_reconnect(server_url, reconnect_time=3)
            time.sleep(3)
            
            new_expiry = get_expiry_from_page(sb)
            new_remaining = calculate_remaining_time(new_expiry)

            print(f"[*] 原到期: {original_expiry}")
            print(f"[*] 新到期: {new_expiry}")

            original_dt = parse_expiry_to_datetime(original_expiry)
            new_dt = parse_expiry_to_datetime(new_expiry)

            # ===== 发送 TG 通知 =====
            if result["status"] == "cooldown":
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"ℹ️ 冷却期内，暂时无法续期 (아직...)\n"
                       f"📅 到期: {original_expiry}\n"
                       f"⏳ 剩余: {remaining}")
                sync_tg_notify_photo("popup_fixed.png", msg)
                return

            if original_dt and new_dt and new_dt > original_dt:
                diff_h = (new_dt - original_dt).total_seconds() / 3600
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"✅ 续期成功！(성공!)\n"
                       f"📅 新到期: {new_expiry}\n"
                       f"⏳ 剩余: {new_remaining}\n"
                       f"📝 延长了 {diff_h:.1f} 小时")
                print(f"\n[+] 成功！延长 {diff_h:.1f} 小时")
                sync_tg_notify_photo("popup_fixed.png", msg)

            elif result["status"] == "success":
                # 状态显示成功但时间未变化，可能是页面更新延迟
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"✅ 操作完成 (성공!)\n"
                       f"📅 到期: {new_expiry}\n"
                       f"⏳ 剩余: {new_remaining}\n"
                       f"📝 请确认时间是否更新")
                sync_tg_notify_photo("popup_fixed.png", msg)

            elif original_dt and new_dt and new_dt == original_dt:
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"⚠️ 到期时间未变化\n"
                       f"📅 到期: {original_expiry}\n"
                       f"⏳ 剩余: {remaining}\n"
                       f"📝 状态: {result.get('status', 'unknown')}")
                print("\n[*] 时间未变化")
                sync_tg_notify_photo("popup_fixed.png", msg)

            else:
                msg = (f"🎁 <b>Weirdhost 续订报告</b>\n\n"
                       f"⚠️ 结果待确认\n"
                       f"📅 原到期: {original_expiry}\n"
                       f"📅 新到期: {new_expiry}\n"
                       f"📝 状态: {result.get('status', 'unknown')}")
                sync_tg_notify_photo("popup_fixed.png", msg)

            # ===== 步骤6: 更新 Cookie =====
            try:
                cookies = sb.get_cookies()
                for cookie in cookies:
                    if cookie.get("name", "").startswith("remember_web"):
                        new_val = cookie.get("value", "")
                        if new_val and new_val != cookie_value:
                            new_cookie_str = f"{cookie['name']}={new_val}"
                            print(f"\n[*] 检测到新 Cookie")
                            if asyncio.run(update_github_secret("WEIRDHOST_COOKIE", new_cookie_str)):
                                print("[+] Cookie 已自动更新到 GitHub Secrets")
                            break
            except Exception as e:
                print(f"[!] Cookie 更新检查失败: {e}")

    except Exception as e:
        import traceback
        error_msg = f"🎁 <b>Weirdhost</b>\n\n❌ 脚本异常\n\n<code>{repr(e)}</code>"
        print(f"\n[!] 异常: {repr(e)}")
        traceback.print_exc()

        if os.path.exists("popup_fixed.png"):
            sync_tg_notify_photo("popup_fixed.png", error_msg)
        else:
            sync_tg_notify(error_msg)


if __name__ == "__main__":
    add_server_time()
