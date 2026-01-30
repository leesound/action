#!/usr/bin/env python3
"""
WeirdHost 自动续期 v21
- 监听 API 请求判断结果
- 等待 CF Turnstile 验证
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
PAGE_TIMEOUT = 120000
RETRY_COUNT = 3
SCREENSHOT_PATH = "debug_result.png"

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


def run_browser_renew(cookie_str: str, server_id: str, proxy: Optional[str] = None) -> Dict:
    from playwright.sync_api import sync_playwright
    
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
    
    # 用于存储 API 响应
    api_response = {"status": None, "body": None}
    
    with sync_playwright() as p:
        browser_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        
        proxy_config = None
        if proxy:
            proxy_host = proxy.replace("http://", "").replace("https://", "")
            proxy_config = {"server": f"http://{proxy_host}"}
            print(f"[浏览器] 使用代理: {proxy_host}")
        
        browser = p.chromium.launch(headless=True, args=browser_args)
        
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            proxy=proxy_config
        )
        
        cookie_list = [{"name": k, "value": v, "domain": "hub.weirdhost.xyz", "path": "/"} for k, v in cookies.items()]
        if cookie_list:
            context.add_cookies(cookie_list)
        
        page = context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)
        
        # 监听 API 响应
        def handle_response(response):
            if "/renew" in response.url and "notfreeservers" in response.url:
                try:
                    api_response["status"] = response.status
                    api_response["body"] = response.json()
                    print(f"[API] 状态: {response.status}")
                    print(f"[API] 响应: {api_response['body']}")
                except:
                    pass
        
        page.on("response", handle_response)
        
        try:
            server_url = f"{BASE_URL}/server/{server_id}"
            
            for attempt in range(RETRY_COUNT):
                try:
                    print(f"[浏览器] 访问 {server_url} (尝试 {attempt + 1}/{RETRY_COUNT})")
                    page.goto(server_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    break
                except Exception as e:
                    if attempt < RETRY_COUNT - 1:
                        print(f"[浏览器] 访问失败，重试中...")
                        time.sleep(5)
                    else:
                        raise e
            
            print("[浏览器] 等待页面加载...")
            time.sleep(5)
            
            if "/login" in page.url:
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                page.screenshot(path=SCREENSHOT_PATH, full_page=True)
                result["screenshot"] = SCREENSHOT_PATH
                browser.close()
                return result
            
            # 滚动到底部
            print("[浏览器] 滚动页面...")
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 500)")
                time.sleep(0.5)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            
            # 获取到期时间
            page_content = page.content()
            expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_content)
            if expiry_match:
                result["expiry"] = expiry_match.group(1)
                print(f"[浏览器] 到期时间: {result['expiry']}")
            
            # 查找续期按钮
            renew_button = None
            selectors = [
                "button:has-text('시간추가')",
                "span:has-text('시간추가')",
            ]
            
            for selector in selectors:
                try:
                    elem = page.query_selector(selector)
                    if elem and elem.is_visible():
                        tag = elem.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "span":
                            renew_button = elem.evaluate_handle("el => el.closest('button')")
                        else:
                            renew_button = elem
                        print(f"[浏览器] 找到按钮: {selector}")
                        break
                except:
                    continue
            
            if not renew_button:
                buttons = page.query_selector_all("button")
                for btn in buttons:
                    try:
                        text = btn.inner_text().strip()
                        if "Delete" in text or "red" in (btn.get_attribute("class") or ""):
                            continue
                        if "시간추가" in text:
                            renew_button = btn
                            print(f"[浏览器] 找到按钮: {text}")
                            break
                    except:
                        continue
            
            if not renew_button:
                result["message"] = "未找到续期按钮"
                page.screenshot(path=SCREENSHOT_PATH, full_page=True)
                result["screenshot"] = SCREENSHOT_PATH
                browser.close()
                return result
            
            # 点击按钮
            print("[浏览器] 点击续期按钮...")
            renew_button.scroll_into_view_if_needed()
            time.sleep(1)
            renew_button.click()
            
            # 等待 CF Turnstile 验证和 API 响应
            print("[浏览器] 等待 Cloudflare 验证...")
            max_wait = 60  # 最多等待60秒
            for i in range(max_wait):
                time.sleep(1)
                if api_response["status"] is not None:
                    print(f"[浏览器] API 响应已收到 ({i+1}秒)")
                    break
                if i % 10 == 9:
                    print(f"[浏览器] 等待中... ({i+1}秒)")
            
            # 保存截图
            time.sleep(2)
            page.screenshot(path=SCREENSHOT_PATH, full_page=True)
            result["screenshot"] = SCREENSHOT_PATH
            print("[浏览器] 已保存结果截图")
            
            # 根据 API 响应判断结果
            if api_response["status"] == 200:
                body = api_response["body"] or {}
                if body.get("success") == True:
                    result["success"] = True
                    result["message"] = "续期成功"
                    print("[浏览器] ✓ 续期成功 (API 确认)")
                else:
                    result["success"] = True
                    result["message"] = "续期成功"
                    print("[浏览器] ✓ 续期成功 (状态码 200)")
            elif api_response["status"] == 400:
                result["is_cooldown"] = True
                body = api_response["body"] or {}
                errors = body.get("errors", [])
                if errors:
                    detail = errors[0].get("detail", "冷却期内")
                    result["message"] = detail
                else:
                    result["message"] = "冷却期内"
                print(f"[浏览器] ⏳ 冷却期: {result['message']}")
            elif api_response["status"] is None:
                # 没有收到 API 响应，可能 CF 验证失败
                result["message"] = "未收到 API 响应，可能 CF 验证失败"
                print("[浏览器] ✗ 未收到 API 响应")
            else:
                result["message"] = f"API 返回状态码: {api_response['status']}"
                print(f"[浏览器] ✗ API 错误: {api_response['status']}")
            
            # 刷新获取新的到期时间
            if result["success"]:
                print("[浏览器] 刷新页面获取新到期时间...")
                page.reload(wait_until="domcontentloaded")
                time.sleep(3)
                new_content = page.content()
                new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', new_content)
                if new_match:
                    result["expiry"] = new_match.group(1)
                    print(f"[浏览器] 新到期时间: {result['expiry']}")
                
                # 再次截图
                page.screenshot(path=SCREENSHOT_PATH, full_page=True)
            
            # 获取新 Cookie
            try:
                for cookie in context.cookies():
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
                page.screenshot(path=SCREENSHOT_PATH, full_page=True)
                result["screenshot"] = SCREENSHOT_PATH
            except:
                pass
        
        finally:
            browser.close()
    
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
    print("WeirdHost 自动续期 v21")
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
        # 冷却期不发送通知
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
