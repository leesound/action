#!/usr/bin/env python3
"""
WeirdHost 自动续期 - Playwright 版本
支持 Cookie 认证和邮箱密码认证
"""

import os
import sys
import time
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from base64 import b64encode, b64decode

BASE_URL = "https://hub.weirdhost.xyz"


class WeirdhostRenew:
    def __init__(self):
        # 认证信息
        self.cookie = os.getenv('WEIRDHOST_COOKIE', '') or os.getenv('REMEMBER_WEB_COOKIE', '')
        self.email = os.getenv('WEIRDHOST_EMAIL', '')
        self.password = os.getenv('WEIRDHOST_PASSWORD', '')
        
        # 服务器配置
        server_urls = os.getenv('WEIRDHOST_SERVER_URLS', '')
        server_id = os.getenv('WEIRDHOST_ID', '')
        
        self.server_list = []
        if server_urls:
            self.server_list = [url.strip() for url in server_urls.split(',') if url.strip()]
        elif server_id:
            self.server_list = [f"{BASE_URL}/server/{server_id}"]
        
        # 代理配置
        self.socks_proxy = os.getenv('SOCKS_PROXY', '')
        
        # 通知配置
        self.tg_token = os.getenv('TG_BOT_TOKEN', '')
        self.tg_chat_id = os.getenv('TG_CHAT_ID', '')
        
        # 浏览器配置
        self.headless = os.getenv('HEADLESS', 'false').lower() == 'true'
    
    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {msg}")
    
    def has_cookie_auth(self) -> bool:
        return bool(self.cookie)
    
    def has_email_auth(self) -> bool:
        return bool(self.email and self.password)
    
    def send_telegram(self, message: str, photo_path: Optional[str] = None) -> bool:
        if not self.tg_token or not self.tg_chat_id:
            return False
        try:
            if photo_path and os.path.exists(photo_path):
                url = f"https://api.telegram.org/bot{self.tg_token}/sendPhoto"
                boundary = "----FormBoundary7MA4YWxk"
                with open(photo_path, "rb") as f:
                    photo_data = f.read()
                body = b"\r\n".join([
                    f"--{boundary}".encode(), b'Content-Disposition: form-data; name="chat_id"', b"", self.tg_chat_id.encode(),
                    f"--{boundary}".encode(), b'Content-Disposition: form-data; name="caption"', b"", message.encode('utf-8'),
                    f"--{boundary}".encode(), b'Content-Disposition: form-data; name="parse_mode"', b"", b"HTML",
                    f"--{boundary}".encode(), b'Content-Disposition: form-data; name="photo"; filename="screenshot.png"',
                    b"Content-Type: image/png", b"", photo_data, f"--{boundary}--".encode(), b""
                ])
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            else:
                url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
                data = json.dumps({"chat_id": self.tg_chat_id, "text": message, "parse_mode": "HTML"}).encode('utf-8')
                req = urllib.request.Request(url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(req, timeout=30)
            self.log("✓ Telegram 通知已发送")
            return True
        except Exception as e:
            self.log(f"✗ Telegram 发送失败: {e}", "ERROR")
            return False
    
    def update_github_secret(self, secret_name: str, secret_value: str) -> bool:
        token = os.environ.get("REPO_TOKEN", "").strip()
        repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if not token or not repo:
            return False
        try:
            key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
            req = urllib.request.Request(key_url)
            req.add_header("Authorization", f"token {token}")
            req.add_header("Accept", "application/vnd.github.v3+json")
            req.add_header("User-Agent", "WeirdHost-Renew")
            with urllib.request.urlopen(req, timeout=30) as resp:
                key_data = json.loads(resp.read().decode('utf-8'))
            from nacl import encoding, public
            public_key_bytes = b64decode(key_data["key"])
            sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')
            secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
            data = json.dumps({"encrypted_value": encrypted_value, "key_id": key_data["key_id"]}).encode('utf-8')
            req = urllib.request.Request(secret_url, data=data, method="PUT")
            req.add_header("Authorization", f"token {token}")
            req.add_header("Accept", "application/vnd.github.v3+json")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", "WeirdHost-Renew")
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in (201, 204):
                    self.log("✓ GitHub Secret 已更新")
                    return True
            return False
        except Exception as e:
            self.log(f"✗ GitHub Secret 更新失败: {e}", "ERROR")
            return False
    
    def parse_cookie_for_playwright(self) -> list:
        """解析 cookie 为 Playwright 格式"""
        import urllib.parse
        cookie_str = urllib.parse.unquote(self.cookie.strip())
        cookies = []
        
        # 检查是否是完整的 cookie 字符串 (name=value 格式)
        if '=' in cookie_str and not cookie_str.startswith('remember_web'):
            # 可能是多个 cookie
            for part in cookie_str.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies.append({
                        "name": k.strip(),
                        "value": v.strip(),
                        "domain": "hub.weirdhost.xyz",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax"
                    })
        else:
            # 只有 value，需要添加标准的 cookie name
            value = cookie_str.split('=')[-1] if '=' in cookie_str else cookie_str
            cookies.append({
                "name": "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d",
                "value": value,
                "domain": "hub.weirdhost.xyz",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax"
            })
        
        return cookies
    
    def check_login_status(self, page) -> bool:
        """检查是否已登录"""
        try:
            if "login" in page.url or "auth" in page.url:
                return False
            return True
        except:
            return False
    
    def handle_turnstile(self, page, timeout: int = 120) -> bool:
        """处理 Turnstile 验证"""
        self.log("检查 Turnstile 验证...")
        start = time.time()
        click_count = 0
        
        while time.time() - start < timeout:
            elapsed = int(time.time() - start)
            try:
                content = page.content()
                has_verify = "Verify you are human" in content or "Verifying" in content
                
                # 检查验证是否完成
                response = page.evaluate("""() => {
                    var inp = document.querySelector('input[name="cf-turnstile-response"]');
                    return inp && inp.value && inp.value.length > 50;
                }""")
                
                if response:
                    self.log(f"✓ Turnstile 验证完成 ({elapsed}秒)")
                    return True
                
                if not has_verify:
                    time.sleep(2)
                    content = page.content()
                    if "Verify you are human" not in content and "Verifying" not in content:
                        self.log(f"✓ 无需 Turnstile 验证 ({elapsed}秒)")
                        return True
                
                # 尝试点击验证框
                if click_count < 10 and (click_count == 0 or elapsed % 10 == 0):
                    self.log(f"[{elapsed}秒] 尝试点击验证框...")
                    try:
                        iframe = page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
                        checkbox = iframe.locator('input[type="checkbox"], .cb-i')
                        if checkbox.count() > 0:
                            checkbox.first.click(timeout=5000)
                            click_count += 1
                            time.sleep(3)
                            continue
                    except:
                        pass
                    try:
                        turnstile = page.locator('.cf-turnstile, [data-turnstile]')
                        if turnstile.count() > 0:
                            turnstile.first.click(timeout=5000)
                            click_count += 1
                            time.sleep(3)
                            continue
                    except:
                        pass
                
                if elapsed % 15 == 0 and elapsed > 0:
                    self.log(f"[{elapsed}秒] 等待验证中...")
                time.sleep(2)
            except Exception as e:
                if elapsed % 20 == 0:
                    self.log(f"[{elapsed}秒] 异常: {e}", "WARNING")
                time.sleep(2)
        
        self.log(f"✗ Turnstile 验证超时 ({timeout}秒)", "ERROR")
        return False
    
    def find_renew_button(self, page, server_id: str):
        """查找续期按钮"""
        selectors = [
            'button:has-text("시간추가")',
            'button:has-text("시간 추가")',
            '//button[contains(text(), "시간추가")]',
            '//button[contains(text(), "시간 추가")]',
        ]
        
        for selector in selectors:
            try:
                if selector.startswith('//'):
                    button = page.locator(f'xpath={selector}')
                else:
                    button = page.locator(selector)
                
                if button.count() > 0 and button.first.is_visible():
                    self.log(f"✓ 服务器 {server_id} 找到按钮")
                    return button.first
            except:
                continue
        
        # 备用方法：遍历所有按钮
        try:
            all_buttons = page.locator('button')
            for i in range(all_buttons.count()):
                try:
                    button = all_buttons.nth(i)
                    if button.is_visible():
                        text = button.text_content().strip()
                        if "시간" in text:
                            self.log(f"✓ 服务器 {server_id} 通过文本搜索找到按钮: '{text}'")
                            return button
                except:
                    continue
        except:
            pass
        
        self.log(f"✗ 服务器 {server_id} 未找到续期按钮", "ERROR")
        return None
    
    def process_server(self, page, context, server_url: str) -> Dict:
        """处理单个服务器的续期"""
        server_id = server_url.rstrip('/').split('/')[-1]
        result = {
            "server_id": server_id,
            "success": False,
            "status": "unknown",
            "message": ""
        }
        
        try:
            self.log(f"访问服务器页面: {server_url}")
            page.goto(server_url, wait_until="networkidle", timeout=60000)
            time.sleep(3)
            
            # 检查登录状态
            if not self.check_login_status(page):
                result["status"] = "login_failed"
                result["message"] = "未登录或登录已失效"
                return result
            
            # 截图
            page.screenshot(path=f"server_{server_id}.png")
            
            # 查找续期按钮
            button = self.find_renew_button(page, server_id)
            if not button:
                result["status"] = "no_button"
                result["message"] = "未找到续期按钮"
                return result
            
            # 检查按钮是否可用
            if not button.is_enabled():
                result["status"] = "button_disabled"
                result["message"] = "续期按钮不可点击"
                return result
            
            # 保存点击前的页面内容
            before_content = page.content()
            
            # 点击按钮
            self.log(f"点击续期按钮...")
            button.scroll_into_view_if_needed()
            time.sleep(1)
            button.click()
            time.sleep(3)
            
            # 截图
            page.screenshot(path=f"after_click_{server_id}.png")
            
            # 检查是否需要 Turnstile 验证
            content = page.content()
            if "Verify you are human" in content or "Verifying" in content or page.locator('.cf-turnstile').count() > 0:
                self.log("检测到 Turnstile 验证...")
                if not self.handle_turnstile(page, 120):
                    result["status"] = "turnstile_failed"
                    result["message"] = "Turnstile 验证失败"
                    return result
            
            # 等待结果
            time.sleep(5)
            after_content = page.content()
            
            # 检查结果
            cooldown_keywords = ["아직 서버를 갱신할 수 없습니다", "갱신할 수 없습니다", "기다려주세요", "already", "can't renew", "이미", "한번"]
            success_keywords = ["success", "성공", "added", "추가됨", "갱신되었습니다"]
            
            for kw in cooldown_keywords:
                if kw.lower() in after_content.lower():
                    result["status"] = "cooldown"
                    result["message"] = "冷却期内，已续期过"
                    result["success"] = True  # 冷却期也算成功
                    return result
            
            for kw in success_keywords:
                if kw.lower() in after_content.lower():
                    result["status"] = "success"
                    result["message"] = "续期成功"
                    result["success"] = True
                    return result
            
            # 检查页面是否变化
            if before_content != after_content:
                result["status"] = "changed"
                result["message"] = "页面已变化，可能成功"
                result["success"] = True
            else:
                result["status"] = "no_change"
                result["message"] = "页面无变化"
            
            return result
            
        except Exception as e:
            self.log(f"处理服务器 {server_id} 出错: {e}", "ERROR")
            result["status"] = "error"
            result["message"] = str(e)
            return result
    
    def run(self) -> List[Dict]:
        """主运行函数"""
        from playwright.sync_api import sync_playwright
        
        self.log("=" * 50)
        self.log("WeirdHost 自动续期")
        self.log("=" * 50)
        
        if not self.has_cookie_auth() and not self.has_email_auth():
            self.log("没有可用的认证信息！", "ERROR")
            return [{"server_id": "N/A", "success": False, "status": "no_auth", "message": "无认证信息"}]
        
        if not self.server_list:
            self.log("未设置服务器列表！", "ERROR")
            return [{"server_id": "N/A", "success": False, "status": "no_servers", "message": "无服务器配置"}]
        
        self.log(f"Cookie 认证: {'✓' if self.has_cookie_auth() else '✗'}")
        self.log(f"邮箱认证: {'✓' if self.has_email_auth() else '✗'}")
        self.log(f"服务器数量: {len(self.server_list)}")
        self.log(f"代理: {self.socks_proxy or '无'}")
        
        results = []
        
        with sync_playwright() as p:
            # 启动浏览器
            launch_args = {
                "headless": self.headless,
                "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
            }
            
            if self.socks_proxy:
                proxy_addr = self.socks_proxy.replace("socks5://", "").replace("socks5h://", "")
                launch_args["proxy"] = {"server": f"socks5://{proxy_addr}"}
                self.log(f"使用代理: {proxy_addr}")
            
            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # 添加 cookies
            if self.has_cookie_auth():
                cookies = self.parse_cookie_for_playwright()
                context.add_cookies(cookies)
                self.log("已添加 Cookie")
            
            page = context.new_page()
            page.set_default_timeout(60000)
            
            login_success = False
            
            # 检查 Cookie 登录
            if self.has_cookie_auth():
                self.log("检查 Cookie 登录状态...")
                page.goto(BASE_URL, wait_until="domcontentloaded")
                time.sleep(2)
                if self.check_login_status(page):
                    self.log("✓ Cookie 登录成功")
                    login_success = True
                else:
                    self.log("✗ Cookie 登录失败", "WARNING")
            
            # 尝试邮箱密码登录
            if not login_success and self.has_email_auth():
                self.log("尝试邮箱密码登录...")
                try:
                    page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded")
                    time.sleep(2)
                    page.fill('input[name="username"]', self.email)
                    page.fill('input[name="password"]', self.password)
                    page.click('button[type="submit"]')
                    time.sleep(5)
                    
                    if self.check_login_status(page):
                        self.log("✓ 邮箱密码登录成功")
                        login_success = True
                    else:
                        self.log("✗ 邮箱密码登录失败", "ERROR")
                except Exception as e:
                    self.log(f"邮箱密码登录出错: {e}", "ERROR")
            
            if not login_success:
                self.log("所有登录方式都失败了", "ERROR")
                browser.close()
                return [{"server_id": s.split('/')[-1], "success": False, "status": "login_failed", "message": "登录失败"} for s in self.server_list]
            
            # 处理每个服务器
            for server_url in self.server_list:
                result = self.process_server(page, context, server_url)
                results.append(result)
                self.log(f"服务器 {result['server_id']}: {result['status']} - {result['message']}")
                time.sleep(3)
            
            # 获取新 cookie 并更新
            try:
                for c in context.cookies():
                    if c["name"].startswith("remember_web"):
                        new_cookie = c["name"] + "=" + c["value"]
                        if new_cookie != self.cookie:
                            self.update_github_secret("WEIRDHOST_COOKIE", new_cookie)
                            self.update_github_secret("REMEMBER_WEB_COOKIE", c["value"])
                        break
            except:
                pass
            
            browser.close()
        
        return results
    
    def write_readme(self, results: List[Dict]):
        """写入 README 文件"""
        beijing_time = datetime.now(timezone(timedelta(hours=8)))
        timestamp = beijing_time.strftime('%Y-%m-%d %H:%M:%S')
        
        status_icons = {
            "success": "✅",
            "cooldown": "⏳",
            "changed": "⚠️",
            "no_button": "❌",
            "button_disabled": "❌",
            "login_failed": "❌",
            "turnstile_failed": "❌",
            "error": "💥",
            "no_change": "⚠️",
            "unknown": "❓"
        }
        
        content = f"""# WeirdHost 自动续期

**最后运行**: `{timestamp}` (北京时间)

## 运行结果

"""
        for r in results:
            icon = status_icons.get(r["status"], "❓")
            content += f"- 服务器 `{r['server_id']}`: {icon} {r['message']}\n"
        
        with open('README.md', 'w', encoding='utf-8') as f:
            f.write(content)
        
        self.log("✓ README 已更新")


def main():
    renew = WeirdhostRenew()
    results = renew.run()
    renew.write_readme(results)
    
    # 发送通知
    success_count = sum(1 for r in results if r["success"])
    total_count = len(results)
    
    if success_count == total_count:
        msg = f"✅ <b>WeirdHost 续期完成</b>\n\n全部 {total_count} 个服务器处理成功"
    else:
        msg = f"⚠️ <b>WeirdHost 续期部分完成</b>\n\n成功: {success_count}/{total_count}"
        for r in results:
            if not r["success"]:
                msg += f"\n❌ {r['server_id']}: {r['message']}"
    
    # 查找截图
    screenshot = None
    for f in os.listdir('.'):
        if f.endswith('.png'):
            screenshot = f
            break
    
    renew.send_telegram(msg, screenshot)
    
    # 退出码
    if success_count == 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

