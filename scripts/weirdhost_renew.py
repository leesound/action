#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeirdHost 自动续期脚本 v6
Cookie 登录 + SeleniumBase UC 模式绕过 Cloudflare + 代理支持
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
from typing import Optional, Dict, Tuple

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
DEFAULT_COOKIE_NAME = "remember_web"

# 代理配置
PROXY_HOST = "127.0.0.1"
PROXY_SOCKS_PORT = 1080
PROXY_HTTP_PORT = 8080

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
    """检查代理是否可用"""
    try:
        sock = socket.socket()
        sock.settimeout(5)
        result = sock.connect_ex((PROXY_HOST, PROXY_SOCKS_PORT)) == 0
        sock.close()
        return result
    except:
        return False


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
                    print(f"❌ 获取 public key 失败: {resp.status}")
                    return False
                pk_data = await resp.json()

            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            payload = {"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}
            
            async with session.put(secret_url, headers=headers, json=payload) as resp:
                if resp.status in (201, 204):
                    print(f"✅ 已更新 Secret: {secret_name}")
                    return True
                print(f"❌ 更新失败: {resp.status}")
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
                data.add_field("caption", caption[:1024])
                await session.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=60)
                )
    except Exception as e:
        print(f"[TG] 图片发送失败: {e}")


# ============================================================
# Cloudflare 绕过
# ============================================================
def is_cloudflare_challenge(sb) -> bool:
    """检测是否在 Cloudflare 验证页面"""
    try:
        page = sb.get_page_source().lower()
        url = sb.get_current_url().lower()
        
        # 已通过的特征
        passed_indicators = [
            "/server/" in url and "challenge" not in url,
            "유통기한" in page,  # 到期时间（韩文）
            "시간추가" in page,  # 添加时间按钮
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
            "turnstile",
        ]
        
        return any(ind in page for ind in cf_indicators)
    except:
        return False


def wait_for_cloudflare(sb, timeout: int = 90) -> bool:
    """等待 Cloudflare 验证通过"""
    print("[CF] 检测验证状态...")
    
    start = time.time()
    attempt = 0
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        
        # 检查是否已通过
        if not is_cloudflare_challenge(sb):
            print(f"[CF] ✓ 验证通过 ({elapsed}s)")
            return True
        
        # 每 20 秒尝试点击一次
        if elapsed >= 5 and elapsed % 20 < 2:
            attempt += 1
            print(f"[CF] 尝试点击 #{attempt}...")
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                print(f"[CF] 点击异常: {e}")
            time.sleep(3)
            continue
        
        if elapsed % 15 == 0 and elapsed > 0:
            print(f"[CF] 等待中... ({elapsed}s)")
        
        time.sleep(1)
    
    print(f"[CF] ✗ 超时 ({timeout}s)")
    return False


# ============================================================
# 主类
# ============================================================
class WeirdHostRenewer:
    def __init__(self, cookie_value: str, cookie_name: str = DEFAULT_COOKIE_NAME, 
                 server_url: str = DEFAULT_SERVER_URL, use_proxy: bool = False):
        self.cookie_value = cookie_value
        self.cookie_name = cookie_name
        self.server_url = server_url
        self.use_proxy = use_proxy
        self.display = None
        self.new_cookie_name = None
        self.new_cookie_value = None

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
        
        # 配置代理
        if self.use_proxy:
            sb_kwargs["proxy"] = f"socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}"
            print(f"[浏览器] 代理: socks5://{PROXY_HOST}:{PROXY_SOCKS_PORT}")
        
        return SB(**sb_kwargs)

    def inject_cookie(self, sb) -> bool:
        """注入 Cookie"""
        print(f"[Cookie] 注入: {self.cookie_name}")
        try:
            # 先访问域名以设置 cookie
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=6)
            time.sleep(3)
            
            # 处理可能的 CF 验证
            if is_cloudflare_challenge(sb):
                print("[Cookie] 首次访问遇到 CF 验证...")
                if not wait_for_cloudflare(sb, timeout=90):
                    return False
            
            time.sleep(2)
            
            # 注入 cookie
            sb.add_cookie({
                "name": self.cookie_name,
                "value": self.cookie_value,
                "domain": "hub.weirdhost.xyz",
                "path": "/",
            })
            print("[Cookie] ✓ 已注入")
            return True
        except Exception as e:
            print(f"[Cookie] ✗ 注入失败: {e}")
            return False

    def extract_cookie(self, sb) -> Tuple[Optional[str], Optional[str]]:
        """提取 remember_web* cookie"""
        try:
            cookies = sb.get_cookies()
            for cookie in cookies:
                name = cookie.get("name", "")
                if name.startswith("remember_web"):
                    value = cookie.get("value", "")
                    print(f"[Cookie] 提取: {name} = {mask_str(value, 10)}")
                    return (name, value)
        except Exception as e:
            print(f"[Cookie] 提取失败: {e}")
        return (None, None)

    def get_expiry(self, sb) -> Optional[str]:
        """获取到期时间"""
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
        """点击续期按钮"""
        print("[续期] 查找续期按钮...")
        
        result = sb.execute_script('''
            var buttons = document.querySelectorAll('button, a, [role="button"]');
            var keywords = ['시간추가', '시간연장', '연장', 'Add Time', 'Renew', 'Extend'];
            
            for (var btn of buttons) {
                var text = (btn.textContent || btn.innerText || '').trim();
                for (var kw of keywords) {
                    if (text.includes(kw)) {
                        btn.click();
                        return {found: true, text: text};
                    }
                }
            }
            return {found: false};
        ''')
        
        if result and result.get("found"):
            print(f"[续期] ✓ 已点击: {result.get('text')}")
            return True
        
        print("[续期] ✗ 未找到续期按钮")
        return False

    async def run(self) -> Dict:
        """主流程"""
        result = {
            "success": False,
            "message": "",
            "expiry_before": None,
            "expiry_after": None,
            "cookie_updated": False,
        }
        
        print(f"\n{'=' * 60}")
        print("WeirdHost 自动续期脚本 v6")
        print(f"{'=' * 60}")
        print(f"Cookie: {self.cookie_name} = {mask_str(self.cookie_value, 10)}")
        print(f"服务器: {self.server_url}")
        print(f"代理: {'启用' if self.use_proxy else '未启用'}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 60}")
        
        self._setup_display()
        
        try:
            sb_context = self._start_browser()
            
            with sb_context as sb:
                print("[浏览器] ✓ 已启动")
                
                # ===== 1. 注入 Cookie =====
                if not self.inject_cookie(sb):
                    result["message"] = "Cookie 注入失败"
                    screenshot(sb, "error-cookie-inject")
                    return result
                
                screenshot(sb, "01-cookie-injected")
                
                # ===== 2. 访问服务器页面 =====
                print(f"\n[访问] {self.server_url}")
                sb.uc_open_with_reconnect(self.server_url, reconnect_time=6)
                time.sleep(3)
                screenshot(sb, "02-server-page")
                
                # 处理 Cloudflare
                if is_cloudflare_challenge(sb):
                    print("[访问] 检测到 Cloudflare 验证...")
                    if not wait_for_cloudflare(sb, timeout=90):
                        screenshot(sb, "02-cf-failed")
                        result["message"] = "Cloudflare 验证失败"
                        return result
                
                time.sleep(2)
                screenshot(sb, "03-after-cf")
                
                # 检查是否登录成功
                current_url = sb.get_current_url()
                if "/auth/login" in current_url or "/login" in current_url:
                    screenshot(sb, "03-cookie-expired")
                    result["message"] = "Cookie 已失效"
                    await tg_notify("❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新 REMEMBER_WEB_COOKIE")
                    return result
                
                print("[访问] ✓ Cookie 登录成功")
                
                # ===== 3. 获取当前到期时间 =====
                result["expiry_before"] = self.get_expiry(sb)
                if result["expiry_before"]:
                    remaining = format_remaining(result["expiry_before"])
                    print(f"[到期] 当前: {result['expiry_before']} ({remaining})")
                
                # ===== 4. 点击续期按钮 =====
                if not self.click_renew_button(sb):
                    screenshot(sb, "04-no-button")
                    result["message"] = "未找到续期按钮"
                    return result
                
                time.sleep(3)
                screenshot(sb, "04-after-click")
                
                # ===== 5. 处理续期后的 Cloudflare 验证 =====
                if is_cloudflare_challenge(sb):
                    print("[续期] 检测到 Cloudflare 验证...")
                    screenshot(sb, "05-cf-challenge")
                    
                    if not wait_for_cloudflare(sb, timeout=120):
                        screenshot(sb, "05-cf-failed")
                        result["message"] = "续期 Cloudflare 验证失败"
                        return result
                    
                    screenshot(sb, "05-cf-passed")
                
                time.sleep(3)
                
                # ===== 6. 检查结果 =====
                # 刷新页面获取新到期时间
                sb.uc_open_with_reconnect(self.server_url, reconnect_time=4)
                time.sleep(3)
                
                if is_cloudflare_challenge(sb):
                    wait_for_cloudflare(sb, timeout=60)
                
                time.sleep(2)
                screenshot(sb, "06-result")
                
                result["expiry_after"] = self.get_expiry(sb)
                
                if result["expiry_after"]:
                    new_remaining = format_remaining(result["expiry_after"])
                    print(f"[到期] 新: {result['expiry_after']} ({new_remaining})")
                    
                    # 判断是否续期成功
                    if result["expiry_before"] and result["expiry_after"] != result["expiry_before"]:
                        result["success"] = True
                        result["message"] = "续期成功"
                        print(f"[续期] ✓ 成功！")
                    elif not result["expiry_before"]:
                        result["success"] = True
                        result["message"] = "续期成功（无法比较）"
                        print(f"[续期] ✓ 可能成功")
                    else:
                        # 到期时间未变化，可能在冷却期，也算成功
                        result["success"] = True
                        result["message"] = "已执行（可能在冷却期）"
                        print(f"[续期] ⏳ {result['message']}")
                else:
                    # 无法获取到期时间，但操作已执行
                    result["success"] = True
                    result["message"] = "已执行（无法确认到期时间）"
                    print(f"[续期] ⚠ {result['message']}")
                
                # ===== 7. 提取并更新 Cookie =====
                new_name, new_value = self.extract_cookie(sb)
                if new_name and new_value:
                    if new_value != self.cookie_value or new_name != self.cookie_name:
                        print("[Cookie] 检测到变化，更新 GitHub Secrets...")
                        self.new_cookie_name = new_name
                        self.new_cookie_value = new_value
                        
                        updated = await update_github_secret("REMEMBER_WEB_COOKIE", new_value)
                        if new_name != DEFAULT_COOKIE_NAME:
                            await update_github_secret("REMEMBER_WEB_COOKIE_NAME", new_name)
                        
                        if updated:
                            result["cookie_updated"] = True
                    else:
                        print("[Cookie] 未变化")
                
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
    cookie_value = os.environ.get("REMEMBER_WEB_COOKIE", "").strip()
    cookie_name = os.environ.get("REMEMBER_WEB_COOKIE_NAME", DEFAULT_COOKIE_NAME)
    server_url = os.environ.get("SERVER_URL", DEFAULT_SERVER_URL)
    use_proxy = os.environ.get("USE_PROXY", "").lower() == "true"
    
    if not cookie_value:
        print("❌ 请设置 REMEMBER_WEB_COOKIE")
        sys.exit(1)
    
    # 检查代理
    if use_proxy:
        print(f"[代理] SOCKS5: {PROXY_HOST}:{PROXY_SOCKS_PORT}")
        if check_proxy():
            print("[代理] ✓ 可达")
        else:
            print("[代理] ⚠ 不可达，将尝试直连")
            use_proxy = False
    
    renewer = WeirdHostRenewer(cookie_value, cookie_name, server_url, use_proxy)
    result = await renewer.run()
    
    # 发送通知
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
