#!/usr/bin/env python3
"""
WeirdHost 自动续期 v25
- 使用 undetected-chromedriver
- 浏览器走 SOCKS5 代理
"""

import os
import sys
import json
import time
import re
import urllib.parse
import urllib.request
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

def send_telegram_photo(photo_path: str, caption: str) -> bool:
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
        
        urllib.request.urlopen(req, timeout=60)
        print("[TG] ✓ 图片通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 图片发送失败: {e}")
        return False


def notify_telegram(message: str, photo_path: Optional[str] = None):
    if photo_path and os.path.exists(photo_path):
        if send_telegram_photo(photo_path, message):
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
        urllib.request.urlopen(req, timeout=30)
        print("[TG] ✓ 文本通知已发送")
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")


# ==================== 浏览器自动化 ====================

def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def run_browser_renew(cookie_str: str, server_id: str, socks_proxy: Optional[str] = None) -> Dict:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    
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
    
    driver = None
    try:
        # 配置 Chrome 选项
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=en-US")
        
        # 配置 SOCKS5 代理
        if socks_proxy:
            proxy_addr = socks_proxy.replace("socks5://", "").replace("socks5h://", "")
            options.add_argument(f"--proxy-server=socks5://{proxy_addr}")
            print(f"[浏览器] 使用 SOCKS5 代理: {proxy_addr}")
        
        # 启动浏览器
        print("[浏览器] 启动 Chrome...")
        driver = uc.Chrome(options=options, headless=True, version_main=None)
        driver.set_page_load_timeout(60)
        
        # 访问主页设置 Cookie
        print(f"[浏览器] 访问 {BASE_URL}")
        driver.get(BASE_URL)
        time.sleep(5)
        
        # 检查 CF 验证
        if "just a moment" in driver.page_source.lower():
            print("[浏览器] 等待 CF 验证...")
            time.sleep(10)
        
        # 添加 Cookie
        for name, value in cookies.items():
            try:
                driver.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
            except:
                pass
        
        # 访问服务器页面
        print(f"[浏览器] 访问 {server_url}")
        driver.get(server_url)
        time.sleep(5)
        
        # 检查 CF 验证
        if "just a moment" in driver.page_source.lower():
            print("[浏览器] 等待 CF 验证...")
            time.sleep(10)
        
        # 检查登录状态
        if "/login" in driver.current_url:
            result["cookie_expired"] = True
            result["message"] = "Cookie 已失效"
            driver.save_screenshot(SCREENSHOT_PATH)
            result["screenshot"] = SCREENSHOT_PATH
            return result
        
        # 等待页面加载
        print("[浏览器] 等待页面加载...")
        time.sleep(5)
        driver.save_screenshot("debug_before.png")
        
        # 滚动页面
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
        
        # 获取到期时间
        page_source = driver.page_source
        expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_source)
        if expiry_match:
            result["expiry"] = expiry_match.group(1)
            print(f"[浏览器] 到期时间: {result['expiry']}")
        
        # 查找续期按钮
        print("[浏览器] 查找续期按钮...")
        renew_button = None
        
        buttons = driver.find_elements(By.TAG_NAME, "button")
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
        
        if not renew_button:
            result["message"] = "未找到续期按钮"
            driver.save_screenshot(SCREENSHOT_PATH)
            result["screenshot"] = SCREENSHOT_PATH
            return result
        
        # 点击续期按钮
        print("[浏览器] 点击续期按钮...")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", renew_button)
        time.sleep(1)
        renew_button.click()
        
        # 等待结果
        print("[浏览器] 等待操作结果...")
        
        for i in range(MAX_WAIT_API):
            time.sleep(1)
            
            try:
                current_source = driver.page_source
                
                # 检查成功消息
                if any(kw in current_source for kw in ["성공", "success", "완료", "추가되었"]):
                    result["success"] = True
                    print(f"[浏览器] 检测到成功消息 ({i+1}秒)")
                    break
                
                # 检查冷却期消息
                if any(kw in current_source for kw in ["아직", "기다려", "cooldown", "wait", "시간이 남았"]):
                    result["is_cooldown"] = True
                    print(f"[浏览器] 检测到冷却期消息 ({i+1}秒)")
                    break
                
                # 检查新的到期时间
                new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', current_source)
                if new_match:
                    new_expiry = new_match.group(1)
                    if result["expiry"] and new_expiry != result["expiry"]:
                        result["success"] = True
                        result["expiry"] = new_expiry
                        print(f"[浏览器] 到期时间已更新: {new_expiry} ({i+1}秒)")
                        break
            except:
                pass
            
            if i % 5 == 4:
                print(f"[浏览器] 等待中... ({i+1}秒)")
        
        # 保存截图
        time.sleep(2)
        driver.save_screenshot(SCREENSHOT_PATH)
        result["screenshot"] = SCREENSHOT_PATH
        print("[浏览器] 已保存截图")
        
        # 判断结果
        if result["success"]:
            result["message"] = "续期成功"
            print("[浏览器] ✓ 续期成功")
            
            driver.refresh()
            time.sleep(3)
            new_source = driver.page_source
            new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', new_source)
            if new_match:
                result["expiry"] = new_match.group(1)
            driver.save_screenshot(SCREENSHOT_PATH)
            
        elif result["is_cooldown"]:
            result["message"] = "冷却期内"
            print("[浏览器] ⏳ 冷却期内")
        else:
            result["message"] = "未检测到结果"
            print("[浏览器] ✗ 未检测到结果")
        
        # 获取新 Cookie
        try:
            for cookie in driver.get_cookies():
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
        import traceback
        traceback.print_exc()
        
        if driver:
            try:
                driver.save_screenshot(SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
            except:
                pass
    
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    return result


# ==================== 主函数 ====================

def main():
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    socks_proxy = os.environ.get("SOCKS_PROXY", "").strip()
    
    if not cookie or not server_id:
        print("❌ 请设置 WEIRDHOST_COOKIE 和 WEIRDHOST_ID")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v25 (UC + SOCKS5)")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"SOCKS5 代理: {socks_proxy if socks_proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    result = run_browser_renew(cookie, server_id, socks_proxy if socks_proxy else None)
    
    # 更新 Cookie
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    screenshot = result.get("screenshot")
    
    if result["success"]:
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, screenshot)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[通知] 冷却期内，跳过通知")
        print(f"[信息] 到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新", screenshot)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", screenshot)
        sys.exit(1)


if __name__ == "__main__":
    main()
