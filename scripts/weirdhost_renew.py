#!/usr/bin/env python3
"""
WeirdHost 自动续期脚本
使用 SeleniumBase UC 模式绕过 Cloudflare
"""

import os
import sys
import json
import time
import base64
import asyncio
import traceback
from datetime import datetime
from urllib.parse import quote

# 环境检测
IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

def log(message, level="INFO"):
    """统一日志格式"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

def setup_display():
    """设置虚拟显示"""
    if IS_GITHUB_ACTIONS:
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            log("虚拟显示已启动")
            return display
        except Exception as e:
            log(f"虚拟显示启动失败: {e}", "WARN")
    return None

class TelegramNotifier:
    """Telegram 通知"""
    
    def __init__(self):
        self.bot_token = os.environ.get('TG_BOT_TOKEN', '')
        self.chat_id = os.environ.get('TG_CHAT_ID', '')
        self.enabled = bool(self.bot_token and self.chat_id)
        if self.enabled:
            log("Telegram 通知已启用")
        else:
            log("Telegram 通知未配置", "WARN")
    
    async def send_message(self, message, parse_mode="HTML"):
        """发送消息"""
        if not self.enabled:
            return False
        
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=30) as resp:
                    if resp.status == 200:
                        log("Telegram 消息发送成功")
                        return True
                    else:
                        log(f"Telegram 发送失败: {resp.status}", "WARN")
                        return False
        except Exception as e:
            log(f"Telegram 发送异常: {e}", "ERROR")
            return False
    
    async def send_photo(self, photo_path, caption=""):
        """发送图片"""
        if not self.enabled or not os.path.exists(photo_path):
            return False
        
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
            data = aiohttp.FormData()
            data.add_field('chat_id', self.chat_id)
            data.add_field('caption', caption[:1024], content_type='text/plain')
            data.add_field('parse_mode', 'HTML')
            data.add_field('photo', open(photo_path, 'rb'), filename=os.path.basename(photo_path))
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=60) as resp:
                    return resp.status == 200
        except Exception as e:
            log(f"发送图片失败: {e}", "ERROR")
            return False

class GitHubSecretsManager:
    """GitHub Secrets 管理"""
    
    def __init__(self):
        self.token = os.environ.get('REPO_TOKEN', '')
        self.repository = os.environ.get('GITHUB_REPOSITORY', '')
        self.enabled = bool(self.token and self.repository)
        if self.enabled:
            log("GitHub Secrets 管理已启用")
    
    async def update_secret(self, secret_name, secret_value):
        """更新 Secret"""
        if not self.enabled:
            log("GitHub Secrets 未配置", "WARN")
            return False
        
        try:
            import aiohttp
            from nacl import public, encoding
            
            api_base = f"https://api.github.com/repos/{self.repository}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            
            async with aiohttp.ClientSession() as session:
                # 获取公钥
                async with session.get(f"{api_base}/actions/secrets/public-key", headers=headers) as resp:
                    if resp.status != 200:
                        log(f"获取公钥失败: {resp.status}", "ERROR")
                        return False
                    key_data = await resp.json()
                
                # 加密
                public_key = public.PublicKey(key_data['key'].encode(), encoding.Base64Encoder())
                sealed_box = public.SealedBox(public_key)
                encrypted = sealed_box.encrypt(secret_value.encode())
                encrypted_value = base64.b64encode(encrypted).decode()
                
                # 更新
                payload = {
                    "encrypted_value": encrypted_value,
                    "key_id": key_data['key_id']
                }
                async with session.put(f"{api_base}/actions/secrets/{secret_name}", headers=headers, json=payload) as resp:
                    if resp.status in [201, 204]:
                        log(f"Secret {secret_name} 更新成功")
                        return True
                    else:
                        log(f"更新 Secret 失败: {resp.status}", "ERROR")
                        return False
        except Exception as e:
            log(f"更新 Secret 异常: {e}", "ERROR")
            return False

class WeirdHostRenewer:
    """WeirdHost 续期主类"""
    
    def __init__(self):
        self.email = os.environ.get('WEIRDHOST_EMAIL', '')
        self.password = os.environ.get('WEIRDHOST_PASSWORD', '')
        self.cookies_str = os.environ.get('WEIRDHOST_COOKIES', '')
        self.use_proxy = os.environ.get('USE_PROXY', 'false').lower() == 'true'
        
        self.base_url = "https://weirdhost.net"
        self.driver = None
        self.notifier = TelegramNotifier()
        self.secrets_manager = GitHubSecretsManager()
        
        # 验证配置
        if not self.cookies_str and not (self.email and self.password):
            raise ValueError("需要配置 WEIRDHOST_COOKIES 或 WEIRDHOST_EMAIL + WEIRDHOST_PASSWORD")
        
        log(f"配置: Cookies={'有' if self.cookies_str else '无'}, 账密={'有' if self.email else '无'}, 代理={'是' if self.use_proxy else '否'}")
    
    def init_browser(self):
        """初始化浏览器"""
        from seleniumbase import Driver
        
        log("初始化浏览器...")
        
        driver_args = {
            "browser": "chrome",
            "uc": True,
            "headless": True,
            "agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        
        if self.use_proxy:
            driver_args["proxy"] = "http://127.0.0.1:8080"
            log("使用代理: http://127.0.0.1:8080")
        
        self.driver = Driver(**driver_args)
        self.driver.set_window_size(1920, 1080)
        self.driver.set_page_load_timeout(60)
        self.driver.implicitly_wait(10)
        
        log("浏览器初始化完成")
    
    def save_screenshot(self, name):
        """保存截图"""
        try:
            filename = f"{name}_{datetime.now().strftime('%H%M%S')}.png"
            self.driver.save_screenshot(filename)
            log(f"截图已保存: {filename}")
            return filename
        except Exception as e:
            log(f"截图失败: {e}", "WARN")
            return None
    
    def load_cookies(self):
        """加载 Cookies"""
        if not self.cookies_str:
            return False
        
        try:
            # 先访问网站
            self.driver.get(self.base_url)
            time.sleep(3)
            
            # 解析并添加 cookies
            cookies = json.loads(self.cookies_str)
            for cookie in cookies:
                cookie_dict = {
                    'name': cookie['name'],
                    'value': cookie['value'],
                    'domain': cookie.get('domain', '.weirdhost.net'),
                    'path': cookie.get('path', '/')
                }
                try:
                    self.driver.add_cookie(cookie_dict)
                except Exception:
                    pass
            
            log(f"已加载 {len(cookies)} 个 Cookies")
            return True
        except Exception as e:
            log(f"加载 Cookies 失败: {e}", "ERROR")
            return False
    
    def check_login_status(self):
        """检查登录状态"""
        try:
            self.driver.get(f"{self.base_url}/clientarea.php")
            time.sleep(5)
            
            # 检查是否在登录页
            current_url = self.driver.current_url
            page_source = self.driver.page_source.lower()
            
            if "login" in current_url or "password" in page_source and "inputPassword" in self.driver.page_source:
                log("未登录，需要登录")
                return False
            
            if "my services" in page_source or "clientarea" in current_url:
                log("已登录")
                return True
            
            return False
        except Exception as e:
            log(f"检查登录状态失败: {e}", "ERROR")
            return False
    
    def wait_for_cloudflare(self, timeout=30):
        """等待 Cloudflare 验证"""
        log("检查 Cloudflare...")
        start = time.time()
        
        while time.time() - start < timeout:
            page_source = self.driver.page_source.lower()
            
            # 检查是否还在验证
            if "checking your browser" in page_source or "just a moment" in page_source:
                log("等待 Cloudflare 验证...")
                time.sleep(2)
                continue
            
            # 检查是否有验证框
            try:
                cf_iframe = self.driver.find_elements("css selector", "iframe[src*='challenges.cloudflare.com']")
                if cf_iframe:
                    log("检测到 Cloudflare 验证框，尝试点击...")
                    time.sleep(3)
                    # UC 模式通常会自动处理
            except Exception:
                pass
            
            # 验证通过
            if "checking" not in page_source:
                log("Cloudflare 验证通过")
                return True
            
            time.sleep(1)
        
        log("Cloudflare 验证超时", "WARN")
        return False
    
    def login(self):
        """登录"""
        if not self.email or not self.password:
            log("无账密配置，跳过登录", "WARN")
            return False
        
        try:
            log("开始登录...")
            self.driver.get(f"{self.base_url}/login")
            time.sleep(3)
            
            # 等待 Cloudflare
            self.wait_for_cloudflare()
            self.save_screenshot("login_page")
            
            # 输入邮箱
            email_input = self.driver.find_element("css selector", "input[name='email'], input[name='username'], #inputEmail")
            email_input.clear()
            for char in self.email:
                email_input.send_keys(char)
                time.sleep(0.05)
            
            # 输入密码
            password_input = self.driver.find_element("css selector", "input[name='password'], #inputPassword")
            password_input.clear()
            for char in self.password:
                password_input.send_keys(char)
                time.sleep(0.05)
            
            time.sleep(1)
            self.save_screenshot("before_login_click")
            
            # 点击登录
            login_btn = self.driver.find_element("css selector", "button[type='submit'], input[type='submit'], #login")
            login_btn.click()
            
            time.sleep(5)
            self.wait_for_cloudflare()
            self.save_screenshot("after_login")
            
            # 验证登录
            if self.check_login_status():
                log("登录成功")
                return True
            else:
                log("登录失败", "ERROR")
                return False
                
        except Exception as e:
            log(f"登录异常: {e}", "ERROR")
            self.save_screenshot("login_error")
            return False
    
    def export_cookies(self):
        """导出 Cookies"""
        try:
            cookies = self.driver.get_cookies()
            cookies_json = json.dumps(cookies)
            log(f"导出 {len(cookies)} 个 Cookies")
            return cookies_json
        except Exception as e:
            log(f"导出 Cookies 失败: {e}", "ERROR")
            return None
    
    def get_services(self):
        """获取服务列表"""
        try:
            log("获取服务列表...")
            self.driver.get(f"{self.base_url}/clientarea.php?action=services")
            time.sleep(5)
            self.wait_for_cloudflare()
            self.save_screenshot("services_page")
            
            services = []
            
            # 查找服务行
            rows = self.driver.find_elements("css selector", "table tbody tr, .service-item, [class*='service']")
            
            for row in rows:
                try:
                    text = row.text
                    if not text.strip():
                        continue
                    
                    # 查找服务链接
                    links = row.find_elements("css selector", "a[href*='productdetails'], a[href*='service']")
                    for link in links:
                        href = link.get_attribute("href")
                        if "id=" in href:
                            service_id = href.split("id=")[-1].split("&")[0]
                            service_name = link.text.strip() or f"Service {service_id}"
                            
                            # 检查状态
                            status = "Active" if "active" in text.lower() else "Unknown"
                            
                            services.append({
                                "id": service_id,
                                "name": service_name,
                                "status": status,
                                "url": href
                            })
                except Exception:
                    continue
            
            # 去重
            seen = set()
            unique_services = []
            for s in services:
                if s["id"] not in seen:
                    seen.add(s["id"])
                    unique_services.append(s)
            
            log(f"找到 {len(unique_services)} 个服务")
            return unique_services
            
        except Exception as e:
            log(f"获取服务列表失败: {e}", "ERROR")
            self.save_screenshot("services_error")
            return []
    
    def renew_service(self, service):
        """续期单个服务"""
        service_id = service["id"]
        service_name = service["name"]
        
        try:
            log(f"续期服务: {service_name} (ID: {service_id})")
            
            # 访问服务详情页
            detail_url = f"{self.base_url}/clientarea.php?action=productdetails&id={service_id}"
            self.driver.get(detail_url)
            time.sleep(5)
            self.wait_for_cloudflare()
            self.save_screenshot(f"service_{service_id}_detail")
            
            page_source = self.driver.page_source
            
            # 查找续期按钮
            renew_selectors = [
                "a[href*='renew']",
                "button[onclick*='renew']",
                ".renew-btn",
                "a.btn[href*='renew']",
                "//a[contains(text(),'Renew')]",
                "//button[contains(text(),'Renew')]",
                "//a[contains(text(),'续期')]",
                "//a[contains(text(),'续费')]",
            ]
            
            renew_btn = None
            for selector in renew_selectors:
                try:
                    if selector.startswith("//"):
                        elements = self.driver.find_elements("xpath", selector)
                    else:
                        elements = self.driver.find_elements("css selector", selector)
                    
                    for elem in elements:
                        if elem.is_displayed():
                            renew_btn = elem
                            break
                    if renew_btn:
                        break
                except Exception:
                    continue
            
            if renew_btn:
                log("找到续期按钮，点击...")
                renew_btn.click()
                time.sleep(5)
                self.wait_for_cloudflare()
                self.save_screenshot(f"service_{service_id}_renew_clicked")
                
                # 检查是否需要确认
                confirm_selectors = [
                    "button[type='submit']",
                    "input[type='submit']",
                    ".btn-primary",
                    "//button[contains(text(),'Confirm')]",
                    "//button[contains(text(),'确认')]",
                ]
                
                for selector in confirm_selectors:
                    try:
                        if selector.startswith("//"):
                            confirm_btn = self.driver.find_element("xpath", selector)
                        else:
                            confirm_btn = self.driver.find_element("css selector", selector)
                        
                        if confirm_btn.is_displayed():
                            confirm_btn.click()
                            time.sleep(3)
                            break
                    except Exception:
                        continue
                
                self.save_screenshot(f"service_{service_id}_renewed")
                log(f"服务 {service_name} 续期操作完成")
                return {"success": True, "service": service_name, "message": "续期成功"}
            else:
                # 检查是否已经是最长期限
                if "already" in page_source.lower() or "maximum" in page_source.lower():
                    log(f"服务 {service_name} 已是最长期限")
                    return {"success": True, "service": service_name, "message": "已是最长期限"}
                
                log(f"未找到续期按钮", "WARN")
                return {"success": False, "service": service_name, "message": "未找到续期按钮"}
                
        except Exception as e:
            log(f"续期服务 {service_name} 失败: {e}", "ERROR")
            self.save_screenshot(f"service_{service_id}_error")
            return {"success": False, "service": service_name, "message": str(e)}
    
    async def run(self):
        """主运行流程"""
        display = None
        results = []
        
        try:
            # 启动虚拟显示
            display = setup_display()
            
            # 初始化浏览器
            self.init_browser()
            
            # 尝试使用 Cookies 登录
            logged_in = False
            if self.cookies_str:
                self.load_cookies()
                logged_in = self.check_login_status()
            
            # Cookies 失效则使用账密登录
            if not logged_in:
                if self.email and self.password:
                    logged_in = self.login()
                    
                    # 登录成功后更新 Cookies
                    if logged_in:
                        new_cookies = self.export_cookies()
                        if new_cookies:
                            await self.secrets_manager.update_secret("WEIRDHOST_COOKIES", new_cookies)
            
            if not logged_in:
                raise Exception("登录失败")
            
            # 获取服务列表
            services = self.get_services()
            
            if not services:
                log("未找到任何服务", "WARN")
                results.append({"success": False, "service": "N/A", "message": "未找到服务"})
            else:
                # 续期每个服务
                for service in services:
                    result = self.renew_service(service)
                    results.append(result)
                    time.sleep(2)
            
            # 发送通知
            await self.send_notification(results)
            
            return results
            
        except Exception as e:
            log(f"运行失败: {e}", "ERROR")
            traceback.print_exc()
            self.save_screenshot("fatal_error")
            
            # 发送错误通知
            await self.notifier.send_message(
                f"❌ <b>WeirdHost 续期失败</b>\n\n"
                f"错误: {str(e)}\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            raise
            
        finally:
            # 清理
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            
            if display:
                try:
                    display.stop()
                except Exception:
                    pass
    
    async def send_notification(self, results):
        """发送结果通知"""
        success_count = sum(1 for r in results if r.get("success"))
        total_count = len(results)
        
        # 构建消息
        if success_count == total_count:
            status_emoji = "✅"
            status_text = "全部成功"
        elif success_count > 0:
            status_emoji = "⚠️"
            status_text = "部分成功"
        else:
            status_emoji = "❌"
            status_text = "全部失败"
        
        message = f"{status_emoji} <b>WeirdHost 续期报告</b>\n\n"
        message += f"📊 结果: {status_text} ({success_count}/{total_count})\n"
        message += f"🕐 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        for r in results:
            emoji = "✅" if r.get("success") else "❌"
            message += f"{emoji} {r.get('service', 'Unknown')}: {r.get('message', 'N/A')}\n"
        
        await self.notifier.send_message(message)
        
        # 发送截图
        for f in os.listdir("."):
            if f.endswith(".png"):
                await self.notifier.send_photo(f, f"📸 {f}")
                break


async def main():
    """主入口"""
    log("=" * 50)
    log("WeirdHost 自动续期脚本启动")
    log("=" * 50)
    
    try:
        renewer = WeirdHostRenewer()
        results = await renewer.run()
        
        # 检查结果
        success = all(r.get("success") for r in results)
        if success:
            log("所有服务续期成功")
            sys.exit(0)
        else:
            log("部分服务续期失败", "WARN")
            sys.exit(0)  # 不要因为部分失败而导致 workflow 失败
            
    except Exception as e:
        log(f"脚本执行失败: {e}", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
