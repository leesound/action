明白了！截图显示是 **reCAPTCHA v2 图片验证**（选择汽车），这个无法自动绑过。最好的方案是使用 **Cookie 登录**，跳过登录流程。

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v6
使用 Cookie 登录，绕过 reCAPTCHA
支持自动更新 Cookie 到 GitHub Secrets
"""

import os
import sys
import time
import asyncio
import aiohttp
import platform
import base64
import json
from datetime import datetime
from typing import Optional, Dict, List
from urllib.parse import quote

# ============================================================
# 配置
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/"

PROXY_HOST = "127.0.0.1"
PROXY_SOCKS_PORT = 1080

SCREENSHOT_DIR = "."

# Cookie 名称
REMEMBER_COOKIE_NAME = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"


# ============================================================
# 工具函数
# ============================================================
def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 4:
        return f"{local[0]}***@{domain}"
    return f"{local[:2]}****{local[-2:]}@{domain}"


def mask_id(s: str, show: int = 4) -> str:
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
                if days > 0:
                    return f"{days}天{hours}小时"
                return f"{hours}小时"
            except ValueError:
                continue
    except:
        pass
    return "未知"


def screenshot(sb, name: str) -> str:
    """保存截图"""
    path = f"{SCREENSHOT_DIR}/{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[截图] {name}.png")
    except Exception as e:
        print(f"[截图] 失败: {e}")
    return path


def is_cloudflare_challenge(sb) -> bool:
    """检测是否在 Cloudflare 验证页面"""
    try:
        page = sb.get_page_source().lower()
        url = sb.get_current_url().lower()
      
        # 已通过的特征
        passed_indicators = [
            'name="username"' in page or 'name="password"' in page,
            "/server/" in url,
            "dashboard" in page and "/auth/" not in url,
        ]
      
        if any(passed_indicators):
            return False
      
        # 验证页面特征
        cf_indicators = [
            "verify you are human",
            "checking your browser",
            "just a moment",
            "challenges.cloudflare",
            "cf-turnstile",
        ]
      
        return any(ind in page for ind in cf_indicators)
    except:
        return False


def wait_for_cloudflare(sb, timeout: int = 60) -> bool:
    """等待 Cloudflare 验证通过"""
    print("[CF] 检测验证状态...")
  
    start = time.time()
    clicked = False
  
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
      
        if not is_cloudflare_challenge(sb):
            print(f"[CF] ✓ 验证通过 ({elapsed}s)")
            return True
      
        if not clicked and elapsed >= 3:
            print(f"[CF] 尝试点击验证框...")
            try:
                sb.uc_gui_click_captcha()
                clicked = True
            except:
                clicked = True
            time.sleep(3)
            continue
      
        if elapsed % 15 == 0 and elapsed > 0:
            print(f"[CF] 等待中... ({elapsed}s)")
      
        time.sleep(1)
  
    print(f"[CF] ✗ 超时 ({timeout}s)")
    return False


def parse_cookies(cookie_str: str) -> Dict[str, str]:
    """解析 Cookie 字符串"""
    cookies = {}
    if not cookie_str:
        return cookies
  
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
  
    return cookies


# ============================================================
# GitHub Secrets 更新
# ============================================================
def update_github_secret(secret_name: str, secret_value: str) -> bool:
    """更新 GitHub Secret"""
    import urllib.request
    import urllib.error
  
    token = os.environ.get("REPO_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
  
    if not token or not repo:
        print("[GitHub] ⚠ 未配置 REPO_TOKEN 或 GITHUB_REPOSITORY")
        return False
  
    try:
        # 获取公钥
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(key_url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
      
        with urllib.request.urlopen(req, timeout=30) as resp:
            key_data = json.loads(resp.read().decode())
      
        public_key = key_data["key"]
        key_id = key_data["key_id"]
      
        # 加密 secret
        from nacl import encoding, public
      
        public_key_bytes = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key_bytes)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = base64.b64encode(encrypted).decode("utf-8")
      
        # 更新 secret
        secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_id
        }).encode()
      
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
      
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in [201, 204]:
                print(f"[GitHub] ✓ Secret {secret_name} 已更新")
                return True
      
        return False
      
    except ImportError:
        print("[GitHub] ⚠ 需要安装 pynacl: pip install pynacl")
        return False
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
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
# 主类
# ============================================================
class WeirdHostRenewer:
    def __init__(self, email: str, password: str, cookies: str = "", use_proxy: bool = False):
        self.email = email
        self.password = password
        self.cookies = parse_cookies(cookies)
        self.use_proxy = use_proxy
        self.display = None

    def _setup_display(self):
        """Linux 下设置虚拟显示"""
        if platform.system().lower() == "linux":
            try:
                from pyvirtualdisplay import Display
                self.display = Display(visible=False, size=(1920, 1080))
                self.display.start()
                print("[显示] ✓ 虚拟显示已启动")
            except Exception as e:
                print(f"[显示] ⚠ {e}")

    def _start_browser(self):
        """启动浏览器"""
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
        """注入 Cookie"""
        if not self.cookies:
            print("[Cookie] 未提供 Cookie")
            return False
      
        print("[Cookie] 注入中...")
      
        # 先访问网站以设置域
        sb.uc_open_with_reconnect(BASE_URL, reconnect_time=6)
        time.sleep(3)
      
        # 等待 Cloudflare
        wait_for_cloudflare(sb, timeout=60)
      
        # 注入 cookies
        for name, value in self.cookies.items():
            try:
                sb.add_cookie({
                    "name": name,
                    "value": value,
                    "domain": "hub.weirdhost.xyz",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True if "session" in name.lower() or "remember" in name.lower() else False,
                })
                print(f"[Cookie] ✓ {name[:20]}...")
            except Exception as e:
                print(f"[Cookie] ✗ {name}: {e}")
      
        return True

    def check_login_status(self, sb) -> bool:
        """检查登录状态"""
        print("[登录] 检查状态...")
      
        # 刷新页面
        sb.uc_open_with_reconnect(DASHBOARD_URL, reconnect_time=4)
        time.sleep(3)
      
        # 等待 Cloudflare
        wait_for_cloudflare(sb, timeout=60)
        time.sleep(2)
      
        screenshot(sb, "01-check-login")
      
        current_url = sb.get_current_url()
        page_source = sb.get_page_source().lower()
      
        # 检查是否在登录页
        if "/auth/login" in current_url:
            print("[登录] ✗ 未登录（在登录页）")
            return False
      
        # 检查页面内容
        if any(kw in page_source for kw in ["logout", "로그아웃", "dashboard", "server"]):
            print("[登录] ✓ 已登录")
            return True
      
        print("[登录] ⚠ 状态不确定")
        return False

    def extract_and_save_cookies(self, sb):
        """提取并保存新的 Cookie"""
        print("[Cookie] 提取新 Cookie...")
      
        try:
            all_cookies = sb.get_cookies()
          
            # 找到 remember cookie
            remember_cookie = None
            for cookie in all_cookies:
                if REMEMBER_COOKIE_NAME in cookie.get("name", ""):
                    remember_cookie = cookie
                    break
          
            if remember_cookie:
                new_cookie_str = f"{remember_cookie['name']}={remember_cookie['value']}"
                print(f"[Cookie] ✓ 提取成功: {remember_cookie['name'][:30]}...")
              
                # 更新 GitHub Secret
                if update_github_secret("WEIRDHOST_COOKIES", new_cookie_str):
                    print("[Cookie] ✓ 已更新到 GitHub Secrets")
              
                return new_cookie_str
            else:
                print("[Cookie] ⚠ 未找到 remember cookie")
              
                # 打印所有 cookie 名称用于调试
                cookie_names = [c.get("name", "") for c in all_cookies]
                print(f"[Cookie] 可用: {cookie_names}")
              
        except Exception as e:
            print(f"[Cookie] ✗ 提取失败: {e}")
      
        return None

    def get_servers(self, sb) -> List[Dict]:
        """获取服务器列表"""
        print("\n[服务器] 获取列表...")
      
        sb.uc_open_with_reconnect(DASHBOARD_URL, reconnect_time=4)
        time.sleep(3)
        wait_for_cloudflare(sb, timeout=60)
        time.sleep(2)
        screenshot(sb, "02-dashboard")
      
        # 从页面提取服务器
        servers = sb.execute_script('''
            var servers = [];
            var links = document.querySelectorAll('a[href*="/server/"]');
            var seen = new Set();
          
            for (var link of links) {
                var href = link.getAttribute('href');
                var match = href.match(/\\/server\\/([a-zA-Z0-9-]+)/);
                if (match && !seen.has(match[1])) {
                    seen.add(match[1]);
                    var name = link.textContent.trim();
                    if (!name) {
                        var parent = link.closest('tr, .server-item, [class*="server"]');
                        if (parent) {
                            name = parent.textContent.trim().split('\\n')[0];
                        }
                    }
                    servers.push({
                        id: match[1],
                        name: name || match[1],
                        url: window.location.origin + '/server/' + match[1]
                    });
                }
            }
            return servers;
        ''')
      
        if servers:
            print(f"[服务器] ✓ 找到 {len(servers)} 个")
            for s in servers:
                print(f"  - {mask_id(s['id'])}: {s['name'][:30]}")
        else:
            print("[服务器] ✗ 未找到")
            screenshot(sb, "02-no-servers")
      
        return servers or []

    def get_expiry(self, sb) -> Optional[str]:
        """获取到期时间"""
        try:
            return sb.execute_script('''
                var text = document.body.innerText;
                var patterns = [
                    /유통기한[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2}\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})/,
                    /유통기한[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2})/,
                    /만료[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2})/,
                    /expir[yation]*[\\s:]*([\\d]{4}-[\\d]{2}-[\\d]{2})/i,
                    /([\\d]{4}-[\\d]{2}-[\\d]{2}\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})/
                ];
                for (var p of patterns) {
                    var m = text.match(p);
                    if (m) return m[1].trim();
                }
                return null;
            ''')
        except:
            return None

    def renew_server(self, sb, server: Dict, index: int) -> Dict:
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
      
        masked_id = mask_id(server["id"])
        prefix = f"{index:02d}"
      
        print(f"\n{'=' * 50}")
        print(f"[续期] {masked_id}")
        print('=' * 50)
      
        # 访问服务器页面
        sb.uc_open_with_reconnect(server["url"], reconnect_time=4)
        time.sleep(3)
        wait_for_cloudflare(sb, timeout=60)
        time.sleep(2)
        screenshot(sb, f"{prefix}-server-page")
      
        # 获取当前到期时间
        result["expiry_before"] = self.get_expiry(sb)
        if result["expiry_before"]:
            remaining = format_remaining(result["expiry_before"])
            print(f"[续期] 当前到期: {result['expiry_before']} ({remaining})")
      
        # 注入请求拦截
        sb.execute_script('''
            window.__renewResult = null;
            (function() {
                var origXHR = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(method, url) {
                    this._url = url;
                    this.addEventListener('load', function() {
                        if (this._url && this._url.includes('renew')) {
                            window.__renewResult = {
                                status: this.status,
                                response: this.responseText
                            };
                        }
                    });
                    return origXHR.apply(this, arguments);
                };
              
                var origFetch = window.fetch;
                window.fetch = function(url, opts) {
                    return origFetch.apply(this, arguments).then(function(response) {
                        if (url && url.toString().includes('renew')) {
                            response.clone().text().then(function(text) {
                                window.__renewResult = {
                                    status: response.status,
                                    response: text
                                };
                            });
                        }
                        return response;
                    });
                };
            })();
        ''')
      
        # 查找并点击续期按钮
        print("[续期] 查找续期按钮...")
        clicked = sb.execute_script('''
            var btns = document.querySelectorAll('button, a, [role="button"], span');
            var keywords = ['시간추가', '시간연장', '연장', '갱신', 'renew', 'extend', 'add time', '续期', '시간 추가'];
          
            for (var btn of btns) {
                var text = (btn.textContent || btn.innerText || '').toLowerCase().trim();
                for (var kw of keywords) {
                    if (text.includes(kw.toLowerCase())) {
                        console.log('Found button:', text);
                        btn.click();
                        return text;
                    }
                }
            }
            return null;
        ''')
      
        if not clicked:
            print("[续期] 尝试查找其他按钮...")
            # 打印页面上所有按钮文字
            buttons = sb.execute_script('''
                var btns = document.querySelectorAll('button');
                return Array.from(btns).map(b => b.textContent.trim()).filter(t => t);
            ''')
            print(f"[续期] 页面按钮: {buttons}")
          
            result["message"] = "未找到续期按钮"
            print(f"[续期] ✗ {result['message']}")
            screenshot(sb, f"{prefix}-no-button")
            return result
      
        print(f"[续期] ✓ 已点击: {clicked}")
        time.sleep(2)
        screenshot(sb, f"{prefix}-clicked")
      
        # 处理确认对话框
        time.sleep(1)
        sb.execute_script('''
            var btns = document.querySelectorAll('button, [role="button"]');
            var keywords = ['확인', 'confirm', 'ok', 'yes', '确认', 'submit', '예'];
            for (var btn of btns) {
                var text = (btn.textContent || '').toLowerCase();
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.click();
                        return;
                    }
                }
            }
        ''')
      
        time.sleep(3)
        screenshot(sb, f"{prefix}-result")
      
        # 获取结果
        for _ in range(10):
            api_result = sb.execute_script('return window.__renewResult;')
            if api_result:
                status = api_result.get("status")
                response = api_result.get("response", "")
              
                print(f"[续期] API 响应: {status}")
              
                if status == 200:
                    result["success"] = True
                    result["message"] = "续期成功"
                  
                    time.sleep(2)
                    sb.refresh()
                    time.sleep(3)
                    result["expiry_after"] = self.get_expiry(sb)
                  
                    if result["expiry_after"]:
                        new_remaining = format_remaining(result["expiry_after"])
                        print(f"[续期] ✓ 成功！新到期: {result['expiry_after']} ({new_remaining})")
                    else:
                        print("[续期] ✓ 成功！")
                      
                elif status in [400, 429]:
                    cooldown_keywords = ["아직", "cannot", "wait", "too early", "already", "갱신", "이미"]
                    if any(kw in response.lower() for kw in cooldown_keywords):
                        result["is_cooldown"] = True
                        result["message"] = "冷却期"
                        print(f"[续期] ⏳ 冷却期，跳过")
                    else:
                        result["message"] = f"请求失败: {response[:80]}"
                        print(f"[续期] ✗ {result['message']}")
                else:
                    result["message"] = f"HTTP {status}"
                    print(f"[续期] ✗ {result['message']}")
                break
            time.sleep(1)
        else:
            # 检查页面变化
            new_expiry = self.get_expiry(sb)
            if new_expiry and result["expiry_before"] and new_expiry != result["expiry_before"]:
                result["success"] = True
                result["expiry_after"] = new_expiry
                result["message"] = "续期成功"
                print(f"[续期] ✓ 成功（到期时间已变化）")
            else:
                result["message"] = "无法确定结果"
                print(f"[续期] ⚠ {result['message']}")
      
        return result

    async def run(self) -> List[Dict]:
        """主流程"""
        results = []
      
        print(f"\n{'=' * 60}")
        print("WeirdHost 自动续期脚本 v6 (Cookie 登录)")
        print(f"{'=' * 60}")
        print(f"账号: {mask_email(self.email)}")
        print(f"Cookie: {'已提供' if self.cookies else '未提供'}")
        print(f"代理: {'启用' if self.use_proxy else '未启用'}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 60}")
      
        if not self.cookies:
            print("\n❌ 未提供 Cookie，无法登录")
            print("请设置 WEIRDHOST_COOKIES 环境变量")
            await tg_notify("❌ <b>WeirdHost 续期失败</b>\n\n未提供 Cookie")
            return results
      
        self._setup_display()
      
        try:
            sb_context = self._start_browser()
          
            with sb_context as sb:
                print("[浏览器] ✓ 已启动")
              
                # 注入 Cookie
                self.inject_cookies(sb)
              
                # 检查登录状态
                if not self.check_login_status(sb):
                    print("\n[主流程] ✗ Cookie 无效或已过期")
                    await tg_notify(f"❌ <b>WeirdHost Cookie 过期</b>\n\n请更新 WEIRDHOST_COOKIES")
                    return results
              
                # 提取并保存新 Cookie（续期）
                self.extract_and_save_cookies(sb)
              
                # 获取服务器
                servers = self.get_servers(sb)
                if not servers:
                    print("\n[主流程] ✗ 无服务器")
                    await tg_notify(f"⚠️ <b>WeirdHost 无服务器</b>\n\n账号: {mask_email(self.email)}")
                    return results
              
                # 续期每个服务器
                for idx, server in enumerate(servers, 1):
                    result = self.renew_server(sb, server, idx)
                    results.append(result)
                  
                    # 发送通知
                    if result["success"]:
                        expiry_info = ""
                        if result["expiry_after"]:
                            remaining = format_remaining(result["expiry_after"])
                            expiry_info = f"\n📅 新到期: {result['expiry_after']}\n⏳ 剩余: {remaining}"
                        await tg_notify(f"✅ <b>WeirdHost 续期成功</b>\n\n🖥 {mask_id(result['id'])}{expiry_info}")
                    elif not result["is_cooldown"]:
                        await tg_notify(f"❌ <b>WeirdHost 续期失败</b>\n\n🖥 {mask_id(result['id'])}\n❗ {result['message']}")
                  
                    if len(servers) > 1:
                        time.sleep(3)
              
                # 再次保存 Cookie（确保最新）
                self.extract_and_save_cookies(sb)
              
                # 汇总
                self._print_summary(results)
              
        finally:
            if self.display:
                try:
                    self.display.stop()
                except:
                    pass
      
        return results

    def _print_summary(self, results: List[Dict]):
        print(f"\n{'=' * 60}")
        print("执行结果汇总")
        print(f"{'=' * 60}")
      
        success = sum(1 for r in results if r["success"])
        cooldown = sum(1 for r in results if r["is_cooldown"])
        fail = len(results) - success - cooldown
      
        print(f"总计: {len(results)} | 成功: {success} | 冷却: {cooldown} | 失败: {fail}")
        print("-" * 60)
      
        for r in results:
            if r["success"]:
                status = "✓"
            elif r["is_cooldown"]:
                status = "⏳"
            else:
                status = "✗"
            print(f"  {status} {mask_id(r['id'])} - {r['message']}")
      
        print(f"{'=' * 60}\n")


# ============================================================
# 入口
# ============================================================
async def main():
    email = os.environ.get("WEIRDHOST_EMAIL", "").strip()
    password = os.environ.get("WEIRDHOST_PASSWORD", "").strip()
    cookies = os.environ.get("WEIRDHOST_COOKIES", "").strip()
  
    if not email:
        print("❌ 请设置 WEIRDHOST_EMAIL")
        sys.exit(1)
  
    if not cookies:
        print("❌ 请设置 WEIRDHOST_COOKIES")
        print("格式: remember_web_xxx=eyJpdiI6...")
        sys.exit(1)
  
    use_proxy = os.environ.get("USE_PROXY", "").lower() == "true"
  
    if use_proxy:
        print(f"[代理] SOCKS5: {PROXY_HOST}:{PROXY_SOCKS_PORT}")
        import socket
        try:
            sock = socket.socket()
            sock.settimeout(5)
            if sock.connect_ex((PROXY_HOST, PROXY_SOCKS_PORT)) == 0:
                print("[代理] ✓ 可达")
            else:
                print("[代理] ⚠ 不可达")
            sock.close()
        except:
            pass
  
    renewer = WeirdHostRenewer(email, password, cookies, use_proxy)
    results = await renewer.run()
  
    if results:
        success = sum(1 for r in results if r["success"])
        cooldown = sum(1 for r in results if r["is_cooldown"])
        if success > 0 or cooldown == len(results):
            sys.exit(0)
  
    sys.exit(1)


if __name__ == "__main__":
