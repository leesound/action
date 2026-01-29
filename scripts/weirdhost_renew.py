#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本

功能：
  1. 账号密码登录（支持 Turnstile 验证）
  2. Cloudflare Turnstile 自动绕过
  3. 支持 SOCKS5 代理
  4. 自动获取服务器列表并续期
  5. Telegram 通知（仅成功/失败，冷却期跳过）

环境变量：
  - WEIRDHOST_EMAIL    : 登录邮箱（必须）
  - WEIRDHOST_PASSWORD : 登录密码（必须）
  - USE_PROXY          : 是否使用代理（可选，设为 "true" 启用）
  - TG_BOT_TOKEN       : Telegram Bot Token（可选）
  - TG_CHAT_ID         : Telegram Chat ID（可选）
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
# 配置常量
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/"

# 代理配置
PROXY_HOST = "127.0.0.1"
PROXY_SOCKS_PORT = 1080

# 冷却期关键词
COOLDOWN_KEYWORDS = [
    "아직 서버를 갱신할 수 없습니다",
    "cannot renew yet",
    "wait until",
    "too early",
]


# ============================================================
# 工具函数
# ============================================================
def is_linux() -> bool:
    return platform.system().lower() == "linux"


def is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def should_use_proxy() -> bool:
    return os.environ.get("USE_PROXY", "").strip().lower() == "true"


def mask_string(s: str, show_chars: int = 2) -> str:
    if not s or len(s) < show_chars * 2 + 2:
        return "***"
    return f"{s[:show_chars]}****{s[-show_chars:]}"


def mask_email(email: str) -> str:
    if "@" not in email:
        return mask_string(email)
    local, domain = email.split("@", 1)
    return f"{mask_string(local, 2)}@{domain}"


def calculate_remaining_days(expiry_str: str) -> Optional[int]:
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                return (expiry_dt - datetime.now()).days
            except ValueError:
                continue
        return None
    except:
        return None


def format_remaining_time(expiry_str: str) -> str:
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


def get_executor_name() -> str:
    return "GitHub Actions" if is_github_actions() else "本地执行"


def is_cooldown_error(error_msg: str) -> bool:
    if not error_msg:
        return False
    error_lower = error_msg.lower()
    return any(kw.lower() in error_lower for kw in COOLDOWN_KEYWORDS)


# ============================================================
# Turnstile 绕过模块
# ============================================================
class TurnstileBypasser:
    def __init__(self, use_proxy: bool = False, headless: bool = True):
        self.use_proxy = use_proxy
        self.headless = headless
        self.display = None
        self.sb = None
        self._sb_context = None

    def _setup_display(self):
        if is_linux() and not os.environ.get("DISPLAY"):
            try:
                from pyvirtualdisplay import Display
                self.display = Display(visible=False, size=(1920, 1080))
                self.display.start()
                os.environ["DISPLAY"] = self.display.new_display_var
                print("[Turnstile] 已启动虚拟显示")
            except Exception as e:
                print(f"[Turnstile] 虚拟显示启动失败: {e}")

    def start(self):
        self._setup_display()

        from seleniumbase import SB

        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": False if is_linux() else self.headless,
        }

        if self.use_proxy:
            proxy_str = f"socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}"
            sb_kwargs["proxy"] = proxy_str
            print(f"[Turnstile] 使用代理: {proxy_str}")

        self._sb_context = SB(**sb_kwargs)
        self.sb = self._sb_context.__enter__()
        print("[Turnstile] 浏览器已启动")

    def stop(self):
        if self._sb_context:
            try:
                self._sb_context.__exit__(None, None, None)
            except:
                pass
        if self.display:
            try:
                self.display.stop()
            except:
                pass

    def wait_for_turnstile(self, timeout: int = 120) -> bool:
        print("[Turnstile] 检测验证...")

        cf_indicators = [
            "turnstile", "challenges.cloudflare", "just a moment",
            "verify you are human", "checking your browser", "cf-challenge"
        ]

        for i in range(timeout):
            try:
                page_source = self.sb.get_page_source().lower()
                has_cf = any(x in page_source for x in cf_indicators)

                if not has_cf:
                    print(f"[Turnstile] ✓ 通过 ({i + 1}s)")
                    return True

                if i in [3, 8, 15, 25, 40, 60, 90]:
                    try:
                        self.sb.uc_gui_click_captcha()
                        time.sleep(2)
                    except:
                        pass

                if i % 20 == 0 and i > 0:
                    print(f"[Turnstile] 等待中... ({i}s)")

                time.sleep(1)
            except:
                time.sleep(1)

        print("[Turnstile] ✗ 超时")
        return False

    def open_url(self, url: str, wait_cf: bool = True) -> bool:
        try:
            self.sb.uc_open_with_reconnect(url, reconnect_time=5.0)
            time.sleep(2)
            return self.wait_for_turnstile() if wait_cf else True
        except Exception as e:
            print(f"[Turnstile] 打开失败: {e}")
            return False

    def get_cookies(self) -> Dict[str, str]:
        try:
            return {c["name"]: c["value"] for c in self.sb.get_cookies()}
        except:
            return {}

    def get_current_url(self) -> str:
        try:
            return self.sb.get_current_url()
        except:
            return ""

    def execute_script(self, script: str) -> Any:
        try:
            return self.sb.execute_script(script)
        except:
            return None

    def screenshot(self, path: str):
        try:
            self.sb.save_screenshot(path)
        except:
            pass


# ============================================================
# Telegram 通知
# ============================================================
async def tg_notify(message: str):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    print("[TG] ✓ 通知已发送")
                else:
                    print(f"[TG] ✗ 发送失败: {resp.status}")
    except Exception as e:
        print(f"[TG] ✗ 异常: {e}")


# ============================================================
# WeirdHost 续期主类
# ============================================================
class WeirdHostRenewer:
    def __init__(self, email: str, password: str, use_proxy: bool = False):
        self.email = email
        self.password = password
        self.use_proxy = use_proxy
        self.bypasser: Optional[TurnstileBypasser] = None
        self.logged_in = False
        self.servers: List[Dict] = []

    def start(self):
        print(f"\n{'=' * 60}")
        print("WeirdHost 自动续期脚本")
        print(f"{'=' * 60}")
        print(f"账号: {mask_email(self.email)}")
        print(f"代理: {'SOCKS5 127.0.0.1:1080' if self.use_proxy else '未使用'}")
        print(f"执行: {get_executor_name()}")
        print(f"{'=' * 60}\n")

        self.bypasser = TurnstileBypasser(use_proxy=self.use_proxy, headless=True)
        self.bypasser.start()

    def stop(self):
        if self.bypasser:
            self.bypasser.stop()

    def login(self) -> bool:
        print(f"\n[登录] 开始...")

        try:
            print("[登录] 访问登录页...")
            if not self.bypasser.open_url(LOGIN_URL, wait_cf=True):
                print("[登录] ✗ 无法访问")
                return False

            time.sleep(2)

            current_url = self.bypasser.get_current_url()
            if "/auth/login" not in current_url and "/login" not in current_url:
                print("[登录] ✓ 已登录")
                self.logged_in = True
                return True

            print("[登录] 填写表单...")
            time.sleep(1)

            # 输入邮箱
            self.bypasser.execute_script(f'''
                var inputs = document.querySelectorAll('input');
                for (var inp of inputs) {{
                    var t = (inp.type || '').toLowerCase();
                    var n = (inp.name || '').toLowerCase();
                    var p = (inp.placeholder || '').toLowerCase();
                    if (t === 'email' || n === 'user' || n === 'email' || p.includes('mail')) {{
                        inp.value = "{self.email}";
                        inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                        break;
                    }}
                }}
            ''')

            time.sleep(0.5)

            # 输入密码
            self.bypasser.execute_script(f'''
                var pwdInputs = document.querySelectorAll('input[type="password"]');
                if (pwdInputs.length > 0) {{
                    pwdInputs[0].value = "{self.password}";
                    pwdInputs[0].dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
            ''')

            time.sleep(1)

            try:
                self.bypasser.sb.uc_gui_click_captcha()
                time.sleep(3)
            except:
                pass

            print("[登录] 提交...")
            self.bypasser.execute_script('''
                var btn = document.querySelector('button[type="submit"]') ||
                          document.querySelector('form button');
                if (btn) btn.click();
            ''')

            time.sleep(3)
            self.bypasser.wait_for_turnstile(timeout=60)
            time.sleep(2)

            current_url = self.bypasser.get_current_url()
            if "/auth/login" in current_url or "/login" in current_url:
                print("[登录] ✗ 失败")
                return False

            print(f"[登录] ✓ 成功")
            self.logged_in = True
            return True

        except Exception as e:
            print(f"[登录] ✗ 异常: {e}")
            return False

    def get_servers(self) -> List[Dict]:
        print(f"\n[服务器] 获取列表...")

        try:
            self.bypasser.open_url(DASHBOARD_URL, wait_cf=True)
            time.sleep(2)

            servers = self.bypasser.execute_script('''
                var servers = [];
                var rows = document.querySelectorAll('table tr');

                for (var row of rows) {
                    var link = row.querySelector('a[href*="/server/"]');
                    if (link) {
                        var href = link.getAttribute('href');
                        var id = href.replace('/server/', '').split('/')[0];
                        var name = link.textContent.trim();

                        servers.push({
                            id: id,
                            name: name,
                            url: 'https://hub.weirdhost.xyz/server/' + id
                        });
                    }
                }
                return servers;
            ''')

            if servers:
                self.servers = servers
                print(f"[服务器] ✓ 找到 {len(servers)} 个")
                for s in servers:
                    print(f"  - {mask_string(s['id'], 4)}: {s['name']}")
            else:
                print("[服务器] ✗ 未找到")

            return servers or []

        except Exception as e:
            print(f"[服务器] ✗ 失败: {e}")
            return []

    def get_server_expiry(self) -> Optional[str]:
        try:
            return self.bypasser.execute_script('''
                var text = document.body.innerText;
                var patterns = [
                    /유통기한[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/,
                    /유통기한[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/,
                    /expir[yation]*[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/i,
                    /expir[yation]*[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/i,
                    /(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/
                ];
                for (var pattern of patterns) {
                    var match = text.match(pattern);
                    if (match) return match[1].trim();
                }
                return null;
            ''')
        except:
            return None

    def click_renew_and_get_result(self, server_id: str) -> Dict[str, Any]:
        result = {
            "clicked": False,
            "api_success": None,
            "api_status": None,
            "api_message": "",
            "is_cooldown": False
        }

        self.bypasser.execute_script('''
            window.__renewResult = null;
            (function() {
                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;
                
                XMLHttpRequest.prototype.open = function(method, url) {
                    this._url = url;
                    return origOpen.apply(this, arguments);
                };
                
                XMLHttpRequest.prototype.send = function() {
                    var xhr = this;
                    var origOnLoad = xhr.onload;
                    
                    xhr.onload = function() {
                        if (xhr._url && xhr._url.includes('/renew')) {
                            window.__renewResult = {
                                status: xhr.status,
                                response: xhr.responseText
                            };
                        }
                        if (origOnLoad) origOnLoad.apply(xhr, arguments);
                    };
                    return origSend.apply(this, arguments);
                };
            })();
        ''')

        time.sleep(0.5)

        clicked = self.bypasser.execute_script('''
            var buttons = document.querySelectorAll('button');
            var keywords = ['시간추가', '시간연장', 'Add Time', 'Renew', 'Extend', '연장', '갱신'];
            for (var btn of buttons) {
                var text = btn.textContent || '';
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
            print("[续期] ✗ 未找到续期按钮")
            return result

        result["clicked"] = True
        print("[续期] ✓ 已点击续期按钮")

        time.sleep(2)
        self.bypasser.wait_for_turnstile(timeout=30)

        self.bypasser.execute_script('''
            var buttons = document.querySelectorAll('button');
            var keywords = ['확인', 'Confirm', 'OK', 'Yes', 'Submit'];
            for (var btn of buttons) {
                var text = btn.textContent || '';
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.click();
                        return;
                    }
                }
            }
        ''')

        time.sleep(3)

        for _ in range(10):
            api_result = self.bypasser.execute_script('return window.__renewResult;')
            if api_result:
                result["api_status"] = api_result.get("status")
                response_text = api_result.get("response", "")

                print(f"[续期] API 响应: {result['api_status']}")

                if result["api_status"] == 200:
                    result["api_success"] = True
                    result["api_message"] = "续期成功"
                elif result["api_status"] == 400:
                    result["api_success"] = False
                    if is_cooldown_error(response_text):
                        result["is_cooldown"] = True
                        result["api_message"] = "冷却期，还不能续期"
                    else:
                        result["api_message"] = f"请求失败: {response_text[:100]}"
                else:
                    result["api_success"] = False
                    result["api_message"] = f"HTTP {result['api_status']}"
                break
            time.sleep(1)

        return result

    async def renew_server(self, server: Dict) -> Dict[str, Any]:
        result = {
            "success": False,
            "server_id": server.get("id", "Unknown"),
            "server_name": server.get("name", "Unknown"),
            "message": "",
            "expiry_before": None,
            "expiry_after": None,
            "remaining_days": None,
            "is_cooldown": False,
            "should_notify": False
        }

        server_url = server.get("url", "")
        masked_id = mask_string(result["server_id"], 4)

        print(f"\n{'=' * 50}")
        print(f"[续期] {masked_id} ({result['server_name']})")
        print(f"{'=' * 50}")

        try:
            print("[续期] 访问页面...")
            if not self.bypasser.open_url(server_url, wait_cf=True):
                result["message"] = "无法访问服务器页面"
                result["should_notify"] = True
                return result

            time.sleep(2)

            result["expiry_before"] = self.get_server_expiry()
            if result["expiry_before"]:
                result["remaining_days"] = calculate_remaining_days(result["expiry_before"])
                remaining_str = format_remaining_time(result["expiry_before"])
                print(f"[续期] 到期: {result['expiry_before']} (剩余 {remaining_str})")

            print("[续期] 尝试续期...")
            renew_result = self.click_renew_and_get_result(result["server_id"])

            if not renew_result["clicked"]:
                result["message"] = "未找到续期按钮"
                result["should_notify"] = True
                return result

            result["is_cooldown"] = renew_result["is_cooldown"]

            if renew_result["is_cooldown"]:
                result["message"] = "冷却期，跳过"
                result["should_notify"] = False
                print(f"[续期] ⏳ {result['message']}")
                return result

            if renew_result["api_success"]:
                time.sleep(2)
                self.bypasser.open_url(server_url, wait_cf=True)
                time.sleep(2)
                result["expiry_after"] = self.get_server_expiry()

                result["success"] = True
                result["message"] = "续期成功"
                result["should_notify"] = True

                if result["expiry_after"]:
                    new_remaining = format_remaining_time(result["expiry_after"])
                    print(f"[续期] ✓ 成功！新到期: {result['expiry_after']} ({new_remaining})")
                else:
                    print(f"[续期] ✓ 成功！")

            elif renew_result["api_success"] is False:
                result["message"] = renew_result["api_message"]
                result["should_notify"] = True
                print(f"[续期] ✗ 失败: {result['message']}")

            else:
                result["message"] = "无法确定续期结果"
                result["should_notify"] = True
                print(f"[续期] ⚠ {result['message']}")

            return result

        except Exception as e:
            result["message"] = f"异常: {str(e)}"
            result["should_notify"] = True
            print(f"[续期] ✗ {result['message']}")
            return result

    async def run(self) -> List[Dict]:
        results = []

        try:
            self.start()

            if not self.login():
                print("\n[主流程] ✗ 登录失败")
                await tg_notify(f"""❌ <b>WeirdHost 登录失败</b>

账号: <code>{mask_email(self.email)}</code>
执行: {get_executor_name()}

请检查账号密码。""")
                return results

            servers = self.get_servers()

            if not servers:
                print("\n[主流程] ✗ 没有服务器")
                return results

            for server in servers:
                result = await self.renew_server(server)
                results.append(result)

                if result["should_notify"]:
                    await self._send_notification(result, server.get("url", ""))

                time.sleep(2)

            self._print_summary(results)
            return results

        finally:
            self.stop()

    async def _send_notification(self, result: Dict, server_url: str):
        if result["success"]:
            expiry_info = ""
            if result["expiry_after"]:
                remaining = format_remaining_time(result["expiry_after"])
                expiry_info = f"""
📅 新到期: <code>{result['expiry_after']}</code>
⏳ 剩余: <b>{remaining}</b>"""

            msg = f"""✅ <b>WeirdHost 续期成功</b>

🖥 服务器: <code>{result['server_id']}</code>
📛 名称: {result['server_name']}{expiry_info}
💻 执行: {get_executor_name()}"""

        else:
            msg = f"""❌ <b>WeirdHost 续期失败</b>

🖥 服务器: <code>{result['server_id']}</code>
📛 名称: {result['server_name']}
❗ 原因: {result['message']}
💻 执行: {get_executor_name()}

👉 <a href="{server_url}">点击检查</a>"""

        await tg_notify(msg)

    def _print_summary(self, results: List[Dict]):
        print(f"\n{'=' * 60}")
        print("续期结果汇总")
        print(f"{'=' * 60}")

        success_count = sum(1 for r in results if r["success"])
        cooldown_count = sum(1 for r in results if r["is_cooldown"])
        fail_count = len(results) - success_count - cooldown_count

        print(f"总计: {len(results)} | 成功: {success_count} | 冷却: {cooldown_count} | 失败: {fail_count}")
        print("-" * 60)

        for r in results:
            if r["success"]:
                status = "✓ 成功"
            elif r["is_cooldown"]:
                status = "⏳ 冷却"
            else:
                status = "✗ 失败"

            print(f"  {status} | {mask_string(r['server_id'], 4)} | {r['message']}")

        print(f"{'=' * 60}\n")


# ============================================================
# 主函数
# ============================================================
async def main():
    email = os.environ.get("WEIRDHOST_EMAIL", "").strip()
    password = os.environ.get("WEIRDHOST_PASSWORD", "").strip()

    if not email or not password:
        print("❌ 请设置 WEIRDHOST_EMAIL 和 WEIRDHOST_PASSWORD 环境变量")
        sys.exit(1)

    use_proxy = should_use_proxy()

    if use_proxy:
        print(f"[代理] 将使用 SOCKS5: {PROXY_HOST}:{PROXY_SOCKS_PORT}")
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((PROXY_HOST, PROXY_SOCKS_PORT))
            sock.close()
            if result == 0:
                print("[代理] ✓ 端口可达")
            else:
                print("[代理] ⚠ 端口不可达，继续尝试...")
        except Exception as e:
            print(f"[代理] ⚠ 测试失败: {e}")
    else:
        print("[代理] 未启用")

    try:
        renewer = WeirdHostRenewer(
            email=email,
            password=password,
            use_proxy=use_proxy
        )

        results = await renewer.run()

        if results:
            success_count = sum(1 for r in results if r["success"])
            cooldown_count = sum(1 for r in results if r["is_cooldown"])

            if success_count > 0 or cooldown_count == len(results):
                sys.exit(0)

        sys.exit(1)

    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)

    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

