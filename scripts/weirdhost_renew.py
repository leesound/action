#!/usr/bin/env python3
"""
WeirdHost 自动续期 v37 - Playwright 版本
"""

import os
import sys
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Dict
from base64 import b64encode, b64decode

BASE_URL = "https://hub.weirdhost.xyz"
SCREENSHOT_PATH = "debug_result.png"


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
        data = json.dumps({"encrypted_value": encrypted_value, "key_id": key_data["key_id"]}).encode('utf-8')
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "WeirdHost-Renew")
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print("[GitHub] ✓ Secret 已更新")
                return True
        return False
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
        return False


def send_telegram_photo(photo_path: str, caption: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id or not os.path.exists(photo_path):
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        boundary = "----FormBoundary7MA4YWxk"
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        body = b"\r\n".join([
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="chat_id"', b"", chat_id.encode(),
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="caption"', b"", caption.encode('utf-8'),
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="parse_mode"', b"", b"HTML",
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="photo"; filename="screenshot.png"',
            b"Content-Type: image/png", b"", photo_data, f"--{boundary}--".encode(), b""
        ])
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=60)
        print("[TG] ✓ 图片通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 图片发送失败: {e}")
        return False


def send_telegram_text(message: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode('utf-8')
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=30)
        print("[TG] ✓ 文本通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")
        return False


def notify_telegram(message: str, photo_path: Optional[str] = None):
    if photo_path and os.path.exists(photo_path):
        if send_telegram_photo(photo_path, message):
            return
    send_telegram_text(message)


def parse_cookie_string(cookie_str: str) -> list:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = []
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies.append({"name": k.strip(), "value": v.strip(), "domain": "hub.weirdhost.xyz", "path": "/"})
    return cookies


def get_expiry_from_page(page) -> Optional[str]:
    try:
        content = page.content()
        m = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', content)
        if m:
            return m.group(1).strip()
    except:
        pass
    return None


def check_cooldown_message(page) -> bool:
    try:
        content = page.content()
        for kw in ["아직 서버를 갱신할 수 없습니다", "갱신할 수 없습니다", "기다려주세요"]:
            if kw in content:
                return True
    except:
        pass
    return False


def handle_turnstile(page, timeout: int = 120) -> bool:
    print("[Turnstile] 检查验证...")
    start = time.time()
    click_count = 0
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        try:
            content = page.content()
            has_verify = "Verify you are human" in content or "Verifying" in content
            
            response = page.evaluate("""() => {
                var inp = document.querySelector('input[name="cf-turnstile-response"]');
                return inp && inp.value && inp.value.length > 50;
            }""")
            
            if response:
                print(f"[Turnstile] ✓ 验证完成 ({elapsed}秒)")
                return True
            
            if not has_verify:
                time.sleep(2)
                content = page.content()
                if "Verify you are human" not in content and "Verifying" not in content:
                    print(f"[Turnstile] ✓ 无需验证 ({elapsed}秒)")
                    return True
            
            if click_count < 10 and (click_count == 0 or elapsed % 10 == 0):
                print(f"[Turnstile] {elapsed}秒 - 尝试点击...")
                try:
                    iframe = page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
                    checkbox = iframe.locator('input[type="checkbox"], .cb-i')
                    if checkbox.count() > 0:
                        checkbox.first.click(timeout=5000)
                        click_count += 1
                        print(f"[Turnstile] 点击了 checkbox")
                        time.sleep(3)
                        continue
                except:
                    pass
                try:
                    turnstile = page.locator('.cf-turnstile, [data-turnstile]')
                    if turnstile.count() > 0:
                        turnstile.first.click(timeout=5000)
                        click_count += 1
                        print(f"[Turnstile] 点击了容器")
                        time.sleep(3)
                        continue
                except:
                    pass
            
            if elapsed % 15 == 0 and elapsed > 0:
                print(f"[Turnstile] {elapsed}秒 - 等待中...")
            time.sleep(2)
        except Exception as e:
            if elapsed % 20 == 0:
                print(f"[Turnstile] {elapsed}秒 - 异常: {e}")
            time.sleep(2)
    
    print(f"[Turnstile] ✗ 超时 ({timeout}秒)")
    return False


def run_playwright_renew(cookie_str: str, server_id: str, socks_proxy: Optional[str] = None) -> Dict:
    from playwright.sync_api import sync_playwright
    
    result = {"success": False, "is_cooldown": False, "cookie_expired": False, "message": "", "expiry": "", "new_cookie": None, "screenshot": None}
    cookies = parse_cookie_string(cookie_str)
    server_url = f"{BASE_URL}/server/{server_id}"
    
    with sync_playwright() as p:
        launch_args = {"headless": False, "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]}
        if socks_proxy:
            proxy_addr = socks_proxy.replace("socks5://", "").replace("socks5h://", "")
            launch_args["proxy"] = {"server": f"socks5://{proxy_addr}"}
            print(f"[浏览器] 代理: {proxy_addr}")
        
        print("[浏览器] 启动 Chromium...")
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_cookies(cookies)
        page = context.new_page()
        
        try:
            print(f"[浏览器] 访问 {server_url}")
            page.goto(server_url, wait_until="domcontentloaded", timeout=90000)
            time.sleep(3)
            page.screenshot(path="01-server-page.png")
            print("[截图] ./01-server-page.png")
            
            if "login" in page.url:
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                page.screenshot(path=SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
                browser.close()
                return result
            
            time.sleep(5)
            original_expiry = get_expiry_from_page(page)
            if original_expiry:
                result["expiry"] = original_expiry
                print(f"[浏览器] 当前到期: {original_expiry}")
            
            # 查找并点击续期按钮
            print("[浏览器] 查找续期按钮...")
            add_button = page.locator('button:has-text("시간추가"), button:has-text("시간 추가")')
            
            if add_button.count() == 0:
                result["message"] = "未找到续期按钮"
                page.screenshot(path=SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
                browser.close()
                return result
            
            add_button.first.scroll_into_view_if_needed()
            time.sleep(1)
            
            for attempt in range(3):
                try:
                    add_button.first.click(timeout=10000)
                    print(f"[浏览器] ✓ 点击续期按钮成功 (第{attempt+1}次)")
                    break
                except Exception as e:
                    print(f"[浏览器] 点击失败 (第{attempt+1}次): {e}")
                    if attempt == 2:
                        result["message"] = f"点击按钮失败: {e}"
                        page.screenshot(path=SCREENSHOT_PATH)
                        result["screenshot"] = SCREENSHOT_PATH
                        browser.close()
                        return result
                    time.sleep(2)
            
            time.sleep(3)
            page.screenshot(path="02-after-click.png")
            print("[截图] ./02-after-click.png")
            
            # 处理 Turnstile
            content = page.content()
            if "Verify you are human" in content or "Verifying" in content or page.locator('.cf-turnstile').count() > 0:
                print("[浏览器] 检测到 Turnstile...")
                if not handle_turnstile(page, 120):
                    result["message"] = "Turnstile 验证超时"
                    page.screenshot(path=SCREENSHOT_PATH)
                    result["screenshot"] = SCREENSHOT_PATH
                    browser.close()
                    return result
                page.screenshot(path="03-turnstile-done.png")
                print("[截图] ./03-turnstile-done.png")
            
            # 等待结果
            print("[浏览器] 等待结果...")
            for i in range(60):
                time.sleep(1)
                
                if check_cooldown_message(page):
                    result["is_cooldown"] = True
                    result["message"] = "冷却期内"
                    print("[浏览器] ⏳ 冷却期")
                    break
                
                new_expiry = get_expiry_from_page(page)
                if new_expiry and original_expiry and new_expiry != original_expiry:
                    result["success"] = True
                    result["expiry"] = new_expiry
                    result["message"] = "续期成功"
                    print(f"[浏览器] ✓ 日期更新: {original_expiry} -> {new_expiry}")
                    break
                
                if i % 15 == 14:
                    print(f"[浏览器] {i+1}秒 - 等待中...")
            
            time.sleep(2)
            page.screenshot(path=SCREENSHOT_PATH)
            result["screenshot"] = SCREENSHOT_PATH
            print(f"[截图] ./{SCREENSHOT_PATH}")
            
            if not result["success"] and not result["is_cooldown"]:
                final = get_expiry_from_page(page)
                if final and original_expiry and final != original_expiry:
                    result["success"] = True
                    result["expiry"] = final
                    result["message"] = "续期成功"
                else:
                    result["message"] = result["message"] or "日期未变化"
            
            # 获取新 cookie
            try:
                for c in context.cookies():
                    if c["name"].startswith("remember_web"):
                        new_c = c["name"] + "=" + c["value"]
                        if new_c != cookie_str:
                            result["new_cookie"] = new_c
                        break
            except:
                pass
            
        except Exception as e:
            result["message"] = str(e)
            print(f"[浏览器] ✗ 异常: {e}")
            import traceback
            traceback.print_exc()
            try:
                page.screenshot(path=SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
            except:
                pass
        finally:
            browser.close()
    
    return result


def main():
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    socks_proxy = os.environ.get("SOCKS_PROXY", "").strip()
    
    if not cookie or not server_id:
        print("❌ 请设置 WEIRDHOST_COOKIE 和 WEIRDHOST_ID")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v37 (Playwright)")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {socks_proxy or '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    result = run_playwright_renew(cookie, server_id, socks_proxy or None)
    
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    screenshot_file = result.get("screenshot")
    
    if result["success"]:
        notify_telegram(f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}", screenshot_file)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[结果] 冷却期内，到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>", screenshot_file)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", screenshot_file)
        sys.exit(1)


if __name__ == "__main__":
    main()
