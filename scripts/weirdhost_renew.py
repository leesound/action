#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 (单文件完整版)

功能：
  1. 账号密码登录（支持 Turnstile 验证）
  2. Cloudflare Turnstile 自动绕过
  3. HY2 代理支持
  4. 自动获取服务器列表并续期
  5. Telegram 通知

环境变量：
  - WEIRDHOST_EMAIL    : 登录邮箱（必须）
  - WEIRDHOST_PASSWORD : 登录密码（必须）
  - HY2_URL            : HY2 代理 URL（可选，格式: hysteria2://password@host:port?sni=xxx&insecure=1#name）
  - TG_BOT_TOKEN       : Telegram Bot Token（可选）
  - TG_CHAT_ID         : Telegram Chat ID（可选）

依赖安装：
  pip install seleniumbase aiohttp pyvirtualdisplay
  # Linux 还需要: apt-get install -y xvfb
"""

import os
import sys
import time
import json
import asyncio
import aiohttp
import platform
import subprocess
import signal
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path

# ============================================================
# 配置常量
# ============================================================
BASE_URL = "https://hub.weirdhost.xyz"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/"
NOTIFY_DAYS_BEFORE = 2  # 到期前几天通知

# HY2 本地代理端口
HY2_SOCKS_PORT = 21080
HY2_HTTP_PORT = 21081


# ============================================================
# 工具函数
# ============================================================
def is_linux() -> bool:
    """检测是否为Linux系统"""
    return platform.system().lower() == "linux"


def is_github_actions() -> bool:
    """检测是否在 GitHub Actions 环境"""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def mask_string(s: str, show_chars: int = 2) -> str:
    """脱敏字符串"""
    if not s or len(s) < show_chars * 2 + 2:
        return "***"
    return f"{s[:show_chars]}****{s[-show_chars:]}"


def mask_email(email: str) -> str:
    """脱敏邮箱"""
    if "@" not in email:
        return mask_string(email)
    local, domain = email.split("@", 1)
    return f"{mask_string(local, 2)}@{domain}"


def extract_server_id(url: str) -> str:
    """从 URL 提取服务器 ID"""
    try:
        if "/server/" in url:
            # 处理完整 UUID 格式: 8a8db3cc-85cd-4d4b-b943-e087f5e7e43d
            server_part = url.split("/server/")[-1].strip("/")
            return server_part.split("/")[0]
        return "Unknown"
    except:
        return "Unknown"


def calculate_remaining_days(expiry_str: str) -> Optional[int]:
    """计算剩余天数"""
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                diff = expiry_dt - datetime.now()
                return diff.days
            except ValueError:
                continue
        return None
    except:
        return None


def format_remaining_time(expiry_str: str) -> str:
    """格式化剩余时间"""
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
        hours, remainder = divmod(diff.seconds, 3600)
        minutes = remainder // 60

        parts = []
        if days > 0:
            parts.append(f"{days} 天")
        if hours > 0:
            parts.append(f"{hours} 小时")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes} 分钟")

        return " ".join(parts) if parts else "不到 1 分钟"
    except:
        return "计算失败"


def get_executor_name() -> str:
    """获取执行器名称"""
    if is_github_actions():
        return "GitHub Actions"
    return "本地执行"


# ============================================================
# HY2 代理管理
# ============================================================
class HY2ProxyManager:
    """Hysteria2 代理管理器"""

    def __init__(self, hy2_url: str):
        """
        初始化 HY2 代理管理器

        Args:
            hy2_url: HY2 URL (格式: hysteria2://password@host:port?sni=xxx&insecure=1#name)
        """
        self.hy2_url = hy2_url
        self.process: Optional[subprocess.Popen] = None
        self.config_path = Path("/tmp/hy2-config.yaml")
        self.binary_path = Path("/tmp/hysteria")

        # 解析 URL
        self.config = self._parse_hy2_url(hy2_url)

    def _parse_hy2_url(self, url: str) -> Dict[str, Any]:
        """
        解析 HY2 URL

        格式: hysteria2://password@host:port?sni=xxx&alpn=xxx&insecure=1#name
        """
        config = {
            "password": "",
            "server": "",
            "sni": "",
            "alpn": "",
            "insecure": False,
            "name": ""
        }

        try:
            # 移除协议前缀
            if url.startswith("hysteria2://"):
                url = url[12:]
            elif url.startswith("hy2://"):
                url = url[6:]

            # 提取名称 (#name)
            if "#" in url:
                url, config["name"] = url.rsplit("#", 1)
                config["name"] = unquote(config["name"])

            # 提取参数 (?key=value&...)
            params = {}
            if "?" in url:
                url, query_string = url.split("?", 1)
                for param in query_string.split("&"):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        params[key] = unquote(value)

            config["sni"] = params.get("sni", "")
            config["alpn"] = params.get("alpn", "h3")
            config["insecure"] = params.get("insecure", "0") == "1"

            # 提取密码和服务器 (password@host:port)
            if "@" in url:
                config["password"], server_part = url.rsplit("@", 1)
            else:
                server_part = url

            config["server"] = server_part

            # 如果没有 SNI，使用服务器地址
            if not config["sni"]:
                config["sni"] = server_part.split(":")[0]

            print(f"[HY2] 解析配置:")
            print(f"      服务器: {config['server']}")
            print(f"      SNI: {config['sni']}")
            print(f"      跳过验证: {config['insecure']}")

            return config

        except Exception as e:
            print(f"[HY2] URL 解析失败: {e}")
            return config

    async def download_binary(self) -> bool:
        """下载 Hysteria2 二进制文件"""
        if self.binary_path.exists():
            print("[HY2] 二进制文件已存在")
            return True

        print("[HY2] 下载 Hysteria2...")

        # 确定下载 URL
        system = platform.system().lower()
        machine = platform.machine().lower()

        if system == "linux":
            if machine in ["x86_64", "amd64"]:
                arch = "amd64"
            elif machine in ["aarch64", "arm64"]:
                arch = "arm64"
            else:
                arch = "amd64"
            filename = f"hysteria-linux-{arch}"
        elif system == "darwin":
            if machine == "arm64":
                arch = "arm64"
            else:
                arch = "amd64"
            filename = f"hysteria-darwin-{arch}"
        elif system == "windows":
            filename = "hysteria-windows-amd64.exe"
        else:
            print(f"[HY2] 不支持的系统: {system}")
            return False

        download_url = f"https://github.com/apernet/hysteria/releases/latest/download/{filename}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        print(f"[HY2] 下载失败: HTTP {resp.status}")
                        return False

                    with open(self.binary_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)

            # 添加执行权限
            os.chmod(self.binary_path, 0o755)
            print(f"[HY2] 下载完成: {self.binary_path}")
            return True

        except Exception as e:
            print(f"[HY2] 下载异常: {e}")
            return False

    def _generate_config(self) -> str:
        """生成配置文件"""
        config_content = f"""server: {self.config['server']}
auth: {self.config['password']}

tls:
  sni: {self.config['sni']}
  insecure: {str(self.config['insecure']).lower()}

socks5:
  listen: 127.0.0.1:{HY2_SOCKS_PORT}

http:
  listen: 127.0.0.1:{HY2_HTTP_PORT}
"""
        return config_content

    async def start(self) -> bool:
        """启动 HY2 代理"""
        if not self.config["server"]:
            print("[HY2] 配置无效，跳过启动")
            return False

        # 下载二进制文件
        if not await self.download_binary():
            return False

        # 生成配置文件
        config_content = self._generate_config()
        self.config_path.write_text(config_content)
        print(f"[HY2] 配置文件已生成: {self.config_path}")

        # 启动进程
        try:
            self.process = subprocess.Popen(
                [str(self.binary_path), "client", "-c", str(self.config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # 等待启动
            await asyncio.sleep(3)

            # 检查进程状态
            if self.process.poll() is not None:
                stderr = self.process.stderr.read().decode() if self.process.stderr else ""
                print(f"[HY2] 启动失败: {stderr}")
                return False

            # 测试连接
            if await self._test_connection():
                print(f"[HY2] 代理已启动")
                print(f"      SOCKS5: 127.0.0.1:{HY2_SOCKS_PORT}")
                print(f"      HTTP:   127.0.0.1:{HY2_HTTP_PORT}")
                return True
            else:
                print("[HY2] 代理启动但连接测试失败")
                return False

        except Exception as e:
            print(f"[HY2] 启动异常: {e}")
            return False

    async def _test_connection(self) -> bool:
        """测试代理连接"""
        try:
            connector = aiohttp.TCPConnector()
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    "https://httpbin.org/ip",
                    proxy=f"http://127.0.0.1:{HY2_HTTP_PORT}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[HY2] 连接测试成功，出口 IP: {data.get('origin', 'Unknown')}")
                        return True
        except Exception as e:
            print(f"[HY2] 连接测试失败: {e}")
        return False

    def stop(self):
        """停止 HY2 代理"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                print("[HY2] 代理已停止")
            except:
                self.process.kill()

    def get_proxy_url(self) -> str:
        """获取代理 URL（供 SeleniumBase 使用）"""
        return f"http://127.0.0.1:{HY2_HTTP_PORT}"


# ============================================================
# Turnstile 绕过模块
# ============================================================
class TurnstileBypasser:
    """Cloudflare Turnstile 绕过器"""

    def __init__(self, proxy: Optional[str] = None, headless: bool = True):
        """
        初始化绕过器

        Args:
            proxy: 代理地址 (格式: http://host:port)
            headless: 是否无头模式（Linux 下自动使用 Xvfb）
        """
        self.proxy = proxy
        self.headless = headless
        self.display = None
        self.sb = None
        self._sb_context = None

    def _setup_display(self):
        """设置 Linux 虚拟显示"""
        if is_linux() and not os.environ.get("DISPLAY"):
            try:
                from pyvirtualdisplay import Display
                self.display = Display(visible=False, size=(1920, 1080))
                self.display.start()
                os.environ["DISPLAY"] = self.display.new_display_var
                print("[Turnstile] Linux: 已启动虚拟显示 (Xvfb)")
            except ImportError:
                print("[Turnstile] 警告: pyvirtualdisplay 未安装")
            except Exception as e:
                print(f"[Turnstile] 虚拟显示启动失败: {e}")

    def start(self):
        """启动浏览器"""
        self._setup_display()

        from seleniumbase import SB

        # 构建参数
        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": False if is_linux() else self.headless,
        }

        if self.proxy:
            sb_kwargs["proxy"] = self.proxy
            print(f"[Turnstile] 使用代理: {self.proxy}")

        self._sb_context = SB(**sb_kwargs)
        self.sb = self._sb_context.__enter__()
        print("[Turnstile] 浏览器已启动")

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

    def wait_for_turnstile(self, timeout: int = 120) -> bool:
        """
        等待并处理 Turnstile 验证

        Returns:
            bool: 是否成功通过验证
        """
        print("[Turnstile] 检测 Cloudflare 验证...")

        for i in range(timeout):
            try:
                page_source = self.sb.get_page_source().lower()

                # 检测 Turnstile/CF 特征
                cf_indicators = [
                    "turnstile",
                    "challenges.cloudflare",
                    "just a moment",
                    "verify you are human",
                    "checking your browser",
                    "cf-challenge",
                    "ray id"
                ]

                has_cf = any(x in page_source for x in cf_indicators)

                if not has_cf:
                    print(f"[Turnstile] ✓ 验证通过 ({i + 1}秒)")
                    return True

                # 定期尝试点击验证码
                if i in [3, 8, 15, 25, 40, 60]:
                    try:
                        self.sb.uc_gui_click_captcha()
                        print(f"[Turnstile] 尝试点击验证码 ({i + 1}秒)")
                        time.sleep(2)
                    except Exception as e:
                        pass

                if i % 15 == 0 and i > 0:
                    print(f"[Turnstile] 等待中... ({i}/{timeout}秒)")

                time.sleep(1)

            except Exception as e:
                time.sleep(1)

        print("[Turnstile] ✗ 验证超时")
        return False

    def open_url(self, url: str, wait_cf: bool = True) -> bool:
        """
        打开 URL 并处理 Cloudflare

        Args:
            url: 目标 URL
            wait_cf: 是否等待 CF 验证

        Returns:
            bool: 是否成功
        """
        print(f"[Turnstile] 访问: {url}")

        try:
            self.sb.uc_open_with_reconnect(url, reconnect_time=5.0)
            time.sleep(2)

            if wait_cf:
                return self.wait_for_turnstile()

            return True

        except Exception as e:
            print(f"[Turnstile] 打开 URL 失败: {e}")
            return False

    def get_cookies(self) -> Dict[str, str]:
        """获取所有 Cookie"""
        try:
            cookies_list = self.sb.get_cookies()
            return {c["name"]: c["value"] for c in cookies_list}
        except:
            return {}

    def get_current_url(self) -> str:
        """获取当前 URL"""
        try:
            return self.sb.get_current_url()
        except:
            return ""

    def execute_script(self, script: str) -> Any:
        """执行 JavaScript"""
        try:
            return self.sb.execute_script(script)
        except:
            return None

    def screenshot(self, path: str):
        """截图"""
        try:
            self.sb.save_screenshot(path)
        except:
            pass


# ============================================================
# Telegram 通知
# ============================================================
async def tg_notify(message: str):
    """发送 Telegram 通知"""
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("[TG] 未配置，跳过通知")
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
                    print(f"[TG] ✗ 发送失败: HTTP {resp.status}")
    except Exception as e:
        print(f"[TG] ✗ 发送异常: {e}")


async def tg_notify_photo(photo_path: str, caption: str = ""):
    """发送 Telegram 图片"""
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()

    if not token or not chat_id or not os.path.exists(photo_path):
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    try:
        async with aiohttp.ClientSession() as session:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption[:1024])
                data.add_field("parse_mode", "HTML")
                await session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=60))
    except Exception as e:
        print(f"[TG] 发送图片失败: {e}")


# ============================================================
# WeirdHost 续期主类
# ============================================================
class WeirdHostRenewer:
    """WeirdHost 自动续期器"""

    def __init__(self, email: str, password: str, proxy: Optional[str] = None):
        """
        初始化续期器

        Args:
            email: 登录邮箱
            password: 登录密码
            proxy: HTTP 代理地址
        """
        self.email = email
        self.password = password
        self.proxy = proxy
        self.bypasser: Optional[TurnstileBypasser] = None
        self.logged_in = False
        self.servers: List[Dict] = []

    def start(self):
        """启动浏览器"""
        print(f"\n{'=' * 60}")
        print("WeirdHost 自动续期脚本")
        print(f"{'=' * 60}")
        print(f"[*] 账号: {mask_email(self.email)}")
        if self.proxy:
            print(f"[*] 代理: {self.proxy}")
        print(f"[*] 执行器: {get_executor_name()}")
        print(f"{'=' * 60}\n")

        self.bypasser = TurnstileBypasser(proxy=self.proxy, headless=True)
        self.bypasser.start()

    def stop(self):
        """停止浏览器"""
        if self.bypasser:
            self.bypasser.stop()

    def login(self) -> bool:
        """
        账号密码登录

        流程：
        1. 访问登录页面，等待 Turnstile
        2. 填写账号密码
        3. 点击登录
        4. 处理登录后的 Turnstile
        5. 验证登录状态
        """
        print(f"\n[登录] 开始登录流程...")

        try:
            # 1. 访问登录页面
            print("[登录] 1/5 访问登录页面...")
            if not self.bypasser.open_url(LOGIN_URL, wait_cf=True):
                print("[登录] ✗ 无法访问登录页面")
                return False

            time.sleep(2)

            # 检查是否已登录
            current_url = self.bypasser.get_current_url()
            if "/auth/login" not in current_url and "/login" not in current_url:
                print("[登录] ✓ 检测到已登录状态")
                self.logged_in = True
                return True

            # 2. 填写登录表单
            print("[登录] 2/5 填写登录表单...")
            time.sleep(1)

            # 输入邮箱
            email_filled = False
            email_selectors = [
                'input[name="user"]',
                'input[type="email"]',
                'input[placeholder*="mail"]',
                'input[placeholder*="Mail"]',
                'input[name="email"]',
            ]

            for selector in email_selectors:
                try:
                    self.bypasser.sb.type(selector, self.email)
                    email_filled = True
                    print(f"[登录]   ✓ 邮箱已输入")
                    break
                except:
                    continue

            if not email_filled:
                # JavaScript 方式
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
                print(f"[登录]   ✓ 邮箱已输入 (JS)")

            time.sleep(0.5)

            # 输入密码
            password_filled = False
            try:
                self.bypasser.sb.type('input[type="password"]', self.password)
                password_filled = True
                print(f"[登录]   ✓ 密码已输入")
            except:
                pass

            if not password_filled:
                self.bypasser.execute_script(f'''
                    var pwdInputs = document.querySelectorAll('input[type="password"]');
                    if (pwdInputs.length > 0) {{
                        pwdInputs[0].value = "{self.password}";
                        pwdInputs[0].dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                ''')
                print(f"[登录]   ✓ 密码已输入 (JS)")

            time.sleep(1)

            # 3. 处理登录前的 Turnstile
            print("[登录] 3/5 处理 Turnstile 验证...")

            has_turnstile = self.bypasser.execute_script('''
                return document.querySelector('iframe[src*="challenges.cloudflare"]') !== null ||
                       document.querySelector('[data-sitekey]') !== null ||
                       document.querySelector('.cf-turnstile') !== null;
            ''')

            if has_turnstile:
                print("[登录]   发现 Turnstile，尝试处理...")
                try:
                    self.bypasser.sb.uc_gui_click_captcha()
                    time.sleep(3)
                except:
                    pass

            # 4. 点击登录按钮
            print("[登录] 4/5 点击登录按钮...")

            login_clicked = False
            login_selectors = [
                'button[type="submit"]',
                'form button',
                'button:contains("Login")',
                'button:contains("로그인")',
                'button:contains("Sign")',
                'input[type="submit"]',
            ]

            for selector in login_selectors:
                try:
                    self.bypasser.sb.click(selector)
                    login_clicked = True
                    print(f"[登录]   ✓ 已点击登录按钮")
                    break
                except:
                    continue

            if not login_clicked:
                self.bypasser.execute_script('''
                    var btn = document.querySelector('button[type="submit"]') ||
                              document.querySelector('form button') ||
                              document.querySelector('button');
                    if (btn) btn.click();
                ''')
                print(f"[登录]   ✓ 已点击登录按钮 (JS)")

            # 5. 等待登录完成
            print("[登录] 5/5 等待登录完成...")
            time.sleep(3)

            # 处理登录后的 Turnstile
            self.bypasser.wait_for_turnstile(timeout=60)

            time.sleep(2)

            # 验证登录状态
            current_url = self.bypasser.get_current_url()
            page_source = self.bypasser.sb.get_page_source().lower()

            if "/auth/login" in current_url or "/login" in current_url:
                if "invalid" in page_source or "incorrect" in page_source or "wrong" in page_source:
                    print("[登录] ✗ 账号或密码错误")
                    return False
                print("[登录] ✗ 登录失败，仍在登录页面")
                return False

            print(f"[登录] ✓ 登录成功！")
            self.logged_in = True
            return True

        except Exception as e:
            print(f"[登录] ✗ 登录异常: {e}")
            return False

    def get_servers(self) -> List[Dict]:
        """获取服务器列表"""
        print(f"\n[服务器] 获取服务器列表...")

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
                print(f"[服务器] ✓ 找到 {len(servers)} 个服务器:")
                for s in servers:
                    print(f"         - {mask_string(s['id'], 3)}: {s['name']}")
            else:
                print("[服务器] ✗ 未找到服务器")

            return servers or []

        except Exception as e:
            print(f"[服务器] ✗ 获取失败: {e}")
            return []

    def get_server_expiry(self, server_url: str) -> Optional[str]:
        """获取服务器到期时间"""
        try:
            expiry = self.bypasser.execute_script('''
                var text = document.body.innerText;

                var patterns = [
                    /유통기한[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/,
                    /유통기한[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/,
                    /expiry[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/i,
                    /expiry[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/i,
                    /Expiration[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/i,
                    /(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/
                ];

                for (var pattern of patterns) {
                    var match = text.match(pattern);
                    if (match) {
                        return match[1].trim();
                    }
                }

                return null;
            ''')

            return expiry

        except Exception as e:
            print(f"[到期时间] 获取失败: {e}")
            return None

    def click_renew_button(self) -> bool:
        """点击续期按钮"""
        print("[续期] 查找续期按钮...")

        # 尝试多种选择器
        renew_selectors = [
            'button:has-text("시간추가")',
            'button:has-text("시간연장")',
            'button:has-text("Add Time")',
            'button:has-text("Renew")',
            'button:has-text("Extend")',
        ]

        for selector in renew_selectors:
            try:
                self.bypasser.sb.click(selector)
                print(f"[续期] ✓ 已点击续期按钮")
                return True
            except:
                continue

        # JavaScript 方式查找
        clicked = self.bypasser.execute_script('''
            var buttons = document.querySelectorAll('button');
            var keywords = ['시간추가', '시간연장', 'Add Time', 'Renew', 'Extend', '연장'];

            for (var btn of buttons) {
                var text = btn.textContent || btn.innerText || '';
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        ''')

        if clicked:
            print(f"[续期] ✓ 已点击续期按钮 (JS)")
            return True

        print("[续期] ✗ 未找到续期按钮")
        return False

    def handle_renew_dialog(self) -> bool:
        """处理续期对话框（包括 Turnstile 验证）"""
        print("[续期] 处理续期对话框...")

        time.sleep(2)

        # 等待可能出现的 Turnstile
        self.bypasser.wait_for_turnstile(timeout=60)

        time.sleep(1)

        # 尝试点击复选框（如果有）
        try:
            checkbox_clicked = self.bypasser.execute_script('''
                var checkbox = document.querySelector('input[type="checkbox"]');
                if (checkbox && !checkbox.checked) {
                    checkbox.click();
                    return true;
                }
                return false;
            ''')
            if checkbox_clicked:
                print("[续期] ✓ 已勾选复选框")
        except:
            pass

        time.sleep(1)

        # 尝试点击确认按钮
        confirm_selectors = [
            'button:has-text("확인")',
            'button:has-text("Confirm")',
            'button:has-text("OK")',
            'button:has-text("Yes")',
            'button[type="submit"]',
        ]

        for selector in confirm_selectors:
            try:
                self.bypasser.sb.click(selector)
                print(f"[续期] ✓ 已点击确认按钮")
                return True
            except:
                continue

        return True

    async def renew_server(self, server: Dict) -> Dict[str, Any]:
        """
        续期单个服务器

        Returns:
            {
                "success": bool,
                "server_id": str,
                "server_name": str,
                "message": str,
                "expiry_before": str,
                "expiry_after": str,
                "remaining_days": int
            }
        """
        result = {
            "success": False,
            "server_id": server.get("id", "Unknown"),
            "server_name": server.get("name", "Unknown"),
            "message": "",
            "expiry_before": None,
            "expiry_after": None,
            "remaining_days": None
        }

        server_url = server.get("url", "")
        masked_id = mask_string(result["server_id"], 3)

        print(f"\n{'=' * 50}")
        print(f"[续期] 服务器: {masked_id} ({result['server_name']})")
        print(f"{'=' * 50}")

        try:
            # 1. 访问服务器页面
            print("[续期] 1/4 访问服务器页面...")
            if not self.bypasser.open_url(server_url, wait_cf=True):
                result["message"] = "无法访问服务器页面"
                return result

            time.sleep(2)

            # 2. 获取当前到期时间
            print("[续期] 2/4 获取到期时间...")
            result["expiry_before"] = self.get_server_expiry(server_url)

            if result["expiry_before"]:
                result["remaining_days"] = calculate_remaining_days(result["expiry_before"])
                remaining_str = format_remaining_time(result["expiry_before"])
                print(f"[续期]   到期: {result['expiry_before']}")
                print(f"[续期]   剩余: {remaining_str} ({result['remaining_days']} 天)")
            else:
                print("[续期]   ⚠ 无法获取到期时间")
                result["message"] = "无法获取到期时间"

                # 发送通知
                msg = f"""⚠️ <b>WeirdHost 状态异常</b>

❌ 无法获取到期时间
🖥 服务器: <code>{result['server_id']}</code>
📛 名称: {result['server_name']}
💻 执行器: {get_executor_name()}

👉 <a href="{server_url}">点击检查</a>"""
                await tg_notify(msg)
                return result

            # 检查是否需要提醒（到期前 N 天）
            if result["remaining_days"] is not None and result["remaining_days"] <= NOTIFY_DAYS_BEFORE:
                print(f"[续期]   ⚠ 即将到期，发送提醒...")

                if result["remaining_days"] < 0:
                    status = "🔴 已过期"
                elif result["remaining_days"] == 0:
                    status = "🔴 今天到期"
                elif result["remaining_days"] == 1:
                    status = "🟡 明天到期"
                else:
                    status = f"🟡 {result['remaining_days']} 天后到期"

                msg = f"""⚠️ <b>WeirdHost 续订提醒</b>

{status}
🖥 服务器: <code>{result['server_id']}</code>
📛 名称: {result['server_name']}
📅 到期时间: <code>{result['expiry_before']}</code>
⏳ 剩余: <b>{remaining_str}</b>
💻 执行器: {get_executor_name()}

👉 <a href="{server_url}">点击续订</a>"""
                await tg_notify(msg)

            # 3. 尝试续期
            print("[续期] 3/4 尝试续期...")

            if not self.click_renew_button():
                result["message"] = "未找到续期按钮"
                return result

            time.sleep(2)

            # 4. 处理续期对话框
            print("[续期] 4/4 处理续期确认...")
            self.handle_renew_dialog()

            time.sleep(3)

            # 等待 API 响应
            print("[续期] 等待响应...")
            time.sleep(5)

            # 刷新页面获取新的到期时间
            self.bypasser.open_url(server_url, wait_cf=True)
            time.sleep(2)

            result["expiry_after"] = self.get_server_expiry(server_url)

            # 判断是否续期成功
            if result["expiry_after"] and result["expiry_before"]:
                if result["expiry_after"] > result["expiry_before"]:
                    result["success"] = True
                    result["message"] = "续期成功"
                    new_remaining = format_remaining_time(result["expiry_after"])

                    print(f"[续期] ✓ 续期成功！")
                    print(f"[续期]   新到期: {result['expiry_after']}")
                    print(f"[续期]   新剩余: {new_remaining}")

                    # 发送成功通知
                    msg = f"""🎉 <b>WeirdHost 续期成功</b>

✅ 续期成功！
🖥 服务器: <code>{result['server_id']}</code>
📛 名称: {result['server_name']}
📅 原到期: <code>{result['expiry_before']}</code>
📅 新到期: <code>{result['expiry_after']}</code>
⏳ 剩余: <b>{new_remaining}</b>
💻 执行器: {get_executor_name()}"""
                    await tg_notify(msg)

                else:
                    result["message"] = "到期时间未变化（可能在冷却期）"
                    print(f"[续期] ℹ {result['message']}")
            else:
                result["message"] = "无法确认续期结果"
                print(f"[续期] ⚠ {result['message']}")

            return result

        except Exception as e:
            result["message"] = f"续期异常: {str(e)}"
            print(f"[续期] ✗ {result['message']}")
            return result

    async def run(self) -> List[Dict]:
        """
        运行完整的续期流程

        Returns:
            所有服务器的续期结果列表
        """
        results = []

        try:
            # 1. 启动浏览器
            self.start()

            # 2. 登录
            if not self.login():
                print("\n[主流程] ✗ 登录失败，退出")
                await tg_notify(f"""❌ <b>WeirdHost 登录失败</b>

账号: <code>{mask_email(self.email)}</code>
执行器: {get_executor_name()}

请检查账号密码是否正确。""")
                return results

            # 3. 获取服务器列表
            servers = self.get_servers()

            if not servers:
                print("\n[主流程] ✗ 没有找到服务器")
                return results

            # 4. 逐个续期
            for server in servers:
                result = await self.renew_server(server)
                results.append(result)
                time.sleep(2)  # 服务器之间间隔

            # 5. 输出汇总
            print(f"\n{'=' * 60}")
            print("续期结果汇总")
            print(f"{'=' * 60}")

            success_count = sum(1 for r in results if r["success"])
            print(f"总计: {len(results)} 个服务器, 成功: {success_count} 个")

            for r in results:
                status = "✓" if r["success"] else "✗"
                print(f"  {status} {mask_string(r['server_id'], 3)}: {r['message']}")

            return results

        finally:
            # 6. 清理
            self.stop()


# ============================================================
# 主函数
# ============================================================
async def main():
    """主函数"""
    # 获取环境变量
    email = os.environ.get("WEIRDHOST_EMAIL", "").strip()
    password = os.environ.get("WEIRDHOST_PASSWORD", "").strip()
    hy2_url = os.environ.get("HY2_URL", "").strip()

    # 验证必要参数
    if not email or not password:
        print("❌ 错误: 请设置 WEIRDHOST_EMAIL 和 WEIRDHOST_PASSWORD 环境变量")
        print("\n示例:")
        print("  export WEIRDHOST_EMAIL='your@email.com'")
        print("  export WEIRDHOST_PASSWORD='yourpassword'")
        print("  export HY2_URL='hysteria2://pass@host:port?sni=xxx&insecure=1#name'  # 可选")
        sys.exit(1)

    # HY2 代理管理器
    hy2_manager: Optional[HY2ProxyManager] = None
    proxy_url: Optional[str] = None

    try:
        # 启动 HY2 代理（如果配置了）
        if hy2_url:
            print("\n[HY2] 配置了 HY2 代理，正在启动...")
            hy2_manager = HY2ProxyManager(hy2_url)

            if await hy2_manager.start():
                proxy_url = hy2_manager.get_proxy_url()
                print(f"[HY2] ✓ 代理已就绪: {proxy_url}")
            else:
                print("[HY2] ⚠ 代理启动失败，将直接连接")

        # 创建续期器并运行
        renewer = WeirdHostRenewer(
            email=email,
            password=password,
            proxy=proxy_url
        )

        results = await renewer.run()

        # 返回状态码
        if results and any(r["success"] for r in results):
            sys.exit(0)
        else:
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n[*] 用户中断")
        sys.exit(130)

    except Exception as e:
        print(f"\n❌ 未处理的异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        # 停止 HY2 代理
        if hy2_manager:
            hy2_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())

