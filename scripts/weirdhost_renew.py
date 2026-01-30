#!/usr/bin/env python3
"""
WeirdHost 自动续期 v17
- 使用 Playwright 浏览器自动化
- 支持自动更新 Cookie 到 GitHub Secrets
"""

import os
import sys
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Dict
from base64 import b64encode, b64decode

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"

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
    """更新 GitHub Secret"""
    token = os.environ.get("REPO_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    
    if not token or not repo:
        print("[GitHub] 未配置 REPO_TOKEN 或 GITHUB_REPOSITORY，跳过更新")
        return False
    
    try:
        print(f"[GitHub] 获取仓库 {repo} 的公钥...")
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(key_url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "WeirdHost-Renew-Script")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            key_data = json.loads(resp.read().decode('utf-8'))
        
        public_key = key_data["key"]
        key_id = key_data["key_id"]
        
        try:
            from nacl import encoding, public
            
            public_key_bytes = b64decode(public_key)
            sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')
        except ImportError:
            print("[GitHub] ⚠ 需要 PyNaCl 库")
            return False
        
        print(f"[GitHub] 更新 Secret: {secret_name}...")
        secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_id
        }).encode('utf-8')
        
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "WeirdHost-Renew-Script")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print(f"[GitHub] ✓ Secret {secret_name} 已更新")
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
    """解析 Cookie 字符串为字典"""
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def run_browser_renew(cookie_str: str, server_id: str, proxy: Optional[str] = None) -> Dict:
    """使用 Playwright 进行续期"""
    from playwright.sync_api import sync_playwright
    
    result = {
        "success": False,
        "is_cooldown": False,
        "cookie_expired": False,
        "message": "",
        "expiry": "",
        "new_cookie": None
    }
    
    # 解析 Cookie
    cookies = parse_cookie_string(cookie_str)
    
    with sync_playwright() as p:
        # 启动浏览器
        browser_args = []
        if proxy:
            browser_args.append(f"--proxy-server={proxy}")
        
        browser = p.chromium.launch(
            headless=True,
            args=browser_args
        )
        
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
        
        try:
            # 访问服务器页面
            server_url = f"{BASE_URL}/server/{server_id}"
            print(f"[浏览器] 访问 {server_url}")
            
            page.goto(server_url, wait_until="networkidle", timeout=60000)
            time.sleep(2)
            
            # 检查是否需要登录
            if "/login" in page.url or "login" in page.url.lower():
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效，需要重新登录"
                print("[浏览器] ✗ Cookie 已失效")
                browser.close()
                return result
            
            # 截图调试
            page.screenshot(path="debug_before_renew.png")
            print("[浏览器] 已保存截图: debug_before_renew.png")
            
            # 获取到期时间
            try:
                expiry_elem = page.query_selector("text=Renewal Date") or page.query_selector("text=到期")
                if expiry_elem:
                    parent = expiry_elem.evaluate_handle("el => el.parentElement || el.closest('div')")
                    text = parent.inner_text() if parent else ""
                    # 尝试提取日期
                    import re
                    date_match = re.search(r'\d{4}-\d{2}-\d{2}', text)
                    if date_match:
                        result["expiry"] = date_match.group()
                        print(f"[浏览器] 到期时间: {result['expiry']}")
            except Exception as e:
                print(f"[浏览器] 获取到期时间失败: {e}")
            
            # 查找续期按钮
            renew_button = None
            
            # 尝试多种选择器
            selectors = [
                "button:has-text('Renew')",
                "button:has-text('renew')",
                "button:has-text('续期')",
                "button:has-text('갱신')",
                "a:has-text('Renew')",
                "[data-action='renew']",
                ".renew-button",
                "button.btn-primary:has-text('Renew')",
            ]
            
            for selector in selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        renew_button = btn
                        print(f"[浏览器] 找到续期按钮: {selector}")
                        break
                except:
                    continue
            
            if not renew_button:
                # 尝试通过文本内容查找
                buttons = page.query_selector_all("button")
                for btn in buttons:
                    try:
                        text = btn.inner_text().lower()
                        if "renew" in text or "续期" in text or "갱신" in text:
                            renew_button = btn
                            print(f"[浏览器] 通过文本找到按钮: {text}")
                            break
                    except:
                        continue
            
            if not renew_button:
                result["message"] = "未找到续期按钮"
                print("[浏览器] ✗ 未找到续期按钮")
                page.screenshot(path="debug_no_button.png")
                browser.close()
                return result
            
            # 点击续期按钮
            print("[浏览器] 点击续期按钮...")
            renew_button.click()
            time.sleep(3)
            
            # 截图
            page.screenshot(path="debug_after_click.png")
            print("[浏览器] 已保存截图: debug_after_click.png")
            
            # 检查结果
            page_content = page.content().lower()
            
            # 检查是否在冷却期
            cooldown_keywords = ["아직", "갱신할 수 없습니다", "cooldown", "wait", "too early", "not yet"]
            for keyword in cooldown_keywords:
                if keyword in page_content:
                    result["is_cooldown"] = True
                    result["message"] = "冷却期内，无法续期"
                    print("[浏览器] ⏳ 冷却期内")
                    break
            
            # 检查是否成功
            success_keywords = ["success", "성공", "renewed", "续期成功", "갱신되었습니다"]
            for keyword in success_keywords:
                if keyword in page_content:
                    result["success"] = True
                    result["message"] = "续期成功"
                    print("[浏览器] ✓ 续期成功")
                    break
            
            # 如果没有明确结果，检查页面变化
            if not result["success"] and not result["is_cooldown"]:
                # 刷新页面获取新的到期时间
                page.reload(wait_until="networkidle")
                time.sleep(2)
                
                try:
                    import re
                    new_content = page.content()
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', new_content)
                    if date_match:
                        new_expiry = date_match.group(1)
                        if result["expiry"] and new_expiry > result["expiry"]:
                            result["success"] = True
                            result["expiry"] = new_expiry
                            result["message"] = "续期成功"
                            print(f"[浏览器] ✓ 续期成功，新到期: {new_expiry}")
                        elif new_expiry:
                            result["expiry"] = new_expiry
                except:
                    pass
            
            # 获取新 Cookie
            try:
                new_cookies = context.cookies()
                for cookie in new_cookies:
                    if cookie["name"].startswith("remember_web"):
                        new_cookie_str = f"{cookie['name']}={cookie['value']}"
                        if new_cookie_str != cookie_str:
                            result["new_cookie"] = new_cookie_str
                            print("[浏览器] 检测到新 Cookie")
                        break
            except:
                pass
            
        except Exception as e:
            result["message"] = str(e)
            print(f"[浏览器] ✗ 异常: {e}")
            try:
                page.screenshot(path="debug_error.png")
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
    
    if not cookie:
        print("❌ 请设置 WEIRDHOST_COOKIE")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nCookie 未设置")
        sys.exit(1)
    
    if not server_id:
        print("❌ 请设置 WEIRDHOST_ID")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nServer ID 未设置")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v17 (Playwright)")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {proxy if proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    # 运行浏览器续期
    result = run_browser_renew(cookie, server_id, proxy if proxy else None)
    
    # 检查是否需要更新 Cookie
    if result.get("new_cookie"):
        print("[Cookie] 检测到新 Cookie，尝试更新...")
        if update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"]):
            notify_telegram("🔄 <b>Cookie 已自动更新</b>\n\nGitHub Secret 已更新为新 Cookie", proxy)
    
    # 发送通知
    if result["success"]:
        remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy)
        sys.exit(0)
    elif result["is_cooldown"]:
        remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
        msg = f"ℹ️ <b>WeirdHost 冷却期</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy)
        sys.exit(0)
    elif result["cookie_expired"]:
        msg = "❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新 WEIRDHOST_COOKIE Secret"
        notify_telegram(msg, proxy)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n❗ {result['message']}", proxy)
        sys.exit(1)


if __name__ == "__main__":
    main()
