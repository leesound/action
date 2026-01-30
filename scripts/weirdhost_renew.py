#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v4
参考 KataBump 的 Cloudflare 绕过方法
"""

import os
import sys
import time
import asyncio
import aiohttp
import platform
from datetime import datetime
from typing import Optional, Dict, List, Any

# ============================================================
# 配置
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/"

PROXY_HOST = "127.0.0.1"
PROXY_SOCKS_PORT = 1080

SCREENSHOT_DIR = "."


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
        
        # 验证页面特征
        cf_indicators = [
            "verify you are human",
            "checking your browser",
            "just a moment",
            "challenges.cloudflare",
            "cf-turnstile",
            "确认您是真人",
        ]
        
        # 已通过的特征
        passed_indicators = [
            "auth/login" in url and ("email" in page or "password" in page),
            "dashboard" in url,
            "/server/" in url,
        ]
        
        if any(passed_indicators):
            return False
            
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
        
        # 检查是否已通过
        if not is_cloudflare_challenge(sb):
            print(f"[CF] ✓ 验证通过 ({elapsed}s)")
            return True
        
        # 首次检测到验证，尝试点击
        if not clicked and elapsed >= 3:
            print(f"[CF] 尝试点击验证框...")
            try:
                sb.uc_gui_click_captcha()
                clicked = True
            except Exception as e:
                print(f"[CF] 点击异常: {e}")
                clicked = True
            time.sleep(3)
            continue
        
        # 如果点击后还没通过，再次尝试
        if clicked and elapsed >= 15 and elapsed % 10 == 0:
            print(f"[CF] 重试点击... ({elapsed}s)")
            try:
                sb.uc_gui_click_captcha()
            except:
                pass
        
        if elapsed % 20 == 0 and elapsed > 0:
            print(f"[CF] 等待中... ({elapsed}s)")
        
        time.sleep(1)
    
    print(f"[CF] ✗ 超时 ({timeout}s)")
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
    def __init__(self, email: str, password: str, use_proxy: bool = False):
        self.email = email
        self.password = password
        self.use_proxy = use_proxy
        self.sb = None
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

    def login(self, sb) -> bool:
        """登录"""
        print("\n[登录] 开始...")
        
        # 打开登录页
        print("[登录] 访问登录页...")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
        time.sleep(3)
        screenshot(sb, "01-login-open")
        
        # 等待 Cloudflare
        if not wait_for_cloudflare(sb, timeout=90):
            screenshot(sb, "01-cf-failed")
            return False
        
        screenshot(sb, "02-cf-passed")
        
        # 检查是否已登录
        current_url = sb.get_current_url()
        if "/auth/login" not in current_url:
            print("[登录] ✓ 已登录 (session有效)")
            return True
        
        # 填写表单
        print("[登录] 填写表单...")
        try:
            sb.wait_for_element("input[type='email'], input[name='email']", timeout=10)
            
            # 输入邮箱
            email_input = "input[type='email'], input[name='email']"
            sb.type(email_input, self.email)
            
            # 输入密码
            pwd_input = "input[type='password']"
            sb.type(pwd_input, self.password)
            
            screenshot(sb, "03-form-filled")
            
            # 提交
            print("[登录] 提交...")
            sb.uc_click("button[type='submit'], form button")
            
        except Exception as e:
            print(f"[登录] 表单异常: {e}")
            screenshot(sb, "03-form-error")
            return False
        
        time.sleep(3)
        
        # 处理提交后的验证
        if is_cloudflare_challenge(sb):
            print("[登录] 处理提交后验证...")
            wait_for_cloudflare(sb, timeout=60)
        
        time.sleep(2)
        screenshot(sb, "04-login-result")
        
        # 检查结果
        current_url = sb.get_current_url()
        if "/auth/login" in current_url:
            page = sb.get_page_source().lower()
            if "invalid" in page or "incorrect" in page or "wrong" in page:
                print("[登录] ✗ 账号或密码错误")
            else:
                print("[登录] ✗ 登录失败")
            return False
        
        print("[登录] ✓ 成功")
        return True

    def get_servers(self, sb) -> List[Dict]:
        """获取服务器列表"""
        print("\n[服务器] 获取列表...")
        
        sb.uc_open_with_reconnect(DASHBOARD_URL, reconnect_time=4)
        time.sleep(3)
        wait_for_cloudflare(sb, timeout=60)
        screenshot(sb, "05-dashboard")
        
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
                    servers.push({
                        id: match[1],
                        name: link.textContent.trim() || match[1],
                        url: window.location.origin + '/server/' + match[1]
                    });
                }
            }
            return servers;
        ''')
        
        if servers:
            print(f"[服务器] ✓ 找到 {len(servers)} 个")
            for s in servers:
                print(f"  - {mask_id(s['id'])}: {s['name']}")
        else:
            print("[服务器] ✗ 未找到")
            screenshot(sb, "05-no-servers")
        
        return servers or []

    def get_expiry(self, sb) -> Optional[str]:
        """获取到期时间"""
        try:
            return sb.execute_script('''
                var text = document.body.innerText;
                var patterns = [
                    /유통기한[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/,
                    /유통기한[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/,
                    /expir[yation]*[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/i,
                    /(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/
                ];
                for (var p of patterns) {
                    var m = text.match(p);
                    if (m) return m[1].trim();
                }
                return null;
            ''')
        except:
            return None

    def renew_server(self, sb, server: Dict) -> Dict:
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
        print(f"\n{'=' * 50}")
        print(f"[续期] {masked_id} - {server['name']}")
        print('=' * 50)
        
        # 访问服务器页面
        sb.uc_open_with_reconnect(server["url"], reconnect_time=4)
        time.sleep(3)
        wait_for_cloudflare(sb, timeout=60)
        screenshot(sb, f"06-server-{masked_id[:8]}")
        
        # 获取当前到期时间
        result["expiry_before"] = self.get_expiry(sb)
        if result["expiry_before"]:
            remaining = format_remaining(result["expiry_before"])
            print(f"[续期] 当前到期: {result['expiry_before']} ({remaining})")
        
        # 注入 XHR 拦截
        sb.execute_script('''
            window.__renewResult = null;
            (function() {
                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(m, url) {
                    this._url = url;
                    return origOpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function() {
                    var xhr = this;
                    var orig = xhr.onload;
                    xhr.onload = function() {
                        if (xhr._url && xhr._url.includes('/renew')) {
                            window.__renewResult = {status: xhr.status, response: xhr.responseText};
                        }
                        if (orig) orig.apply(xhr, arguments);
                    };
                    return origSend.apply(this, arguments);
                };
            })();
        ''')
        
        # 点击续期按钮
        print("[续期] 查找续期按钮...")
        clicked = sb.execute_script('''
            var btns = document.querySelectorAll('button, a.btn, [role="button"]');
            var keywords = ['시간추가', '시간연장', 'Add Time', 'Renew', 'Extend', '연장', '갱신', '续期'];
            for (var btn of btns) {
                var text = (btn.textContent || '').trim();
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        ''')
        
        if not clicked:
            result["message"] = "未找到续期按钮"
            print(f"[续期] ✗ {result['message']}")
            return result
        
        print("[续期] ✓ 已点击续期按钮")
        time.sleep(2)
        screenshot(sb, f"07-renew-clicked-{masked_id[:8]}")
        
        # 处理可能的验证
        if is_cloudflare_challenge(sb):
            wait_for_cloudflare(sb, timeout=30)
        
        # 点击确认
        sb.execute_script('''
            var btns = document.querySelectorAll('button, [role="button"]');
            var keywords = ['확인', 'Confirm', 'OK', 'Yes', '确认'];
            for (var btn of btns) {
                var text = (btn.textContent || '').trim();
                for (var kw of keywords) {
                    if (text.includes(kw)) { btn.click(); return; }
                }
            }
        ''')
        
        time.sleep(3)
        screenshot(sb, f"08-renew-result-{masked_id[:8]}")
        
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
                    
                    # 刷新获取新到期时间
                    time.sleep(2)
                    sb.uc_open_with_reconnect(server["url"], reconnect_time=4)
                    time.sleep(2)
                    result["expiry_after"] = self.get_expiry(sb)
                    
                    if result["expiry_after"]:
                        new_remaining = format_remaining(result["expiry_after"])
                        print(f"[续期] ✓ 成功！新到期: {result['expiry_after']} ({new_remaining})")
                    else:
                        print("[续期] ✓ 成功！")
                        
                elif status == 400:
                    cooldown_keywords = ["아직", "cannot renew", "wait", "too early"]
                    if any(kw in response.lower() for kw in cooldown_keywords):
                        result["is_cooldown"] = True
                        result["message"] = "冷却期"
                        print(f"[续期] ⏳ 冷却期，跳过")
                    else:
                        result["message"] = f"请求失败: {response[:50]}"
                        print(f"[续期] ✗ {result['message']}")
                else:
                    result["message"] = f"HTTP {status}"
                    print(f"[续期] ✗ {result['message']}")
                break
            time.sleep(1)
        else:
            result["message"] = "无法获取结果"
            print(f"[续期] ⚠ {result['message']}")
        
        return result

    async def run(self) -> List[Dict]:
        """主流程"""
        results = []
        
        print(f"\n{'=' * 60}")
        print("WeirdHost 自动续期脚本 v4")
        print(f"{'=' * 60}")
        print(f"账号: {mask_email(self.email)}")
        print(f"代理: {'启用' if self.use_proxy else '未启用'}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 60}")
        
        self._setup_display()
        
        try:
            sb_context = self._start_browser()
            
            with sb_context as sb:
                print("[浏览器] ✓ 已启动")
                
                # 登录
                if not self.login(sb):
                    print("\n[主流程] ✗ 登录失败")
                    await tg_notify(f"❌ <b>WeirdHost 登录失败</b>\n\n账号: {mask_email(self.email)}")
                    return results
                
                # 获取服务器
                servers = self.get_servers(sb)
                if not servers:
                    print("\n[主流程] ✗ 无服务器")
                    return results
                
                # 续期每个服务器
                for server in servers:
                    result = self.renew_server(sb, server)
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
    
    if not email or not password:
        print("❌ 请设置 WEIRDHOST_EMAIL 和 WEIRDHOST_PASSWORD")
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
    
    renewer = WeirdHostRenewer(email, password, use_proxy)
    results = await renewer.run()
    
    if results:
        success = sum(1 for r in results if r["success"])
        cooldown = sum(1 for r in results if r["is_cooldown"])
        if success > 0 or cooldown == len(results):
            sys.exit(0)
    
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
