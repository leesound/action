#!/usr/bin/env python3
"""
WeirdHost 自动续期 v22
- 使用 SeleniumBase UC Mode 绕过 Cloudflare Turnstile
- 监听 API 请求判断结果
"""

import os
import sys
import json
import time
import re
import urllib.parse
import urllib.request
import platform
from datetime import datetime
from typing import Optional, Dict
from base64 import b64encode, b64decode

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"
SCREENSHOT_PATH = "debug_result.png"
MAX_WAIT_API = 30

# ==================== 工具函数 ====================

def calculate_remaining_time(expiry_str: str) -> str:
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
            return "已过期"
        
        days = diff.days
        hours = diff.seconds // 3600
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        return " ".join(parts) if parts else "不到1小时"
    except:
        return "计算失败"


def is_linux() -> bool:
    return platform.system().lower() == "linux"


def setup_linux_display():
    """Linux 虚拟显示"""
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            print("[Linux] 已启动虚拟显示")
            return display
        except Exception as e:
            print(f"[Linux] 虚拟显示失败: {e}")
    return None


# ==================== GitHub API ====================

def update_github_secret(secret_name: str, secret_value: str) -> bool:
    token = os.environ.get("REPO_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    
    if not token or not repo:
        return False
    
    try:
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(key_url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "WeirdHost-Renew")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            key_data = json.loads(resp.read().decode('utf-8'))
        
        from nacl import encoding, public
        public_key_bytes = b64decode(key_data["key"])
        sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
        encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
        encrypted_value = b64encode(encrypted).decode('utf-8')
        
        secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_data["key_id"]
        }).encode('utf-8')
        
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "WeirdHost-Renew")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print(f"[GitHub] ✓ Secret 已更新")
                return True
        return False
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
        return False


# ==================== Telegram 通知 ====================

def send_telegram_photo(photo_path: str, caption: str, proxy: Optional[str] = None) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    
    if not os.path.exists(photo_path):
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        
        body = []
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="chat_id"')
        body.append(b"")
        body.append(chat_id.encode())
        
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="caption"')
        body.append(b"")
        body.append(caption.encode())
        
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="parse_mode"')
        body.append(b"")
        body.append(b"HTML")
        
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="photo"; filename="screenshot.png"')
        body.append(b"Content-Type: image/png")
        body.append(b"")
        body.append(photo_data)
        
        body.append(f"--{boundary}--".encode())
        body.append(b"")
        
        body_bytes = b"\r\n".join(body)
        
        req = urllib.request.Request(url, data=body_bytes, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            )
            opener.open(req, timeout=60)
        else:
            urllib.request.urlopen(req, timeout=60)
        
        print("[TG] ✓ 图片通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 图片发送失败: {e}")
        return False


def notify_telegram(message: str, proxy: Optional[str] = None, photo_path: Optional[str] = None):
    if photo_path and os.path.exists(photo_path):
        if send_telegram_photo(photo_path, message, proxy):
            return
    
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        
        req = urllib.request.Request(url, data=data)
        
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            )
            opener.open(req, timeout=30)
        else:
            urllib.request.urlopen(req, timeout=30)
        
        print("[TG] ✓ 文本通知已发送")
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")


# ==================== SeleniumBase 自动化 ====================

def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def run_browser_renew(cookie_str: str, server_id: str, proxy: Optional[str] = None) -> Dict:
    from seleniumbase import SB
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    result = {
        "success": False,
        "is_cooldown": False,
        "cookie_expired": False,
        "message": "",
        "expiry": "",
        "new_cookie": None,
        "screenshot": None
    }
    
    cookies = parse_cookie_string(cookie_str)
    server_url = f"{BASE_URL}/server/{server_id}"
    api_result = {"status": None, "body": None}
    
    # 设置 Linux 虚拟显示
    display = setup_linux_display()
    
    try:
        # 配置代理
        proxy_arg = None
        if proxy:
            proxy_host = proxy.replace("http://", "").replace("https://", "")
            proxy_arg = proxy_host
            print(f"[浏览器] 使用代理: {proxy_host}")
        
        with SB(uc=True, test=True, locale="en", proxy=proxy_arg) as sb:
            # 先访问域名设置 Cookie
            print(f"[浏览器] 访问 {BASE_URL}")
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
            time.sleep(2)
            
            # 添加 Cookie
            for name, value in cookies.items():
                try:
                    sb.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
                except:
                    pass
            
            # 访问服务器页面
            print(f"[浏览器] 访问 {server_url}")
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            time.sleep(3)
            
            # 检查是否需要登录
            if "/login" in sb.get_current_url():
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                sb.save_screenshot(SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            # 等待页面加载
            print("[浏览器] 等待页面加载...")
            time.sleep(5)
            
            # 滚动到底部
            sb.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            
            # 获取到期时间
            page_source = sb.get_page_source()
            expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_source)
            if expiry_match:
                result["expiry"] = expiry_match.group(1)
                print(f"[浏览器] 到期时间: {result['expiry']}")
            
            # 查找续期按钮
            print("[浏览器] 查找续期按钮...")
            renew_button = None
            
            try:
                buttons = sb.find_elements("button")
                for btn in buttons:
                    try:
                        text = btn.text.strip()
                        class_attr = btn.get_attribute("class") or ""
                        if "Delete" in text or "red" in class_attr.lower():
                            continue
                        if "시간추가" in text:
                            renew_button = btn
                            print(f"[浏览器] 找到按钮: {text}")
                            break
                    except:
                        continue
            except Exception as e:
                print(f"[浏览器] 查找按钮失败: {e}")
            
            if not renew_button:
                result["message"] = "未找到续期按钮"
                sb.save_screenshot(SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            # 启用网络监听 (通过 CDP)
            print("[浏览器] 启用网络监听...")
            try:
                sb.execute_cdp_cmd("Network.enable", {})
            except:
                pass
            
            # 点击续期按钮
            print("[浏览器] 点击续期按钮...")
            sb.execute_script("arguments[0].scrollIntoView({block: 'center'});", renew_button)
            time.sleep(1)
            renew_button.click()
            
            # 等待 Cloudflare Turnstile 验证
            print("[浏览器] 等待 Cloudflare 验证...")
            cf_handled = False
            
            for i in range(MAX_WAIT_API):
                time.sleep(1)
                
                # 检测 Turnstile iframe
                if not cf_handled:
                    try:
                        page_src = sb.get_page_source().lower()
                        if "turnstile" in page_src or "challenges.cloudflare" in page_src:
                            print("[浏览器] 检测到 Turnstile，尝试处理...")
                            try:
                                sb.uc_gui_click_captcha()
                                cf_handled = True
                                print("[浏览器] ✓ Turnstile 已处理")
                            except Exception as e:
                                print(f"[浏览器] Turnstile 处理失败: {e}")
                    except:
                        pass
                
                # 检查页面变化（API 响应后页面会更新）
                try:
                    current_source = sb.get_page_source()
                    
                    # 检查成功消息
                    if any(kw in current_source for kw in ["성공", "success", "완료", "추가되었"]):
                        api_result["status"] = 200
                        print(f"[浏览器] 检测到成功消息 ({i+1}秒)")
                        break
                    
                    # 检查冷却期消息
                    if any(kw in current_source for kw in ["아직", "기다려", "cooldown", "wait"]):
                        api_result["status"] = 400
                        print(f"[浏览器] 检测到冷却期消息 ({i+1}秒)")
                        break
                    
                    # 检查新的到期时间
                    new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', current_source)
                    if new_match:
                        new_expiry = new_match.group(1)
                        if result["expiry"] and new_expiry != result["expiry"]:
                            api_result["status"] = 200
                            result["expiry"] = new_expiry
                            print(f"[浏览器] 到期时间已更新: {new_expiry} ({i+1}秒)")
                            break
                except:
                    pass
                
                if i % 5 == 4:
                    print(f"[浏览器] 等待中... ({i+1}秒)")
            
            # 保存截图
            time.sleep(2)
            sb.save_screenshot(SCREENSHOT_PATH)
            result["screenshot"] = SCREENSHOT_PATH
            print("[浏览器] 已保存截图")
            
            # 判断结果
            if api_result["status"] == 200:
                result["success"] = True
                result["message"] = "续期成功"
                print("[浏览器] ✓ 续期成功")
                
                # 刷新获取最新到期时间
                sb.refresh()
                time.sleep(3)
                new_source = sb.get_page_source()
                new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', new_source)
                if new_match:
                    result["expiry"] = new_match.group(1)
                sb.save_screenshot(SCREENSHOT_PATH)
                
            elif api_result["status"] == 400:
                result["is_cooldown"] = True
                result["message"] = "冷却期内"
                print("[浏览器] ⏳ 冷却期内")
            else:
                result["message"] = "未检测到 API 响应，可能 CF 验证失败"
                print("[浏览器] ✗ 未检测到结果")
            
            # 获取新 Cookie
            try:
                for cookie in sb.get_cookies():
                    if cookie["name"].startswith("remember_web"):
                        new_cookie_str = f"{cookie['name']}={cookie['value']}"
                        if new_cookie_str != cookie_str:
                            result["new_cookie"] = new_cookie_str
                        break
            except:
                pass
            
    except Exception as e:
        result["message"] = str(e)
        print(f"[浏览器] ✗ 异常: {e}")
        try:
            import traceback
            traceback.print_exc()
        except:
            pass
    
    finally:
        if display:
            try:
                display.stop()
            except:
                pass
    
    return result


# ==================== 主函数 ====================

def main():
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    proxy = os.environ.get("HTTP_PROXY", "").strip()
    
    if not cookie or not server_id:
        print("❌ 请设置 WEIRDHOST_COOKIE 和 WEIRDHOST_ID")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v22 (SeleniumBase UC)")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {proxy if proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    result = run_browser_renew(cookie, server_id, proxy if proxy else None)
    
    # 更新 Cookie
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    screenshot = result.get("screenshot")
    
    # 通知逻辑
    if result["success"]:
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy, screenshot)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[通知] 冷却期内，跳过通知")
        print(f"[信息] 到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新", proxy, screenshot)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", proxy, screenshot)
        sys.exit(1)


if __name__ == "__main__":
    main()
