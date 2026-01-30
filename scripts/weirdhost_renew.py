#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v7
使用 Cookie 跳过登录
"""

import os
import sys
import asyncio
import aiohttp
import base64
from datetime import datetime
from typing import Optional, Dict, List
from playwright.async_api import async_playwright, Page, BrowserContext

# ============================================================
# 配置
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
COOKIE_DOMAIN = "hub.weirdhost.xyz"

# ============================================================
# 加密工具
# ============================================================
try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False


def encrypt_secret(public_key: str, secret_value: str) -> str:
    if not NACL_AVAILABLE:
        return ""
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


# ============================================================
# 工具函数
# ============================================================
def mask_string(s: str, show: int = 4) -> str:
    if not s or len(s) <= show * 2:
        return s or "***"
    return f"{s[:show]}****{s[-show:]}"


def parse_cookie(cookie_str: str) -> tuple:
    """解析 name=value 格式的 Cookie"""
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        return (parts[0].strip(), parts[1].strip())
    return ("remember_web", cookie_str.strip())


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
    except:
        return "计算失败"


def is_cooldown_error(error_text: str) -> bool:
    keywords = [
        "can only once", "can't renew", "cannot renew", 
        "already renewed", "too early", "wait", "아직"
    ]
    return any(kw in error_text.lower() for kw in keywords)


# ============================================================
# Telegram 通知
# ============================================================
async def tg_notify(message: str):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=30)
            )
        print("[TG] ✓ 通知已发送")
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")


async def tg_notify_photo(photo_path: str, caption: str = ""):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id or not os.path.exists(photo_path):
        return
    
    try:
        async with aiohttp.ClientSession() as session:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                if caption:
                    data.add_field("caption", caption)
                    data.add_field("parse_mode", "HTML")
                await session.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=60)
                )
        print("[TG] ✓ 图片已发送")
    except Exception as e:
        print(f"[TG] ✗ 图片发送失败: {e}")


# ============================================================
# GitHub Secret 更新
# ============================================================
async def update_github_secret(secret_name: str, secret_value: str) -> bool:
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
            payload = {"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}
            
            async with session.put(secret_url, headers=headers, json=payload) as resp:
                if resp.status in (201, 204):
                    print(f"[GitHub] ✓ Secret {secret_name} 已更新")
                    return True
                return False
        except:
            return False


# ============================================================
# Cloudflare 验证处理
# ============================================================
async def wait_for_cloudflare(page: Page, timeout: int = 120) -> bool:
    print("[CF] 检测验证状态...")
    
    for i in range(timeout):
        try:
            is_cf = await page.evaluate("""
                () => {
                    if (document.querySelector('iframe[src*="challenges.cloudflare.com"]')) return true;
                    if (document.querySelector('[data-sitekey]')) return true;
                    if (document.querySelector('.cf-turnstile')) return true;
                    const text = document.body?.innerText || '';
                    if (text.includes('Checking') || text.includes('Verify you are human')) return true;
                    if (text.includes('Just a moment')) return true;
                    return false;
                }
            """)
            
            if not is_cf:
                print(f"[CF] ✓ 验证通过 ({i+1}s)")
                return True
            
            if i == 5 or i == 15 or i == 30:
                try:
                    await page.evaluate("""
                        () => {
                            const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                            if (iframe) iframe.click();
                        }
                    """)
                except:
                    pass
            
            if i % 15 == 0 and i > 0:
                print(f"[CF] 等待中... ({i}s)")
            
            await page.wait_for_timeout(1000)
            
        except:
            await page.wait_for_timeout(1000)
    
    print(f"[CF] ✗ 超时 ({timeout}s)")
    return False


async def handle_turnstile(page: Page, timeout: int = 60) -> bool:
    print("[Turnstile] 检测验证...")
    
    for i in range(timeout):
        try:
            has_response = await page.evaluate("""
                () => {
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    return input && input.value && input.value.length > 20;
                }
            """)
            
            if has_response:
                print(f"[Turnstile] ✓ 验证完成 ({i+1}s)")
                return True
            
            if i == 3 or i == 10 or i == 20:
                await page.evaluate("""
                    () => {
                        const checkbox = document.querySelector('.cf-turnstile input[type="checkbox"]');
                        if (checkbox) checkbox.click();
                        const iframe = document.querySelector('.cf-turnstile iframe');
                        if (iframe) iframe.click();
                    }
                """)
            
            if i % 15 == 0 and i > 0:
                print(f"[Turnstile] 等待中... ({i}s)")
            
            await page.wait_for_timeout(1000)
            
        except:
            await page.wait_for_timeout(1000)
    
    return False


# ============================================================
# 页面操作
# ============================================================
async def get_expiry_time(page: Page) -> str:
    try:
        return await page.evaluate("""
            () => {
                const text = document.body.innerText;
                let match = text.match(/유통기한\\s*([\\d]{4}-[\\d]{2}-[\\d]{2}(?:\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})?)/);
                if (match) return match[1].trim();
                match = text.match(/[Ee]xpir[yation]*[:\\s]*([\\d]{4}-[\\d]{2}-[\\d]{2})/);
                if (match) return match[1].trim();
                match = text.match(/([\\d]{4}-[\\d]{2}-[\\d]{2}\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})/);
                if (match) return match[1].trim();
                return 'Unknown';
            }
        """)
    except:
        return "Unknown"


async def find_and_click_renew_button(page: Page) -> bool:
    try:
        clicked = await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button, a, [role="button"]');
                const keywords = ['시간추가', '시간연장', '연장', '갱신', 'renew', 'extend', 'add time'];
                for (const btn of buttons) {
                    const text = (btn.textContent || btn.innerText || '').toLowerCase();
                    for (const kw of keywords) {
                        if (text.includes(kw.toLowerCase())) {
                            btn.click();
                            return true;
                        }
                    }
                }
                return false;
            }
        """)
        return clicked
    except:
        return False


async def click_confirm_button(page: Page):
    try:
        await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button, [role="button"]');
                const keywords = ['확인', 'confirm', 'ok', 'yes', 'submit', '确认'];
                for (const btn of buttons) {
                    const text = (btn.textContent || '').toLowerCase();
                    for (const kw of keywords) {
                        if (text.includes(kw)) {
                            btn.click();
                            return;
                        }
                    }
                }
            }
        """)
    except:
        pass


async def extract_cookie(context: BrowserContext) -> Optional[str]:
    """提取完整 Cookie 字符串"""
    try:
        cookies = await context.cookies()
        for cookie in cookies:
            if cookie["name"].startswith("remember_web"):
                return f"{cookie['name']}={cookie['value']}"
    except:
        pass
    return None


# ============================================================
# 续期流程
# ============================================================
async def renew_server(page: Page, server_url: str) -> Dict:
    result = {
        "url": server_url,
        "success": False,
        "message": "",
        "expiry_before": None,
        "expiry_after": None,
        "is_cooldown": False,
    }
    
    server_id = server_url.split("/")[-1]
    masked_id = mask_string(server_id)
    
    print(f"\n{'=' * 50}")
    print(f"[续期] {masked_id}")
    print('=' * 50)
    
    # API 响应捕获
    renew_result = {"captured": False, "status": None, "body": None}
    
    async def capture_response(response):
        if "/renew" in response.url:
            renew_result["captured"] = True
            renew_result["status"] = response.status
            try:
                renew_result["body"] = await response.json()
            except:
                try:
                    renew_result["body"] = await response.text()
                except:
                    renew_result["body"] = None
            print(f"[API] 捕获响应: {response.status}")
    
    page.on("response", capture_response)
    
    try:
        print(f"[续期] 访问: {server_url}")
        await page.goto(server_url, timeout=60000)
        await wait_for_cloudflare(page, timeout=90)
        await page.wait_for_timeout(2000)
        
        # 检查登录状态
        if "/auth/login" in page.url or "/login" in page.url:
            result["message"] = "Cookie 已失效"
            print(f"[续期] ✗ {result['message']}")
            await page.screenshot(path="login_required.png")
            return result
        
        # 获取当前到期时间
        result["expiry_before"] = await get_expiry_time(page)
        if result["expiry_before"] != "Unknown":
            remaining = calculate_remaining_time(result["expiry_before"])
            print(f"[续期] 当前到期: {result['expiry_before']} ({remaining})")
        
        await page.screenshot(path=f"server_{masked_id[:8]}.png")
        
        # 点击续期按钮
        print("[续期] 查找续期按钮...")
        if not await find_and_click_renew_button(page):
            result["message"] = "未找到续期按钮"
            print(f"[续期] ✗ {result['message']}")
            await page.screenshot(path="no_button.png")
            return result
        
        print("[续期] ✓ 已点击续期按钮")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="renew_clicked.png")
        
        # 处理 Turnstile 验证
        await handle_turnstile(page, timeout=60)
        
        # 点击确认
        await click_confirm_button(page)
        await page.wait_for_timeout(3000)
        
        # 等待 API 响应
        print("[续期] 等待 API 响应...")
        for i in range(30):
            if renew_result["captured"]:
                break
            await page.wait_for_timeout(1000)
        
        await page.screenshot(path="renew_result.png")
        
        # 处理结果
        if renew_result["captured"]:
            status = renew_result["status"]
            body = renew_result["body"]
            
            if status in (200, 201, 204):
                result["success"] = True
                result["message"] = "续期成功"
                
                await page.wait_for_timeout(2000)
                await page.reload()
                await wait_for_cloudflare(page, timeout=30)
                await page.wait_for_timeout(2000)
                
                result["expiry_after"] = await get_expiry_time(page)
                if result["expiry_after"] != "Unknown":
                    new_remaining = calculate_remaining_time(result["expiry_after"])
                    print(f"[续期] ✓ 成功！新到期: {result['expiry_after']} ({new_remaining})")
                else:
                    print("[续期] ✓ 成功！")
                    
            elif status == 400:
                error_text = str(body) if body else ""
                if is_cooldown_error(error_text):
                    result["is_cooldown"] = True
                    result["message"] = "冷却期内"
                    print(f"[续期] ⏳ {result['message']}")
                else:
                    result["message"] = f"请求失败: {error_text[:50]}"
                    print(f"[续期] ✗ {result['message']}")
            else:
                result["message"] = f"HTTP {status}"
                print(f"[续期] ✗ {result['message']}")
        else:
            new_expiry = await get_expiry_time(page)
            if new_expiry != "Unknown" and new_expiry != result["expiry_before"]:
                result["success"] = True
                result["expiry_after"] = new_expiry
                result["message"] = "续期成功"
                print(f"[续期] ✓ {result['message']}")
            else:
                result["message"] = "未检测到 API 响应"
                print(f"[续期] ⚠ {result['message']}")
        
    except Exception as e:
        result["message"] = f"异常: {str(e)[:50]}"
        print(f"[续期] ✗ {result['message']}")
    
    finally:
        page.remove_listener("response", capture_response)
    
    return result


async def main():
    # 获取配置
    cookie_str = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_url = os.environ.get("WEIRDHOST_SERVER_URL", "").strip()
    
    if not cookie_str:
        print("❌ 请设置 WEIRDHOST_COOKIE 环境变量")
        await tg_notify("❌ <b>WeirdHost 续期失败</b>\n\nWEIRDHOST_COOKIE 未设置")
        sys.exit(1)
    
    if not server_url:
        print("❌ 请设置 WEIRDHOST_SERVER_URL 环境变量")
        await tg_notify("❌ <b>WeirdHost 续期失败</b>\n\nWEIRDHOST_SERVER_URL 未设置")
        sys.exit(1)
    
    # 解析 Cookie
    cookie_name, cookie_value = parse_cookie(cookie_str)
    
    print(f"\n{'=' * 60}")
    print("WeirdHost 自动续期脚本 v7")
    print(f"{'=' * 60}")
    print(f"Cookie: {cookie_name}={mask_string(cookie_value, 8)}")
    print(f"Server: {server_url}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}")
    
    async with async_playwright() as p:
        print("\n[浏览器] 启动中...")
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
            ]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = {runtime: {}};
        """)
        
        page = await context.new_page()
        page.set_default_timeout(60000)
        
        try:
            # 设置 Cookie
            print(f"[Cookie] 设置: {cookie_name}")
            await context.add_cookies([{
                "name": cookie_name,
                "value": cookie_value,
                "domain": COOKIE_DOMAIN,
                "path": "/",
            }])
            
            # 续期
            result = await renew_server(page, server_url)
            
            # 检查并更新 Cookie
            new_cookie = await extract_cookie(context)
            if new_cookie and new_cookie != cookie_str:
                print(f"\n[Cookie] 检测到新 Cookie，尝试更新...")
                await update_github_secret("WEIRDHOST_COOKIE", new_cookie)
            
            # 发送通知
            if result["success"]:
                expiry_info = ""
                if result["expiry_after"]:
                    remaining = calculate_remaining_time(result["expiry_after"])
                    expiry_info = f"\n📅 新到期: {result['expiry_after']}\n⏳ 剩余: {remaining}"
                await tg_notify(f"✅ <b>WeirdHost 续期成功</b>{expiry_info}")
                sys.exit(0)
            elif result["is_cooldown"]:
                remaining = calculate_remaining_time(result["expiry_before"]) if result["expiry_before"] else "未知"
                await tg_notify(f"ℹ️ <b>WeirdHost 冷却期</b>\n\n📅 到期: {result['expiry_before']}\n⏳ 剩余: {remaining}")
                sys.exit(0)
            else:
                await tg_notify_photo("renew_result.png", f"❌ <b>WeirdHost 续期失败</b>\n\n❗ {result['message']}")
                sys.exit(1)
            
        except Exception as e:
            print(f"\n[异常] {e}")
            import traceback
            traceback.print_exc()
            try:
                await page.screenshot(path="error.png")
                await tg_notify_photo("error.png", f"❌ <b>WeirdHost 异常</b>\n\n{str(e)[:100]}")
            except:
                await tg_notify(f"❌ <b>WeirdHost 异常</b>\n\n{str(e)[:100]}")
            sys.exit(1)
        
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
