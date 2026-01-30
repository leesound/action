#!/usr/bin/env python3
"""
WeirdHost 自动续期 v19
- 优化超时和重试
- 改用 domcontentloaded
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
PAGE_TIMEOUT = 120000  # 2分钟
RETRY_COUNT = 3

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

def notify_telegram(message: str, proxy: Optional[str] = None):
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
        
        print("[TG] ✓ 通知已发送")
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
        "new_cookie": None
    }
    
    cookies = parse_cookie_string(cookie_str)
    
    with sync_playwright() as p:
        # 浏览器配置
        browser_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        
        # 代理配置
        proxy_config = None
        if proxy:
            # 解析代理 URL
            if proxy.startswith("http://"):
                proxy_host = proxy[7:]
            elif proxy.startswith("https://"):
                proxy_host = proxy[8:]
            else:
                proxy_host = proxy
            
            proxy_config = {"server": f"http://{proxy_host}"}
            print(f"[浏览器] 使用代理: {proxy_host}")
        
        browser = p.chromium.launch(
            headless=True,
            args=browser_args
        )
        
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            proxy=proxy_config
        )
        
        # 设置 Cookie
        cookie_list = []
        for name, value in cookies.items():
            cookie_list.append({
                "name": name,
                "value": value,
                "domain": "hub.weirdhost.xyz",
                "path": "/"
            })
        
        if cookie_list:
            context.add_cookies(cookie_list)
        
        page = context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)
        
        try:
            server_url = f"{BASE_URL}/server/{server_id}"
            
            # 重试机制
            for attempt in range(RETRY_COUNT):
                try:
                    print(f"[浏览器] 访问 {server_url} (尝试 {attempt + 1}/{RETRY_COUNT})")
                    page.goto(server_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    break
                except Exception as e:
                    if attempt < RETRY_COUNT - 1:
                        print(f"[浏览器] 访问失败，重试中... ({e})")
                        time.sleep(5)
                    else:
                        raise e
            
            # 等待页面加载
            print("[浏览器] 等待页面加载...")
            time.sleep(5)
            
            # 检查是否需要登录
            if "/login" in page.url:
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                print("[浏览器] ✗ Cookie 已失效")
                browser.close()
                return result
            
            # 等待内容加载
            try:
                page.wait_for_selector("button", timeout=30000)
            except:
                pass
            
            # 滚动到底部
            print("[浏览器] 滚动页面...")
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 500)")
                time.sleep(0.5)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            
            # 截图
            page.screenshot(path="debug_before_renew.png", full_page=True)
            print("[浏览器] 已保存截图")
            
            # 获取到期时间
            page_content = page.content()
            expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_content)
            if expiry_match:
                result["expiry"] = expiry_match.group(1)
                print(f"[浏览器] 到期时间: {result['expiry']}")
            
            # 查找续期按钮
            renew_button = None
            
            # 方法1: 直接选择器
            selectors = [
                "button:has-text('시간추가')",
                "button:has-text('Renew')",
                "span:has-text('시간추가')",
            ]
            
            for selector in selectors:
                try:
                    elem = page.query_selector(selector)
                    if elem and elem.is_visible():
                        # 如果是 span，找父级 button
                        tag = elem.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "span":
                            renew_button = elem.evaluate_handle("el => el.closest('button')")
                        else:
                            renew_button = elem
                        print(f"[浏览器] 找到按钮: {selector}")
                        break
                except:
                    continue
            
            # 方法2: 遍历按钮
            if not renew_button:
                buttons = page.query_selector_all("button")
                for btn in buttons:
                    try:
                        text = btn.inner_text().strip()
                        class_attr = btn.get_attribute("class") or ""
                        color_attr = btn.get_attribute("color") or ""
                        
                        # 跳过删除按钮
                        if "red" in class_attr or color_attr == "red" or "Delete" in text:
                            continue
                        
                        if "시간추가" in text or "시간" in text:
                            renew_button = btn
                            print(f"[浏览器] 找到按钮: {text}")
                            break
                    except:
                        continue
            
            if not renew_button:
                result["message"] = "未找到续期按钮"
                print("[浏览器] ✗ 未找到续期按钮")
                
                # 打印页面上所有按钮用于调试
                buttons = page.query_selector_all("button")
                print(f"[调试] 页面上共有 {len(buttons)} 个按钮:")
                for i, btn in enumerate(buttons):
                    try:
                        text = btn.inner_text().strip()[:50]
                        print(f"  [{i}] {text}")
                    except:
                        pass
                
                browser.close()
                return result
            
            # 点击按钮
            print("[浏览器] 点击续期按钮...")
            renew_button.scroll_into_view_if_needed()
            time.sleep(1)
            renew_button.click()
            time.sleep(5)
            
            # 截图
            page.screenshot(path="debug_after_click.png", full_page=True)
            
            # 检查结果
            new_content = page.content().lower()
            
            # 冷却期检查
            if any(kw in new_content for kw in ["아직", "cooldown", "wait", "기다려"]):
                result["is_cooldown"] = True
                result["message"] = "冷却期内"
                print("[浏览器] ⏳ 冷却期内")
            # 成功检查
            elif any(kw in new_content for kw in ["success", "성공", "완료", "추가되었"]):
                result["success"] = True
                result["message"] = "续期成功"
                print("[浏览器] ✓ 续期成功")
            else:
                # 刷新检查时间变化
                page.reload(wait_until="domcontentloaded")
                time.sleep(3)
                new_content = page.content()
                new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', new_content)
                if new_match:
                    new_expiry = new_match.group(1)
                    if result["expiry"] and new_expiry > result["expiry"]:
                        result["success"] = True
                        result["expiry"] = new_expiry
                        result["message"] = "续期成功"
                        print(f"[浏览器] ✓ 续期成功，新到期: {new_expiry}")
                    else:
                        result["is_cooldown"] = True
                        result["message"] = "可能在冷却期"
                        result["expiry"] = new_expiry
            
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
                page.screenshot(path="debug_error.png", full_page=True)
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
    print("WeirdHost 自动续期 v19")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {proxy if proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    result = run_browser_renew(cookie, server_id, proxy if proxy else None)
    
    # 更新 Cookie
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    # 发送通知
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    
    if result["success"]:
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy)
        sys.exit(0)
    elif result["is_cooldown"]:
        msg = f"ℹ️ <b>WeirdHost 冷却期</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy)
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新", proxy)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", proxy)
        sys.exit(1)


if __name__ == "__main__":
    main()
