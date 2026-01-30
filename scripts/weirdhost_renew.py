#!/usr/bin/env python3
"""
WeirdHost 自动续期脚本
使用 SeleniumBase UC 模式绕过 Cloudflare
Cookies 用于绕过 reCAPTCHA
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
    prefix = {
        "INFO": "✓",
        "WARN": "⚠",
        "ERROR": "✗",
        "STEP": "→"
    }.get(level, "•")
    print(f"[{timestamp}] [{prefix}] {message}", flush=True)

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
                async with session.get(f"{api_base}/actions/secrets/public-key", headers=headers) as resp:
                    if resp.status != 200:
                        log(f"获取公钥失败: {resp.status}", "ERROR")
                        return False
                    key_data = await resp.json()
                
                public_key = public.PublicKey(key_data['key'].encode(), encoding.Base64Encoder())
                sealed_box = public.SealedBox(public_key)
                encrypted = sealed_box.encrypt(secret_value.encode())
                encrypted_value = base64.b64encode(encrypted).decode()
                
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
        self.screenshot_count = 0
        
        # 验证配置
        if not (self.email and self.password):
            raise ValueError("需要配置 WEIRDHOST_EMAIL 和 WEIRDHOST_PASSWORD")
        
        log(f"配置: 账密=有, Cookies(reCAPTCHA)={'有' if self.cookies_str else '无'}, 代理={'是' if self.use_proxy else '否'}")
    
    def init_browser(self):
        """初始化浏览器"""
        from seleniumbase import Driver
        
        log("初始化浏览器...", "STEP")
        
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
        
        log("浏览器已启动")
    
    def save_screenshot(self, name):
        """保存截图"""
        try:
            self.screenshot_count += 1
            filename = f"{self.screenshot_count:02d}-{name}.png"
            self.driver.save_screenshot(filename)
            log(f"截图: {filename}")
            return filename
        except Exception as e:
            log(f"截图失败: {e}", "WARN")
            return None
    
    def wait_for_cloudflare(self, timeout=60):
        """等待 Cloudflare 验证"""
        log("检测 Cloudflare 验证状态...", "STEP")
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                page_source = self.driver.page_source.lower()
                current_url = self.driver.current_url
                
                # 检查是否在验证中
                cf_checking = any(text in page_source for text in [
                    "checking your browser",
                    "just a moment",
                    "please wait",
                    "verifying you are human",
                    "attention required"
                ])
                
                if cf_checking:
                    elapsed = int(time.time() - start)
                    log(f"等待 Cloudflare 验证... ({elapsed}s)")
                    
                    # 尝试点击验证框
                    try:
                        cf_iframe = self.driver.find_elements("css selector", 
                            "iframe[src*='challenges.cloudflare.com'], iframe[title*='challenge']")
                        if cf_iframe:
                            log("检测到验证框，尝试点击...")
                            self.driver.switch_to.frame(cf_iframe[0])
                            checkbox = self.driver.find_elements("css selector", 
                                "input[type='checkbox'], .cb-lb, span.mark")
                            if checkbox:
                                checkbox[0].click()
                                log("已点击验证框")
                            self.driver.switch_to.default_content()
                    except Exception:
                        self.driver.switch_to.default_content()
                    
                    time.sleep(2)
                    continue
                
                # 验证通过
                elapsed = int(time.time() - start)
                log(f"Cloudflare 验证通过 ({elapsed}s)")
                return True
                
            except Exception as e:
                log(f"CF检测异常: {e}", "WARN")
                time.sleep(1)
        
        log("Cloudflare 验证超时", "ERROR")
        return False
    
    def load_recaptcha_cookies(self):
        """加载 reCAPTCHA Cookies"""
        if not self.cookies_str:
            log("无 reCAPTCHA Cookies", "WARN")
            return False
        
        try:
            cookies = json.loads(self.cookies_str)
            loaded = 0
            
            for cookie in cookies:
                try:
                    cookie_dict = {
                        'name': cookie['name'],
                        'value': cookie['value'],
                    }
                    # 可选字段
                    if 'domain' in cookie:
                        cookie_dict['domain'] = cookie['domain']
                    if 'path' in cookie:
                        cookie_dict['path'] = cookie['path']
                    
                    self.driver.add_cookie(cookie_dict)
                    loaded += 1
                except Exception as e:
                    pass
            
            log(f"已加载 {loaded} 个 reCAPTCHA Cookies")
            return loaded > 0
            
        except json.JSONDecodeError as e:
            log(f"Cookies JSON 解析失败: {e}", "ERROR")
            log(f"Cookies 内容前100字符: {self.cookies_str[:100]}", "ERROR")
            return False
        except Exception as e:
            log(f"加载 Cookies 失败: {e}", "ERROR")
            return False
    
    def login(self):
        """登录流程"""
        log("=" * 40)
        log("开始登录流程", "STEP")
        log("=" * 40)
        
        try:
            # 1. 访问登录页
            log("访问登录页...", "STEP")
            self.driver.get(f"{self.base_url}/login")
            time.sleep(3)
            self.save_screenshot("login-page-open")
            
            # 2. 等待 Cloudflare
            if not self.wait_for_cloudflare():
                self.save_screenshot("cf-failed")
                return False
            self.save_screenshot("cf-passed")
            
            # 3. 等待登录表单加载
            log("等待登录表单...", "STEP")
            time.sleep(2)
            
            # 4. 填写邮箱
            log("输入邮箱...", "STEP")
            email_selectors = [
                "input[name='email']",
                "input[name='username']", 
                "input[type='email']",
                "#inputEmail",
                "input#email"
            ]
            
            email_input = None
            for selector in email_selectors:
                try:
                    elements = self.driver.find_elements("css selector", selector)
                    for elem in elements:
                        if elem.is_displayed():
                            email_input = elem
                            break
                    if email_input:
                        break
                except Exception:
                    continue
            
            if not email_input:
                log("未找到邮箱输入框", "ERROR")
                self.save_screenshot("no-email-input")
                return False
            
            email_input.clear()
            for char in self.email:
                email_input.send_keys(char)
                time.sleep(0.03)
            log(f"邮箱已输入: {self.email[:3]}***")
            
            # 5. 填写密码
            log("输入密码...", "STEP")
            password_selectors = [
                "input[name='password']",
                "input[type='password']",
                "#inputPassword",
                "input#password"
            ]
            
            password_input = None
            for selector in password_selectors:
                try:
                    elements = self.driver.find_elements("css selector", selector)
                    for elem in elements:
                        if elem.is_displayed():
                            password_input = elem
                            break
                    if password_input:
                        break
                except Exception:
                    continue
            
            if not password_input:
                log("未找到密码输入框", "ERROR")
                self.save_screenshot("no-password-input")
                return False
            
            password_input.clear()
            for char in self.password:
                password_input.send_keys(char)
                time.sleep(0.03)
            log("密码已输入: ***")
            
            self.save_screenshot("form-filled")
            
            # 6. 勾选同意条款（如果有）
            log("检查同意条款...", "STEP")
            agree_selectors = [
                "input[name='accepttos']",
                "input[name='agree']",
                "input[name='terms']",
                "input[type='checkbox'][id*='agree']",
                "input[type='checkbox'][id*='terms']",
                "input[type='checkbox'][id*='tos']"
            ]
            
            for selector in agree_selectors:
                try:
                    checkbox = self.driver.find_element("css selector", selector)
                    if checkbox.is_displayed() and not checkbox.is_selected():
                        checkbox.click()
                        log("已勾选同意条款")
                        break
                except Exception:
                    continue
            
            # 7. 加载 reCAPTCHA Cookies（关键步骤！）
            log("加载 reCAPTCHA Cookies...", "STEP")
            self.load_recaptcha_cookies()
            time.sleep(1)
            
            self.save_screenshot("before-submit")
            
            # 8. 提交登录
            log("提交登录...", "STEP")
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "#login",
                "button.btn-primary",
                "button[name='login']",
                "//button[contains(text(),'Login')]",
                "//button[contains(text(),'Sign In')]",
                "//input[@value='Login']"
            ]
            
            submit_btn = None
            for selector in submit_selectors:
                try:
                    if selector.startswith("//"):
                        elements = self.driver.find_elements("xpath", selector)
                    else:
                        elements = self.driver.find_elements("css selector", selector)
                    
                    for elem in elements:
                        if elem.is_displayed():
                            submit_btn = elem
                            break
                    if submit_btn:
                        break
                except Exception:
                    continue
            
            if not submit_btn:
                log("未找到提交按钮", "ERROR")
                self.save_screenshot("no-submit-btn")
                return False
            
            submit_btn.click()
            log("已点击登录按钮")
            
            # 9. 等待登录结果
            time.sleep(5)
            self.wait_for_cloudflare()
            self.save_screenshot("after-submit")
            
            # 10. 验证登录状态
            if self.check_login_status():
                log("登录成功！")
                return True
            else:
                log("登录失败", "ERROR")
                self.save_screenshot("login-failed")
                return False
                
        except Exception as e:
            log(f"登录异常: {e}", "ERROR")
            traceback.print_exc()
            self.save_screenshot("login-error")
            return False
    
    def check_login_status(self):
        """检查登录状态"""
        try:
            current_url = self.driver.current_url
            page_source = self.driver.page_source.lower()
            
            # 检查是否在登录页（未登录）
            login_indicators = [
                "login" in current_url and "clientarea" not in current_url,
                "inputpassword" in page_source and "logout" not in page_source,
            ]
            
            if any(login_indicators):
                return False
            
            # 检查是否已登录
            logged_in_indicators = [
                "clientarea" in current_url,
                "logout" in page_source,
                "my services" in page_source,
                "my account" in page_source,
                "welcome back" in page_source,
            ]
            
            return any(logged_in_indicators)
            
        except Exception as e:
            log(f"检查登录状态失败: {e}", "ERROR")
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
        log("=" * 40)
        log("获取服务列表", "STEP")
        log("=" * 40)
        
        try:
            self.driver.get(f"{self.base_url}/clientarea.php?action=services")
            time.sleep(5)
            self.wait_for_cloudflare()
            self.save_screenshot("services-page")
            
            services = []
            page_source = self.driver.page_source
            
            # 方法1: 查找表格中的服务
            rows = self.driver.find_elements("css selector", "table tbody tr")
            for row in rows:
                try:
                    links = row.find_elements("css selector", "a[href*='productdetails']")
                    for link in links:
                        href = link.get_attribute("href")
                        if "id=" in href:
                            service_id = href.split("id=")[-1].split("&")[0]
                            service_name = link.text.strip() or f"Service {service_id}"
                            
                            row_text = row.text.lower()
                            if "active" in row_text:
                                status = "Active"
                            elif "suspended" in row_text:
                                status = "Suspended"
                            elif "pending" in row_text:
                                status = "Pending"
                            else:
                                status = "Unknown"
                            
                            services.append({
                                "id": service_id,
                                "name": service_name,
                                "status": status,
                                "url": href
                            })
                except Exception:
                    continue
            
            # 方法2: 查找服务面板
            if not services:
                panels = self.driver.find_elements("css selector", ".panel, .card, .service-item, [class*='product']")
                for panel in panels:
                    try:
                        links = panel.find_elements("css selector", "a[href*='productdetails'], a[href*='service']")
                        for link in links:
                            href = link.get_attribute("href")
                            if "id=" in href:
                                service_id = href.split("id=")[-1].split("&")[0]
                                service_name = link.text.strip() or panel.text.split('\n')[0][:50]
                                services.append({
                                    "id": service_id,
                                    "name": service_name,
                                    "status": "Unknown",
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
            for s in unique_services:
                log(f"  - {s['name']} (ID: {s['id']}, 状态: {s['status']})")
            
            return unique_services
            
        except Exception as e:
            log(f"获取服务列表失败: {e}", "ERROR")
            self.save_screenshot("services-error")
            return []
    
    def renew_service(self, service):
        """续期单个服务"""
        service_id = service["id"]
        service_name = service["name"]
        
        log(f"续期服务: {service_name} (ID: {service_id})", "STEP")
        
        try:
            # 访问服务详情页
            detail_url = f"{self.base_url}/clientarea.php?action=productdetails&id={service_id}"
            self.driver.get(detail_url)
            time.sleep(5)
            self.wait_for_cloudflare()
            self.save_screenshot(f"service-{service_id}-detail")
            
            page_source = self.driver.page_source
            page_lower = page_source.lower()
            
            # 检查到期时间
            log("检查服务到期时间...")
            
            # 查找续期按钮
            renew_selectors = [
                "a[href*='renew']",
                "button[onclick*='renew']",
                ".renew-btn",
                "a.btn[href*='renew']",
                "//a[contains(text(),'Renew')]",
                "//button[contains(text(),'Renew')]",
                "//a[contains(@href,'renew')]",
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
                            log(f"找到续期按钮: {selector}")
                            break
                    if renew_btn:
                        break
                except Exception:
                    continue
            
            if renew_btn:
                log("点击续期按钮...")
                renew_btn.click()
                time.sleep(5)
                self.wait_for_cloudflare()
                self.save_screenshot(f"service-{service_id}-renew-clicked")
                
                # 检查是否需要确认
                confirm_selectors = [
                    "button[type='submit']",
                    "input[type='submit'][value*='Renew']",
                    ".btn-primary",
                    "//button[contains(text(),'Confirm')]",
                    "//button[contains(text(),'Submit')]",
                ]
                
                for selector in confirm_selectors:
                    try:
                        if selector.startswith("//"):
                            confirm_btn = self.driver.find_element("xpath", selector)
                        else:
                            confirm_btn = self.driver.find_element("css selector", selector)
                        
                        if confirm_btn.is_displayed():
                            log("点击确认按钮...")
                            confirm_btn.click()
                            time.sleep(3)
                            break
                    except Exception:
                        continue
                
                self.save_screenshot(f"service-{service_id}-renewed")
                
                # 检查结果
                result_page = self.driver.page_source.lower()
                if "success" in result_page or "renewed" in result_page or "thank" in result_page:
                    log(f"服务 {service_name} 续期成功！")
                    return {"success": True, "service": service_name, "message": "续期成功"}
                else:
                    log(f"服务 {service_name} 续期操作已完成")
                    return {"success": True, "service": service_name, "message": "续期操作已完成"}
            
            else:
                # 检查是否已经是最长期限或无需续期
                if "maximum" in page_lower or "already" in page_lower:
                    log(f"服务 {service_name} 已是最长期限")
                    return {"success": True, "service": service_name, "message": "已是最长期限"}
                
                if "no renewal" in page_lower or "cannot be renewed" in page_lower:
                    log(f"服务 {service_name} 无法续期")
                    return {"success": True, "service": service_name, "message": "无需续期"}
                
                log(f"未找到续期按钮", "WARN")
                return {"success": False, "service": service_name, "message": "未找到续期按钮"}
                
        except Exception as e:
            log(f"续期服务 {service_name} 失败: {e}", "ERROR")
            self.save_screenshot(f"service-{service_id}-error")
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
            
            # 登录
            if not self.login():
                raise Exception("登录失败，请检查账号密码或 reCAPTCHA Cookies")
            
            # 登录成功后导出并更新 Cookies
            new_cookies = self.export_cookies()
            if new_cookies:
                await self.secrets_manager.update_secret("WEIRDHOST_COOKIES", new_cookies)
            
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
            self.save_screenshot("fatal-error")
            
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
        
        # 发送最后一张截图
        screenshots = sorted([f for f in os.listdir(".") if f.endswith(".png")])
        if screenshots:
            await self.notifier.send_photo(screenshots[-1], f"📸 最终截图")


async def main():
    """主入口"""
    log("=" * 50)
    log("WeirdHost 自动续期脚本启动")
    log("=" * 50)
    
    try:
        renewer = WeirdHostRenewer()
        results = await renewer.run()
        
        success = all(r.get("success") for r in results)
        if success:
            log("所有服务续期成功")
            sys.exit(0)
        else:
            log("部分服务续期失败", "WARN")
            sys.exit(0)
            
    except Exception as e:
        log(f"脚本执行失败: {e}", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

