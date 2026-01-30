#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v6
使用 Cookie 跳过登录，Turnstile 验证绕过
"""

import os
import sys
import asyncio
import aiohttp
import base64
import re
from datetime import datetime
from typing import Optional, Dict, List
from playwright.async_api import async_playwright, Page, BrowserContext

# ============================================================
# 配置
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
COOKIE_DOMAIN = "hub.weirdhost.xyz"

# 默认 Cookie 名称（可能是 remember_web_xxx 格式）
DEFAULT_COOKIE_NAME = "remember_web"

# ============================================================
# 加密工具（用于更新 GitHub Secret）
# ============================================================
try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False


def encrypt_secret(public_key: str, secret_value: str) -> str:
    """加密 GitHub Secret"""
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


def calculate_remaining_time(expiry_str: str) -> str:
    """计算剩余时间"""
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
    """判断是否是冷却期错误"""
    keywords = [
        "can only once", "can't renew", "cannot renew", 
        "already renewed", "too early", "wait", "아직"
    ]
    return any(kw in error_text.lower() for kw in keywords)


# ============================================================
# Telegram 通知
# ============================================================
async def tg_notify(message: str):
    """发送 Telegram 文字通知"""
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
    """发送 Telegram 图片通知"""
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
    """更新 GitHub Secret"""
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    
    if not repo_token or not repository or not NACL_AVAILABLE:
        print("[GitHub] ⚠ 无法更新 Secret（缺少配置或 nacl）")
        return False
    
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            # 获取公钥
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[GitHub] ✗ 获取公钥失败: {resp.status}")
                    return False
                pk_data = await resp.json()
            
            # 加密并更新
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            payload = {"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}
            
            async with session.put(secret_url, headers=headers, json=payload) as resp:
                if resp.status in (201, 204):
                    print(f"[GitHub] ✓ Secret {secret_name} 已更新")
                    return True
                else:
                    print(f"[GitHub] ✗ 更新失败: {resp.status}")
                    return False
        except Exception as e:
            print(f"[GitHub] ✗ 异常: {e}")
            return False


# ============================================================
# Cloudflare 验证处理
# ============================================================
async def wait_for_cloudflare(page: Page, timeout: int = 120) -> bool:
    """等待 Cloudflare 验证通过"""
    print("[CF] 检测验证状态...")
    
    for i in range(timeout):
        try:
            # 检测是否有 CF 验证
            is_cf = await page.evaluate("""
                () => {
                    // Turnstile iframe
                    if (document.querySelector('iframe[src*="challenges.cloudflare.com"]')) return true;
                    if (document.querySelector('[data-sitekey]')) return true;
                    if (document.querySelector('.cf-turnstile')) return true;
                    
                    // 验证文字
                    const text = document.body?.innerText || '';
                    if (text.includes('Checking') || text.includes('Verify you are human')) return true;
                    if (text.includes('Just a moment')) return true;
                    
                    return false;
                }
            """)
            
            if not is_cf:
                print(f"[CF] ✓ 验证通过 ({i+1}s)")
                return True
            
            # 尝试点击 Turnstile
            if i == 5 or i == 15 or i == 30:
                try:
                    await page.evaluate("""
                        () => {
                            const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                            if (iframe) {
                                iframe.click();
                            }
                        }
                    """)
                except:
                    pass
            
            if i % 15 == 0 and i > 0:
                print(f"[CF] 等待中... ({i}s)")
            
            await page.wait_for_timeout(1000)
            
        except Exception as e:
            if i % 30 == 0:
                print(f"[CF] 检测异常: {e}")
            await page.wait_for_timeout(1000)
    
    print(f"[CF] ✗ 超时 ({timeout}s)")
    return False


async def handle_turnstile_in_modal(page: Page, timeout: int = 60) -> bool:
    """处理弹窗中的 Turnstile 验证"""
    print("[Turnstile] 检测弹窗验证...")
    
    for i in range(timeout):
        try:
            # 检查是否有 Turnstile 响应
            has_response = await page.evaluate("""
                () => {
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    return input && input.value && input.value.length > 20;
                }
            """)
            
            if has_response:
                print(f"[Turnstile] ✓ 验证完成 ({i+1}s)")
                return True
            
            # 尝试点击
            if i == 3 or i == 10 or i == 20:
                await page.evaluate("""
                    () => {
                        // 点击 Turnstile checkbox
                        const checkbox = document.querySelector('.cf-turnstile input[type="checkbox"]');
                        if (checkbox) checkbox.click();
                        
                        // 点击 iframe
                        const iframe = document.querySelector('.cf-turnstile iframe');
                        if (iframe) iframe.click();
                    }
                """)
            
            if i % 15 == 0 and i > 0:
                print(f"[Turnstile] 等待中... ({i}s)")
            
            await page.wait_for_timeout(1000)
            
        except:
            await page.wait_for_timeout(1000)
    
    print(f"[Turnstile] ⚠ 超时 ({timeout}s)")
    return False


# ============================================================
# 页面操作
# ============================================================
async def get_expiry_time(page: Page) -> str:
    """获取到期时间"""
    try:
        return await page.evaluate("""
            () => {
                const text = document.body.innerText;
                
                // 韩文格式
                let match = text.match(/유통기한\\s*([\\d]{4}-[\\d]{2}-[\\d]{2}(?:\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})?)/);
                if (match) return match[1].trim();
                
                // 英文格式
                match = text.match(/[Ee]xpir[yation]*[:\\s]*([\\d]{4}-[\\d]{2}-[\\d]{2})/);
                if (match) return match[1].trim();
                
                // 通用日期格式
                match = text.match(/([\\d]{4}-[\\d]{2}-[\\d]{2}\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})/);
                if (match) return match[1].trim();
                
                return 'Unknown';
            }
        """)
    except:
        return "Unknown"


async def get_server_list(page: Page) -> List[Dict]:
    """获取服务器列表"""
    try:
        servers = await page.evaluate("""
            () => {
                const servers = [];
                const links = document.querySelectorAll('a[href*="/server/"]');
                const seen = new Set();
                
                for (const link of links) {
                    const href = link.getAttribute('href');
                    const match = href.match(/\\/server\\/([a-zA-Z0-9-]+)/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        let name = link.textContent.trim();
                        if (!name) {
                            const parent = link.closest('tr, [class*="server"]');
                            if (parent) name = parent.textContent.trim().split('\\n')[0];
                        }
                        servers.push({
                            id: match[1],
                            name: name || match[1],
                            url: window.location.origin + '/server/' + match[1]
                        });
                    }
                }
                return servers;
            }
        """)
        return servers or []
    except:
        return []


async def find_and_click_renew_button(page: Page) -> bool:
    """查找并点击续期按钮"""
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


async def click_confirm_button(page: Page) -> bool:
    """点击确认按钮"""
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
                            return true;
                        }
                    }
                }
                return false;
            }
        """)
        return True
    except:
        return False


async def extract_remember_cookie(context: BrowserContext) -> tuple:
    """提取 remember_web cookie"""
    try:
        cookies = await context.cookies()
        for cookie in cookies:
            if cookie["name"].startswith("remember_web"):
                return (cookie["name"], cookie["value"])
    except:
        pass
    return (None, None)


# ============================================================
# 主流程
# ============================================================
async def renew_server(page: Page, server: Dict, context: BrowserContext) -> Dict:
    """续期单个服务器"""
    result = {
        "id": server["id"],
        "name": server["name"],
        "success": False,
        "message": "",
        "expiry_before": None,
        "expiry_after": None,
        "is_cooldown": False,
    }
    
    masked_id = mask_string(server["id"])
    print(f"\n{'=' * 50}")
    print(f"[续期] {masked_id} - {server['name'][:30]}")
    print('=' * 50)
    
    # 设置 API 响应捕获
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
        # 访问服务器页面
        print(f"[续期] 访问: {server['url']}")
        await page.goto(server["url"], timeout=60000)
        await wait_for_cloudflare(page, timeout=90)
        await page.wait_for_timeout(2000)
        
        # 检查是否需要登录
        if "/auth/login" in page.url or "/login" in page.url:
            result["message"] = "Cookie 已失效"
            print(f"[续期] ✗ {result['message']}")
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
            return result
        
        print("[续期] ✓ 已点击续期按钮")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"renew_clicked_{masked_id[:8]}.png")
        
        # 处理 Turnstile 验证
        await handle_turnstile_in_modal(page, timeout=60)
        
        # 点击确认
        await click_confirm_button(page)
        await page.wait_for_timeout(3000)
        
        # 等待 API 响应
        print("[续期] 等待 API 响应...")
        for i in range(30):
            if renew_result["captured"]:
                break
            await page.wait_for_timeout(1000)
        
        await page.screenshot(path=f"renew_result_{masked_id[:8]}.png")
        
        # 处理结果
        if renew_result["captured"]:
            status = renew_result["status"]
            body = renew_result["body"]
            
            if status in (200, 201, 204):
                result["success"] = True
                result["message"] = "续期成功"
                
                # 刷新获取新到期时间
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
            # 检查页面变化
            new_expiry = await get_expiry_time(page)
            if new_expiry != "Unknown" and new_expiry != result["expiry_before"]:
                result["success"] = True
                result["expiry_after"] = new_expiry
                result["message"] = "续期成功（通过页面判断）"
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
    """主函数"""
    # 获取配置
    cookie_value = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    cookie_name = os.environ.get("WEIRDHOST_COOKIE_NAME", DEFAULT_COOKIE_NAME).strip()
    server_url = os.environ.get("WEIRDHOST_SERVER_URL", "").strip()
    
    if not cookie_value:
        print("❌ 请设置 WEIRDHOST_COOKIE 环境变量")
        await tg_notify("❌ <b>WeirdHost 续期失败</b>\n\nWEIRDHOST_COOKIE 未设置")
        sys.exit(1)
    
    print(f"\n{'=' * 60}")
    print("WeirdHost 自动续期脚本 v6")
    print(f"{'=' * 60}")
    print(f"Cookie: {mask_string(cookie_value, 8)}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}")
    
    results = []
    
    async with async_playwright() as p:
        # 启动浏览器
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
        
        # 反检测
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
            
            # 如果指定了服务器 URL，直接续期
            if server_url:
                server = {
                    "id": server_url.split("/")[-1],
                    "name": "指定服务器",
                    "url": server_url,
                }
                result = await renew_server(page, server, context)
                results.append(result)
            else:
                # 访问首页获取服务器列表
                print("\n[服务器] 获取列表...")
                await page.goto(BASE_URL, timeout=60000)
                await wait_for_cloudflare(page, timeout=90)
                await page.wait_for_timeout(2000)
                
                # 检查登录状态
                if "/auth/login" in page.url or "/login" in page.url:
                    print("[登录] ✗ Cookie 已失效")
                    await page.screenshot(path="login_required.png")
                    await tg_notify_photo("login_required.png", "❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新 WEIRDHOST_COOKIE")
                    sys.exit(1)
                
                await page.screenshot(path="dashboard.png")
                
                servers = await get_server_list(page)
                if not servers:
                    print("[服务器] ✗ 未找到服务器")
                    await tg_notify("⚠️ <b>WeirdHost</b>\n\n未找到服务器")
                    sys.exit(0)
                
                print(f"[服务器] ✓ 找到 {len(servers)} 个")
                for s in servers:
                    print(f"  - {mask_string(s['id'])}: {s['name'][:30]}")
                
                # 续期每个服务器
                for server in servers:
                    result = await renew_server(page, server, context)
                    results.append(result)
                    
                    if len(servers) > 1:
                        await page.wait_for_timeout(3000)
            
            # 检查并更新 Cookie
            new_name, new_value = await extract_remember_cookie(context)
            if new_value and new_value != cookie_value:
                print(f"\n[Cookie] 检测到新 Cookie，尝试更新...")
                await update_github_secret("WEIRDHOST_COOKIE", new_value)
                if new_name and new_name != cookie_name:
                    await update_github_secret("WEIRDHOST_COOKIE_NAME", new_name)
            
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
    
    # 发送汇总通知
    if results:
        success_count = sum(1 for r in results if r["success"])
        cooldown_count = sum(1 for r in results if r["is_cooldown"])
        fail_count = len(results) - success_count - cooldown_count
        
        print(f"\n{'=' * 60}")
        print("执行结果汇总")
        print(f"{'=' * 60}")
        print(f"总计: {len(results)} | 成功: {success_count} | 冷却: {cooldown_count} | 失败: {fail_count}")
        
        for r in results:
            if r["success"]:
                expiry_info = ""
                if r["expiry_after"]:
                    remaining = calculate_remaining_time(r["expiry_after"])
                    expiry_info = f"\n📅 新到期: {r['expiry_after']}\n⏳ 剩余: {remaining}"
                await tg_notify(f"✅ <b>WeirdHost 续期成功</b>\n\n🖥 {mask_string(r['id'])}{expiry_info}")
            elif r["is_cooldown"]:
                remaining = calculate_remaining_time(r["expiry_before"]) if r["expiry_before"] else "未知"
                await tg_notify(f"ℹ️ <b>WeirdHost 冷却期</b>\n\n🖥 {mask_string(r['id'])}\n📅 到期: {r['expiry_before']}\n⏳ 剩余: {remaining}")
            else:
                await tg_notify(f"❌ <b>WeirdHost 续期失败</b>\n\n🖥 {mask_string(r['id'])}\n❗ {r['message']}")
        
        if success_count > 0 or cooldown_count == len(results):
            sys.exit(0)
    
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
