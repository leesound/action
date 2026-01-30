#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v3

针对 Cloudflare Turnstile 优化
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

PROXY_HOST = "127.0.0.1"
PROXY_SOCKS_PORT = 1080

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
# Turnstile 绕过模块 v3
# ============================================================
class TurnstileBypasser:
    def __init__(self, use_proxy: bool = False):
        self.use_proxy = use_proxy
        self.display = None
        self.sb = None
        self._sb_context = None

    def _setup_display(self):
        """设置虚拟显示"""
        if is_linux():
            try:
                from pyvirtualdisplay import Display
                self.display = Display(visible=False, size=(1920, 1080))
                self.display.start()
                print("[Turnstile] ✓ 虚拟显示已启动")
            except Exception as e:
                print(f"[Turnstile] ⚠ 虚拟显示失败: {e}")

    def start(self):
        """启动浏览器"""
        self._setup_display()

        from seleniumbase import SB

        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": False,  # 必须非 headless
            "agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        if self.use_proxy:
            sb_kwargs["proxy"] = f"socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}"
            print(f"[Turnstile] 代理: socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}")

        self._sb_context = SB(**sb_kwargs)
        self.sb = self._sb_context.__enter__()
        print("[Turnstile] ✓ 浏览器已启动")

    def stop(self):
        """停止浏览器"""
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

    def _has_cf_challenge(self) -> bool:
        """检测是否有 Cloudflare 验证"""
        try:
            page_text = self.sb.get_page_source().lower()
            indicators = [
                "challenges.cloudflare.com",
                "cf-turnstile",
                "just a moment",
                "checking your browser",
                "verify you are human",
                "确认您是真人",
                "请完成以下操作",
            ]
            return any(ind in page_text for ind in indicators)
        except:
            return False

    def _try_click_cf(self) -> bool:
        """尝试点击 Cloudflare 验证"""
        methods_tried = []
        
        # 方法1: uc_gui_click_cf (专门用于 Cloudflare)
        try:
            self.sb.uc_gui_click_cf()
            methods_tried.append("uc_gui_click_cf")
            time.sleep(3)
            if not self._has_cf_challenge():
                print(f"[Turnstile] ✓ 方法1成功 (uc_gui_click_cf)")
                return True
        except Exception as e:
            pass

        # 方法2: 切换到 iframe 并点击
        try:
            iframes = self.sb.find_elements("iframe")
            for iframe in iframes:
                try:
                    src = iframe.get_attribute("src") or ""
                    if "challenges.cloudflare" in src or "turnstile" in src:
                        # 获取 iframe 位置并点击
                        location = iframe.location
                        size = iframe.size
                        
                        # 计算复选框位置 (通常在 iframe 左侧)
                        click_x = location['x'] + 30
                        click_y = location['y'] + size['height'] // 2
                        
                        # 使用 ActionChains 点击
                        from selenium.webdriver.common.action_chains import ActionChains
                        actions = ActionChains(self.sb.driver)
                        actions.move_by_offset(click_x, click_y).click().perform()
                        actions.move_by_offset(-click_x, -click_y).perform()
                        
                        methods_tried.append("iframe_click")
                        time.sleep(3)
                        
                        if not self._has_cf_challenge():
                            print(f"[Turnstile] ✓ 方法2成功 (iframe_click)")
                            return True
                except:
                    pass
        except:
            pass

        # 方法3: 使用 JavaScript 触发
        try:
            self.sb.execute_script('''
                // 查找并点击 Turnstile
                var iframes = document.querySelectorAll('iframe');
                for (var iframe of iframes) {
                    if (iframe.src && iframe.src.includes('challenges.cloudflare')) {
                        // 模拟点击 iframe 区域
                        var rect = iframe.getBoundingClientRect();
                        var evt = new MouseEvent('click', {
                            bubbles: true,
                            cancelable: true,
                            clientX: rect.left + 30,
                            clientY: rect.top + rect.height / 2
                        });
                        iframe.dispatchEvent(evt);
                    }
                }
            ''')
            methods_tried.append("js_click")
            time.sleep(3)
            
            if not self._has_cf_challenge():
                print(f"[Turnstile] ✓ 方法3成功 (js_click)")
                return True
        except:
            pass

        # 方法4: 再次尝试 uc_gui_click_captcha
        try:
            self.sb.uc_gui_click_captcha()
            methods_tried.append("uc_gui_click_captcha")
            time.sleep(3)
            if not self._has_cf_challenge():
                print(f"[Turnstile] ✓ 方法4成功 (uc_gui_click_captcha)")
                return True
        except:
            pass

        print(f"[Turnstile] 尝试了: {', '.join(methods_tried)}")
        return False

    def handle_cf_challenge(self, timeout: int = 120) -> bool:
        """处理 Cloudflare 验证"""
        print("[Turnstile] 检测 Cloudflare 验证...")
        
        start_time = time.time()
        attempt = 0
        
        while time.time() - start_time < timeout:
            elapsed = int(time.time() - start_time)
            
            # 检查是否还有验证
            if not self._has_cf_challenge():
                print(f"[Turnstile] ✓ 验证通过 ({elapsed}s)")
                return True
            
            # 每5秒尝试点击一次
            if elapsed > 0 and elapsed % 5 == 0:
                attempt += 1
                print(f"[Turnstile] 尝试 #{attempt} ({elapsed}s)...")
                self._try_click_cf()
            
            # 进度显示
            if elapsed > 0 and elapsed % 30 == 0:
                print(f"[Turnstile] 仍在等待... ({elapsed}s)")
            
            time.sleep(1)
        
        print(f"[Turnstile] ✗ 超时 ({timeout}s)")
        return False

    def open_with_cf_bypass(self, url: str, max_retries: int = 3) -> bool:
        """打开 URL 并绕过 Cloudflare"""
        print(f"[Turnstile] 打开: {url}")
        
        for retry in range(max_retries):
            try:
                if retry > 0:
                    print(f"[Turnstile] 重试 #{retry + 1}")
                
                # 使用 uc_open_with_reconnect
                self.sb.uc_open_with_reconnect(url, reconnect_time=6)
                time.sleep(3)
                
                # 处理 Cloudflare 验证
                if self._has_cf_challenge():
                    if self.handle_cf_challenge(timeout=90):
                        return True
                else:
                    print("[Turnstile] ✓ 无需验证")
                    return True
                    
            except Exception as e:
                print(f"[Turnstile] 异常: {e}")
                time.sleep(2)
        
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

    def save_screenshot(self, filename: str):
        try:
            self.sb.save_screenshot(filename)
            print(f"[截图] {filename}")
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
        print("WeirdHost 自动续期脚本 v3")
        print(f"{'=' * 60}")
        print(f"账号: {mask_email(self.email)}")
        print(f"代理: {'启用' if self.use_proxy else '未启用'}")
        print(f"执行: {get_executor_name()}")
        print(f"{'=' * 60}\n")

        self.bypasser = TurnstileBypasser(use_proxy=self.use_proxy)
        self.bypasser.start()

    def stop(self):
        if self.bypasser:
            self.bypasser.stop()

    def login(self) -> bool:
        print(f"\n[登录] 开始...")

        try:
            # 访问登录页
            if not self.bypasser.open_with_cf_bypass(LOGIN_URL):
                print("[登录] ✗ 无法访问登录页")
                self.bypasser.save_screenshot("login_failed.png")
                return False

            time.sleep(2)

            # 检查是否已登录
            current_url = self.bypasser.get_current_url()
            if "/auth/login" not in current_url and "/login" not in current_url:
                print("[登录] ✓ 已登录 (session有效)")
                self.logged_in = True
                return True

            print("[登录] 填写表单...")

            # 输入邮箱
            self.bypasser.execute_script(f'''
                var emailInput = document.querySelector('input[type="email"]') ||
                                 document.querySelector('input[name="email"]') ||
                                 document.querySelector('input[name="user"]') ||
                                 document.querySelector('input[placeholder*="mail"]');
                if (emailInput) {{
                    emailInput.value = "{self.email}";
                    emailInput.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
            ''')
            time.sleep(0.5)

            # 输入密码
            self.bypasser.execute_script(f'''
                var pwdInput = document.querySelector('input[type="password"]');
                if (pwdInput) {{
                    pwdInput.value = "{self.password}";
                    pwdInput.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
            ''')
            time.sleep(1)

            # 处理可能的 Turnstile
            if self.bypasser._has_cf_challenge():
                print("[登录] 处理表单中的验证...")
                self.bypasser.handle_cf_challenge(timeout=60)

            # 提交表单
            print("[登录] 提交...")
            self.bypasser.execute_script('''
                var form = document.querySelector('form');
                var btn = document.querySelector('button[type="submit"]') ||
                          document.querySelector('form button') ||
                          document.querySelector('input[type="submit"]');
                if (btn) {
                    btn.click();
                } else if (form) {
                    form.submit();
                }
            ''')

            time.sleep(3)

            # 处理提交后的验证
            if self.bypasser._has_cf_challenge():
                print("[登录] 处理提交后的验证...")
                self.bypasser.handle_cf_challenge(timeout=60)

            time.sleep(2)

            # 检查登录结果
            current_url = self.bypasser.get_current_url()
            page_source = self.bypasser.sb.get_page_source().lower()

            if "/auth/login" in current_url or "/login" in current_url:
                # 检查是否有错误信息
                if "invalid" in page_source or "incorrect" in page_source or "wrong" in page_source:
                    print("[登录] ✗ 账号或密码错误")
                else:
                    print("[登录] ✗ 登录失败 (仍在登录页)")
                self.bypasser.save_screenshot("login_result.png")
                return False

            print(f"[登录] ✓ 成功")
            self.logged_in = True
            return True

        except Exception as e:
            print(f"[登录] ✗ 异常: {e}")
            self.bypasser.save_screenshot("login_error.png")
            return False

    def get_servers(self) -> List[Dict]:
        print(f"\n[服务器] 获取列表...")

        try:
            if not self.bypasser.open_with_cf_bypass(DASHBOARD_URL):
                print("[服务器] ✗ 无法访问仪表板")
                return []

            time.sleep(2)

            servers = self.bypasser.execute_script('''
                var servers = [];
                
                // 方法1: 查找表格中的链接
                var rows = document.querySelectorAll('table tr, .server-item, [class*="server"]');
                for (var row of rows) {
                    var link = row.querySelector('a[href*="/server/"]');
                    if (link) {
                        var href = link.getAttribute('href');
                        var id = href.match(/\\/server\\/([^/]+)/);
                        if (id) {
                            servers.push({
                                id: id[1],
                                name: link.textContent.trim() || id[1],
                                url: window.location.origin + '/server/' + id[1]
                            });
                        }
                    }
                }
                
                // 方法2: 查找所有服务器链接
                if (servers.length === 0) {
                    var links = document.querySelectorAll('a[href*="/server/"]');
                    for (var link of links) {
                        var href = link.getAttribute('href');
                        var id = href.match(/\\/server\\/([^/]+)/);
                        if (id && !servers.find(s => s.id === id[1])) {
                            servers.push({
                                id: id[1],
                                name: link.textContent.trim() || id[1],
                                url: window.location.origin + '/server/' + id[1]
                            });
                        }
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
                print("[服务器] ✗ 未找到服务器")
                self.bypasser.save_screenshot("no_servers.png")

            return servers or []

        except Exception as e:
            print(f"[服务器] ✗ 异常: {e}")
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
            "api_message": "",
            "is_cooldown": False
        }

        # 注入 XHR 拦截器
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

        # 点击续期按钮
        clicked = self.bypasser.execute_script('''
            var buttons = document.querySelectorAll('button, a.btn, [role="button"]');
            var keywords = ['시간추가', '시간연장', 'Add Time', 'Renew', 'Extend', '연장', '갱신', '续期'];
            for (var btn of buttons) {
                var text = (btn.textContent || btn.innerText || '').trim();
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

        # 处理可能的验证
        if self.bypasser._has_cf_challenge():
            self.bypasser.handle_cf_challenge(timeout=30)

        # 点击确认按钮
        self.bypasser.execute_script('''
            var buttons = document.querySelectorAll('button, [role="button"]');
            var keywords = ['확인', 'Confirm', 'OK', 'Yes', 'Submit', '确认'];
            for (var btn of buttons) {
                var text = (btn.textContent || '').trim();
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.click();
                        return;
                    }
                }
            }
        ''')

        time.sleep(3)

        # 获取 API 结果
        for _ in range(10):
            api_result = self.bypasser.execute_script('return window.__renewResult;')
            if api_result:
                status = api_result.get("status")
                response = api_result.get("response", "")

                print(f"[续期] API 状态: {status}")

                if status == 200:
                    result["api_success"] = True
                    result["api_message"] = "续期成功"
                elif status == 400:
                    result["api_success"] = False
                    if is_cooldown_error(response):
                        result["is_cooldown"] = True
                        result["api_message"] = "冷却期"
                    else:
                        result["api_message"] = f"请求失败: {response[:100]}"
                else:
                    result["api_success"] = False
                    result["api_message"] = f"HTTP {status}"
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
            "is_cooldown": False,
            "should_notify": False
        }

        server_url = server.get("url", "")
        masked_id = mask_string(result["server_id"], 4)

        print(f"\n{'=' * 50}")
        print(f"[续期] {masked_id} ({result['server_name']})")
        print(f"{'=' * 50}")

        try:
            if not self.bypasser.open_with_cf_bypass(server_url):
                result["message"] = "无法访问服务器页面"
                result["should_notify"] = True
                return result

            time.sleep(2)

            result["expiry_before"] = self.get_server_expiry()
            if result["expiry_before"]:
                remaining = format_remaining_time(result["expiry_before"])
                print(f"[续期] 当前到期: {result['expiry_before']} ({remaining})")

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
                self.bypasser.open_with_cf_bypass(server_url)
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
                print(f"[续期] ✗ {result['message']}")

            else:
                result["message"] = "无法确定结果"
                result["should_notify"] = True

            return result

        except Exception as e:
            result["message"] = f"异常: {str(e)}"
            result["should_notify"] = True
            return result

    async def _send_notification(self, result: Dict, server_url: str):
        if result["success"]:
            expiry_info = ""
            if result["expiry_after"]:
                remaining = format_remaining_time(result["expiry_after"])
                expiry_info = f"\n📅 新到期: <code>{result['expiry_after']}</code>\n⏳ 剩余: <b>{remaining}</b>"

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

    async def run(self) -> List[Dict]:
        results = []

        try:
            self.start()

            if not self.login():
                print("\n[主流程] ✗ 登录失败")
                await tg_notify(f"""❌ <b>WeirdHost 登录失败</b>

📧 账号: <code>{mask_email(self.email)}</code>
💻 执行: {get_executor_name()}

请检查账号密码或 Cloudflare 验证""")
                return results

            servers = self.get_servers()
            if not servers:
                print("\n[主流程] ✗ 未找到服务器")
                await tg_notify(f"""⚠️ <b>WeirdHost 无服务器</b>

📧 账号: <code>{mask_email(self.email)}</code>
💻 执行: {get_executor_name()}""")
                return results

            for server in servers:
                result = await self.renew_server(server)
                results.append(result)

                if result["should_notify"]:
                    await self._send_notification(result, server.get("url", ""))

                if len(servers) > 1:
                    time.sleep(3)

            self._print_summary(results)
            return results

        finally:
            self.stop()

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
                print("[代理] ⚠ 端口不可达")
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

