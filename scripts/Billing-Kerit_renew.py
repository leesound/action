#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Billing Kerit 自动续订脚本
功能：
1. 启动浏览器，注入 Cookie
2. 访问首页，进入续订页面
3. 检查续订按钮，处理 Turnstile 验证
4. 点击广告并完成续订
5. 发送 Telegram 通知（包含截图）
6. 自动更新 Cookie 到 GitHub Secrets
"""

import os
import sys
import time
import random
import subprocess
import asyncio
import aiohttp
import base64
from datetime import datetime
from urllib.parse import unquote, quote

try:
    from seleniumbase import SB
    SELENIUMBASE_AVAILABLE = True
except ImportError:
    SELENIUMBASE_AVAILABLE = False
    print("[ERROR] seleniumbase 未安装，请运行: pip install seleniumbase")

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

# ==================== 配置 ====================
BASE_URL = "https://billing.kerit.cloud"
SESSION_URL = f"{BASE_URL}/session"
FREE_PANEL_URL = f"{BASE_URL}/free_panel"
DOMAIN = "billing.kerit.cloud"
OUTPUT_DIR = "output/screenshots"

# ==================== 工具函数 ====================

def log(level: str, msg: str):
    """统一日志格式"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def env_or_throw(name: str) -> str:
    """获取环境变量，不存在则抛出异常"""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def env_or_default(name: str, default: str = "") -> str:
    """获取环境变量，不存在则返回默认值"""
    return os.environ.get(name, default)


def ensure_output_dir():
    """确保输出目录存在"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log("INFO", "输出目录已就绪")


def screenshot_path(name: str) -> str:
    """生成截图路径"""
    return f"{OUTPUT_DIR}/{name}.png"


def random_delay(min_sec=0.5, max_sec=2.0):
    """随机延迟"""
    time.sleep(random.uniform(min_sec, max_sec))


def parse_cookie_string(cookie_str: str):
    """解析 Cookie 字符串"""
    if not cookie_str:
        return []
    
    cookies = []
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
        
        cookies.append({"name": name, "value": value})
    
    cookie_names = [c["name"] for c in cookies]
    log("INFO", f"解析到 {len(cookies)} 个 Cookie: {', '.join(cookie_names)}")
    return cookies


# ==================== Telegram 通知 ====================

async def tg_notify(message):
    """发送 Telegram 文本通知"""
    token = env_or_default("TG_BOT_TOKEN")
    chat_id = env_or_default("TG_CHAT_ID")
    if not token or not chat_id:
        log("WARN", "Telegram 配置不完整，跳过通知")
        return
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
            log("INFO", "Telegram 文本通知已发送")
        except Exception as e:
            log("WARN", f"Telegram 发送失败: {e}")


async def tg_notify_photo(photo_path, caption=""):
    """发送 Telegram 图片通知"""
    token = env_or_default("TG_BOT_TOKEN")
    chat_id = env_or_default("TG_CHAT_ID")
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
            log("INFO", "Telegram 图片通知已发送")
        except Exception as e:
            log("WARN", f"Telegram 图片发送失败: {e}")


def sync_tg_notify(message):
    asyncio.run(tg_notify(message))


def sync_tg_notify_photo(photo_path, caption=""):
    asyncio.run(tg_notify_photo(photo_path, caption))


def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_file: str = None):
    """发送 Telegram 通知"""
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
    text_lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    caption = "\n".join(text_lines)
    
    if screenshot_file and os.path.exists(screenshot_file):
        sync_tg_notify_photo(screenshot_file, caption)
    else:
        sync_tg_notify(caption)


# ==================== GitHub Secret 更新 ====================

def encrypt_secret(public_key, secret_value):
    """加密 Secret 值"""
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret_async(secret_name, secret_value):
    """异步更新 GitHub Secret"""
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
    """更新 GitHub Secret"""
    result = asyncio.run(update_github_secret_async(secret_name, secret_value))
    if result:
        log("INFO", "GitHub Secret 已更新")
    return result


def save_cookies_for_update(sb) -> str:
    """从浏览器保存 Cookie"""
    try:
        cookies = sb.get_cookies()
        important_names = ["session_id", "cf_clearance"]
        
        filtered = []
        for c in cookies:
            name = c.get("name", "")
            if name in important_names:
                filtered.append(c)
        
        if not filtered:
            log("WARN", "未找到关键 Cookie")
            return ""
        
        cookie_string = "; ".join([f"{c['name']}={quote(str(c.get('value', '')), safe='')}" for c in filtered])
        
        cookie_file = f"{OUTPUT_DIR}/new_cookies.txt"
        with open(cookie_file, "w") as f:
            f.write(cookie_string)
        log("INFO", f"新 Cookie 已保存 ({len(filtered)} 个)")
        
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
    """检查 Turnstile 是否存在"""
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
    """获取 Turnstile 复选框坐标"""
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
            
            var turnstileContainer = document.getElementById('turnstile-container');
            if (turnstileContainer) {
                var rect = turnstileContainer.getBoundingClientRect();
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
            return null;
        """)
        return coords
    except:
        return None


def activate_browser_window():
    """激活浏览器窗口"""
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
    """使用 xdotool 点击"""
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
    """点击 Turnstile 复选框"""
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
    """处理 Turnstile 验证"""
    log("INFO", "开始处理 Turnstile 验证...")
    
    # 等待 Turnstile 出现
    for _ in range(10):
        if check_turnstile_exists(sb):
            log("INFO", "检测到 Turnstile")
            break
        time.sleep(1)
    else:
        log("WARN", "未检测到 Turnstile")
        return False
    
    # 展开弹窗样式
    for _ in range(3):
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.5)
    
    # 尝试点击并等待验证
    for attempt in range(max_attempts):
        log("INFO", f"Turnstile 尝试 {attempt + 1}/{max_attempts}")
        
        if check_turnstile_solved(sb):
            log("INFO", "✅ Turnstile 已通过!")
            return True
        
        sb.execute_script(EXPAND_POPUP_JS)
        time.sleep(0.3)
        click_turnstile_checkbox(sb)
        
        # 等待验证结果
        for _ in range(10):
            time.sleep(0.5)
            if check_turnstile_solved(sb):
                log("INFO", "✅ Turnstile 已通过!")
                return True
    
    return check_turnstile_solved(sb)


def check_renew_button_enabled(sb):
    """检查 Complete Renewal 按钮是否可用"""
    try:
        return sb.execute_script("""
            var btn = document.getElementById('renewBtn');
            if (!btn) return false;
            return !btn.disabled && !btn.hasAttribute('disabled');
        """)
    except:
        return False


def check_renewal_result(sb):
    """检查续订结果"""
    try:
        page_text = sb.get_page_source()
        if "success" in page_text.lower() or "renewed" in page_text.lower():
            return "success"
        if "Cannot exceed" in page_text or "limit" in page_text.lower():
            return "limit_reached"
        return None
    except:
        return None


# ==================== 主逻辑 ====================

def main():
    """主函数"""
    log("INFO", "=" * 50)
    log("INFO", "🚀 Billing Kerit 自动续订脚本启动")
    log("INFO", "=" * 50)
    
    if not SELENIUMBASE_AVAILABLE:
        notify_telegram(False, "初始化失败", "seleniumbase 未安装")
        sys.exit(1)
    
    ensure_output_dir()
    
    # 获取 Cookie
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
    
    final_screenshot = None
    renewal_count = "未知"
    status_text = "未知"
    
    log("INFO", "🌐 启动浏览器...")
    
    try:
        with SB(
            uc=True,
            test=True,
            locale="en",
            headless=False,
            chromium_arg="--disable-dev-shm-usage,--no-sandbox,--disable-gpu,--disable-software-rasterizer,--disable-background-timer-throttling"
        ) as sb:
            log("INFO", "浏览器已启动")
            
            try:
                # 1. 设置 Cookie
                log("INFO", "🍪 注入 Cookie...")
                sb.uc_open_with_reconnect(f"https://{DOMAIN}", reconnect_time=3)
                time.sleep(2)
                
                for c in cookies:
                    sb.add_cookie({
                        "name": c["name"],
                        "value": c["value"],
                        "domain": DOMAIN,
                        "path": "/"
                    })
                log("INFO", "Cookie 已设置")
                
                # 2. 访问首页
                log("INFO", f"🔗 访问 {SESSION_URL}...")
                sb.uc_open_with_reconnect(SESSION_URL, reconnect_time=5)
                time.sleep(3)
                
                current_url = sb.get_current_url()
                log("INFO", f"当前 URL: {current_url}")
                
                # 截图首页
                sp_home = screenshot_path("01-homepage")
                sb.save_screenshot(sp_home)
                final_screenshot = sp_home
                
                # 3. 检查登录状态
                if "/login" in current_url or "/auth" in current_url:
                    log("ERROR", "❌ Cookie 已失效，需要重新登录")
                    notify_telegram(False, "登录检查", "Cookie 已失效，请更新 Cookie", sp_home)
                    sys.exit(1)
                
                log("INFO", "✅ Cookie 有效，已登录")
                
                # 4. 进入 Free Plans 页面
                log("INFO", "🎁 进入 Free Plans 页面...")
                sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=5)
                time.sleep(3)
                
                # 截图 Free Plans 页面
                sp_free = screenshot_path("02-free-plans")
                sb.save_screenshot(sp_free)
                final_screenshot = sp_free
                
                log("INFO", f"当前 URL: {sb.get_current_url()}")
                
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
                    
                    # 保存并更新 Cookie
                    new_cookie_str = save_cookies_for_update(sb)
                    if new_cookie_str:
                        update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                    
                    notify_telegram(True, "检查完成", result_message, final_screenshot)
                else:
                    # 7. 开始续订流程
                    log("INFO", "✨ 续订按钮可用，开始续订流程...")
                    
                    # 点击续订按钮
                    sb.execute_script("""
                        var btn = document.getElementById('renewServerBtn');
                        if (btn) btn.click();
                    """)
                    log("INFO", "已点击续订按钮，等待模态框...")
                    time.sleep(2)
                    
                    # 截图模态框
                    sp_modal = screenshot_path("03-renewal-modal")
                    sb.save_screenshot(sp_modal)
                    final_screenshot = sp_modal
                    
                    # 检查模态框是否打开
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
                    turnstile_passed = handle_turnstile(sb)
                    
                    if not turnstile_passed:
                        log("WARN", "⚠️ Turnstile 验证可能未通过，继续尝试...")
                    else:
                        log("INFO", "✅ Turnstile 验证通过")
                    
                    # 截图验证后状态
                    sp_after_turnstile = screenshot_path("04-after-turnstile")
                    sb.save_screenshot(sp_after_turnstile)
                    final_screenshot = sp_after_turnstile
                    
                    # 9. 点击广告横幅
                    log("INFO", "🖱️ 点击广告横幅...")
                    
                    # 获取当前窗口数量
                    original_windows = len(sb.driver.window_handles)
                    
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
                        // 也尝试直接调用 openAdLink 函数
                        if (typeof openAdLink === 'function') {
                            openAdLink();
                        }
                    """)
                    
                    time.sleep(3)
                    
                    # 如果打开了新窗口，切换回原窗口
                    current_windows = sb.driver.window_handles
                    if len(current_windows) > original_windows:
                        log("INFO", "检测到新窗口，切换回主窗口")
                        sb.driver.switch_to.window(current_windows[0])
                        time.sleep(1)
                    
                    # 截图点击广告后
                    sp_after_ad = screenshot_path("05-after-ad")
                    sb.save_screenshot(sp_after_ad)
                    final_screenshot = sp_after_ad
                    
                    # 10. 等待 Complete Renewal 按钮可用
                    log("INFO", "⏳ 等待 Complete Renewal 按钮可用...")
                    
                    btn_enabled = False
                    for i in range(15):
                        if check_renew_button_enabled(sb):
                            btn_enabled = True
                            log("INFO", "✅ Complete Renewal 按钮已可用")
                            break
                        log("INFO", f"等待按钮可用... ({i+1}/15)")
                        time.sleep(2)
                    
                    if not btn_enabled:
                        log("WARN", "⚠️ Complete Renewal 按钮仍被禁用")
                        sp_btn_disabled = screenshot_path("06-btn-disabled")
                        sb.save_screenshot(sp_btn_disabled)
                        final_screenshot = sp_btn_disabled
                        
                        # 保存 Cookie
                        new_cookie_str = save_cookies_for_update(sb)
                        if new_cookie_str:
                            update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                        
                        notify_telegram(False, "验证未完成", "Turnstile 或广告验证未通过，请手动续订", final_screenshot)
                        sys.exit(1)
                    
                    # 11. 点击 Complete Renewal 按钮
                    log("INFO", "🎯 点击 Complete Renewal 按钮...")
                    sb.execute_script("""
                        var btn = document.getElementById('renewBtn');
                        if (btn && !btn.disabled) {
                            btn.click();
                        }
                        // 也尝试调用 submitRenewal 函数
                        if (typeof submitRenewal === 'function') {
                            submitRenewal();
                        }
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
                        sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=3)
                        time.sleep(2)
                        
                        new_renewal_count = sb.execute_script("""
                            var el = document.getElementById('renewal-count');
                            return el ? el.textContent : '未知';
                        """) or "未知"
                        
                        new_status_text = sb.execute_script("""
                            var el = document.getElementById('renewal-status-text');
                            return el ? el.textContent : '未知';
                        """) or "未知"
                        
                        # 最终状态截图
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
                    elif result == "success" or (renewal_count != new_renewal_count):
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
                
                # 尝试截图
                try:
                    sp_error = screenshot_path("99-error")
                    sb.save_screenshot(sp_error)
                    final_screenshot = sp_error
                except:
                    pass
                
                # 尝试保存 Cookie
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
                    final_screenshot if final_screenshot and os.path.exists(final_screenshot) else None
                )
                sys.exit(1)
    
    except Exception as e:
        log("ERROR", f"💥 浏览器启动失败: {e}")
        import traceback
        traceback.print_exc()
        notify_telegram(False, "启动失败", f"浏览器启动失败: {str(e)[:100]}")
        sys.exit(1)
    
    log("INFO", "🔒 浏览器已关闭")


if __name__ == "__main__":
    main()
