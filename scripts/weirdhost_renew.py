#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v7
完整 Cookie 注入 + SeleniumBase UC 模式 + 代理支持
"""
import os
import sys
import time
import asyncio
import aiohttp
import base64
import platform
import socket
from datetime import datetime
from typing import Optional, Dict, List

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False
    print("⚠️ PyNaCl 未安装，无法自动更新 Secrets")

# ============================================================
# 配置
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
DEFAULT_SERVER_URL = f"{BASE_URL}/server/d341874c"
DOMAIN = "hub.weirdhost.xyz"

PROXY_HOST = "127.0.0.1"
PROXY_SOCKS_PORT = 1080

SCREENSHOT_DIR = "."


# ============================================================
# 工具函数
# ============================================================
def mask_str(s: str, show: int = 4) -> str:
    if not s or len(s) <= show * 2:
        return s or "***"
    return f"{s[:show]}****{s[-show:]}"


def format_remaining(expiry_str: str) -> str:
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                exp = datetime.strptime(expiry_str.strip(), fmt)
                diff = exp - datetime.now()
                if diff.total_seconds() < 0:
                    return "已过期"
                days = diff.days
                hours = diff.seconds // 3600
                return f"{days}天{hours}小时" if days > 0 else f"{hours}小时"
            except ValueError:
                continue
    except:
        pass
    return "未知"


def screenshot(sb, name: str) -> str:
    path = f"{SCREENSHOT_DIR}/{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[截图] {name}.png")
    except Exception as e:
        print(f"[截图] 失败: {e}")
    return path


def check_proxy() -> bool:
    try:
        sock = socket.socket()
        sock.settimeout(5)
        result = sock.connect_ex((PROXY_HOST, PROXY_SOCKS_PORT)) == 0
        sock.close()
        return result
    except:
        return False


def parse_cookies(cookie_string: str) -> List[Dict]:
    """解析 Cookie 字符串"""
    cookies = []
    if not cookie_string:
        return cookies
    
    for part in cookie_string.split("; "):
        part = part.strip()
        if not part or "=" not in part:
            continue
        idx = part.index("=")
        name = part[:idx].strip()
        value = part[idx + 1:].strip()
        if name and value:
            cookies.append({
                "name": name,
                "value": value,
                "domain": DOMAIN,
                "path": "/",
            })
    return cookies


# ============================================================
# GitHub Secrets 更新
# ============================================================
def encrypt_secret(public_key: str, secret_value: str) -> str:
    if not NACL_AVAILABLE:
        raise RuntimeError("PyNaCl 未安装")
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name: str, secret_value: str) -> bool:
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()

    if not repo_token or not repository or not NACL_AVAILABLE:
        print(f"⚠️ 跳过更新 {secret_name}")
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
                    print(f"✅ 已更新 Secret: {secret_name}")
                    return True
                return False
        except Exception as e:
            print(f"❌ 更新 Secret 出错: {e}")
            return False


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
        print("[TG] ✓ 已发送")
    except Exception as e:
        print(f"[TG] ✗ {e}")


# ============================================================
# Cloudflare 检测
# ============================================================
def is_cloudflare_challenge(sb) -> bool:
    try:
        page = sb.get_page_source().lower()
        url = sb.get_current_url().lower()
        
        if any(x in url for x in ["/server/", "dashboard"]) and "challenge" not in url:
            if any(x in page for x in ["유통기한", "시간추가", "pterodactyl"]):
                return False
        
        cf_indicators = [
            "verify you are human", "checking your browser",
            "just a moment", "challenges.cloudflare",
            "cf-turnstile", "turnstile",
        ]
        return any(ind in page for ind in cf_indicators)
    except:
        return False


def wait_for_cloudflare(sb, timeout: int = 60) -> bool:
    print("[CF] 等待验证...")
    start = time.time()
    attempt = 0
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        
        if not is_cloudflare_challenge(sb):
            print(f"[CF] ✓ 通过 ({elapsed}s)")
            return True
        
        if elapsed >= 5 and elapsed % 15 < 2:
            attempt += 1
            print(f"[CF] 尝试点击 #{attempt}...")
            try:
                sb.uc_gui_click_captcha()
            except:
                pass
            time.sleep(3)
            continue
        
        time.sleep(1)
    
    print(f"[CF] ✗ 超时")
    return False


# ============================================================
# 主类
# ============================================================
class WeirdHostRenewer:
    def __init__(self, cookies_str: str, server_url: str = DEFAULT_SERVER_URL, 
                 use_proxy: bool = False):
        self.cookies_str = cookies_str
        self.cookies = parse_cookies(cookies_str)
        self.server_url = server_url
        self.use_proxy = use_proxy
        self.display = None

    def _setup_display(self):
        if platform.system().lower() == "linux":
            try:
                from pyvirtualdisplay import Display
                self.display = Display(visible=False, size=(1920, 1080))
                self.display.start()
                print("[显示] ✓ 虚拟显示已启动")
            except Exception as e:
                print(f"[显示] ⚠ {e}")

    def _start_browser(self):
        from seleniumbase import SB
        
        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": False,
            "uc_cdp_events": True,
        }
        
        if self.use_proxy:
            sb_kwargs["proxy"] = f"socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}"
            print(f"[浏览器] 代理: socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}")
        
        return SB(**sb_kwargs)

    def inject_cookies(self, sb) -> bool:
        print(f"[Cookie] 注入 {len(self.cookies)} 个")
        for c in self.cookies:
            print(f"  - {c['name']}: {mask_str(c['value'], 8)}")
        
        try:
            # 先访问目标域名
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
            time.sleep(2)
            
            # 如果遇到 CF，先等待
            if is_cloudflare_challenge(sb):
                print("[Cookie] 首次访问遇到 CF...")
                wait_for_cloudflare(sb, timeout=60)
            
            # 注入 cookie
            for cookie in self.cookies:
                try:
                    sb.add_cookie(cookie)
                except Exception as e:
                    # 尝试 CDP 方式
                    try:
                        sb.execute_cdp_cmd("Network.setCookie", {
                            "name": cookie["name"],
                            "value": cookie["value"],
                            "domain": cookie["domain"],
                            "path": cookie["path"],
                            "secure": True,
                        })
                    except:
                        print(f"  ⚠ {cookie['name']} 注入失败")
            
            print("[Cookie] ✓ 注入完成")
            return True
        except Exception as e:
            print(f"[Cookie] ✗ {e}")
            return False

    def extract_cookies(self, sb) -> str:
        try:
            cookies = sb.get_cookies()
            parts = [f"{c['name']}={c['value']}" for c in cookies if c.get('name') and c.get('value')]
            return "; ".join(parts)
        except:
            return ""

    def get_expiry(self, sb) -> Optional[str]:
        try:
            return sb.execute_script('''
                var text = document.body.innerText;
                var patterns = [
                    /유통기한[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2}\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})/,
                    /유통기한[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2})/,
                    /만료[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2})/,
                ];
                for (var p of patterns) {
                    var m = text.match(p);
                    if (m) return m[1].trim();
                }
                return null;
            ''')
        except:
            return None

    def click_renew_button(self, sb) -> bool:
        print("[续期] 查找按钮...")
        
        result = sb.execute_script('''
            var buttons = document.querySelectorAll('button, a, [role="button"]');
            var keywords = ['시간추가', '시간연장', '연장', 'Add Time', 'Renew', 'Extend'];
            
            for (var btn of buttons) {
                var text = (btn.textContent || btn.innerText || '').trim();
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.scrollIntoView({block: "center"});
                        btn.click();
                        return {found: true, text: text};
                    }
                }
            }
            return {found: false};
        ''')
        
        if result and result.get("found"):
            print(f"[续期] ✓ 点击: {result.get('text')}")
            return True
        
        print("[续期] ✗ 未找到按钮")
        return False

    def is_logged_in(self, sb) -> bool:
        url = sb.get_current_url()
        if "/auth/login" in url or "/login" in url:
            return False
        try:
            page = sb.get_page_source().lower()
            return any(x in page for x in ["logout", "dashboard", "server", "console", "pterodactyl"])
        except:
            return False

    async def run(self) -> Dict:
        result = {
            "success": False,
            "message": "",
            "expiry_before": None,
            "expiry_after": None,
            "cookie_updated": False,
        }
        
        print(f"\n{'=' * 60}")
        print("WeirdHost 自动续期脚本 v7")
        print(f"{'=' * 60}")
        print(f"Cookie: {len(self.cookies)} 个")
        print(f"服务器: {self.server_url}")
        print(f"代理: {'启用' if self.use_proxy else '未启用'}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 60}")
        
        if not self.cookies:
            result["message"] = "Cookie 解析失败"
            return result
        
        self._setup_display()
        
        try:
            with self._start_browser() as sb:
                print("[浏览器] ✓ 已启动")
                
                # 1. 注入 Cookie
                if not self.inject_cookies(sb):
                    result["message"] = "Cookie 注入失败"
                    screenshot(sb, "error-inject")
                    return result
                
                screenshot(sb, "01-injected")
                
                # 2. 访问服务器页面
                print(f"\n[访问] {self.server_url}")
                sb.uc_open_with_reconnect(self.server_url, reconnect_time=6)
                time.sleep(3)
                screenshot(sb, "02-server")
                
                if is_cloudflare_challenge(sb):
                    if not wait_for_cloudflare(sb, timeout=60):
                        screenshot(sb, "02-cf-fail")
                        result["message"] = "CF 验证失败"
                        return result
                
                time.sleep(2)
                screenshot(sb, "03-loaded")
                
                # 检查登录
                if not self.is_logged_in(sb):
                    screenshot(sb, "03-not-login")
                    result["message"] = "Cookie 已失效"
                    await tg_notify("❌ <b>WeirdHost Cookie 已失效</b>\n\n请更新 WEIRDHOST_COOKIES")
                    return result
                
                print("[访问] ✓ 登录成功")
                
                # 3. 获取当前到期时间
                result["expiry_before"] = self.get_expiry(sb)
                if result["expiry_before"]:
                    print(f"[到期] 当前: {result['expiry_before']} ({format_remaining(result['expiry_before'])})")
                
                # 4. 点击续期
                if not self.click_renew_button(sb):
                    screenshot(sb, "04-no-btn")
                    sb.refresh()
                    time.sleep(3)
                    if not self.click_renew_button(sb):
                        result["message"] = "未找到续期按钮"
                        return result
                
                time.sleep(3)
                screenshot(sb, "04-clicked")
                
                # 5. 处理续期后 CF
                if is_cloudflare_challenge(sb):
                    print("[续期] CF 验证...")
                    screenshot(sb, "05-cf")
                    if not wait_for_cloudflare(sb, timeout=90):
                        screenshot(sb, "05-cf-fail")
                        result["message"] = "续期 CF 验证失败"
                        return result
                    screenshot(sb, "05-cf-pass")
                
                time.sleep(3)
                
                # 6. 检查结果
                sb.uc_open_with_reconnect(self.server_url, reconnect_time=4)
                time.sleep(3)
                
                if is_cloudflare_challenge(sb):
                    wait_for_cloudflare(sb, timeout=60)
                
                time.sleep(2)
                screenshot(sb, "06-result")
                
                result["expiry_after"] = self.get_expiry(sb)
                
                if result["expiry_after"]:
                    remaining = format_remaining(result["expiry_after"])
                    print(f"[到期] 新: {result['expiry_after']} ({remaining})")
                    
                    if result["expiry_before"] and result["expiry_after"] != result["expiry_before"]:
                        result["success"] = True
                        result["message"] = "续期成功"
                        print("[续期] ✓ 成功！")
                    elif not result["expiry_before"]:
                        result["success"] = True
                        result["message"] = "续期成功（无法比较）"
                    else:
                        result["success"] = True
                        result["message"] = "已执行（可能在冷却期）"
                        print(f"[续期] ⏳ {result['message']}")
                else:
                    result["success"] = True
                    result["message"] = "已执行（无法确认）"
                
                # 7. 提取并更新 Cookie
                new_cookies = self.extract_cookies(sb)
                if new_cookies and new_cookies != self.cookies_str:
                    print("[Cookie] 检测到变化，更新...")
                    updated = await update_github_secret("WEIRDHOST_COOKIES", new_cookies)
                    if updated:
                        result["cookie_updated"] = True
                
        except Exception as e:
            result["message"] = f"异常: {str(e)}"
            print(f"[错误] {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.display:
                try:
                    self.display.stop()
                except:
                    pass
        
        return result


# ============================================================
# 入口
# ============================================================
async def main():
    cookies_str = os.environ.get("WEIRDHOST_COOKIES", "").strip()
    server_url = os.environ.get("SERVER_URL", DEFAULT_SERVER_URL)
    use_proxy = os.environ.get("USE_PROXY", "").lower() == "true"
    
    if not cookies_str:
        print("❌ 请设置 WEIRDHOST_COOKIES")
        sys.exit(1)
    
    if use_proxy:
        print(f"[代理] SOCKS5: {PROXY_HOST}:{PROXY_SOCKS_PORT}")
        if check_proxy():
            print("[代理] ✓ 可达")
        else:
            print("[代理] ⚠ 不可达，直连")
            use_proxy = False
    
    renewer = WeirdHostRenewer(cookies_str, server_url, use_proxy)
    result = await renewer.run()
    
    # 通知
    if result["success"]:
        expiry_info = ""
        if result["expiry_after"]:
            remaining = format_remaining(result["expiry_after"])
            expiry_info = f"\n📅 到期: {result['expiry_after']}\n⏳ 剩余: {remaining}"
        
        cookie_info = "\n🔑 Cookie 已更新" if result["cookie_updated"] else ""
        await tg_notify(f"✅ <b>WeirdHost 续期成功</b>{expiry_info}{cookie_info}")
        print("\n🎉 完成")
        sys.exit(0)
    else:
        await tg_notify(f"❌ <b>WeirdHost 续期失败</b>\n\n❗ {result['message']}")
        print(f"\n❌ 失败: {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
