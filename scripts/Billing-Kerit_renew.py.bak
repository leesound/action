#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Billing Kerit 自动续订脚本
功能：
1. 启动浏览器，注入 Cookie
2. 访问首页
3. 进入续订页面（Free Plans）
4. 检查续订按钮，可续订则执行续订流程
5. 发送 Telegram 通知（包含截图）
6. 自动更新 Cookie 到 GitHub Secrets
"""

import os
import sys
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote, quote

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] playwright 未安装，请运行: pip install playwright")
    sys.exit(1)

# ==================== 配置 ====================
BASE_URL = "https://billing.kerit.cloud"
SESSION_URL = f"{BASE_URL}/session"
FREE_PANEL_URL = f"{BASE_URL}/free_panel"
OUTPUT_DIR = Path("output/screenshots")

# ==================== 工具函数 ====================

def log(level: str, msg: str):
    """统一日志格式"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def env_or_throw(name: str) -> str:
    """获取环境变量，不存在则抛出异常"""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def env_or_default(name: str, default: str = "") -> str:
    """获取环境变量，不存在则返回默认值"""
    return os.environ.get(name, default)


def ensure_output_dir():
    """确保输出目录存在"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log("INFO", f"输出目录已就绪")


def screenshot_path(name: str) -> str:
    """生成截图路径"""
    return str(OUTPUT_DIR / f"{name}.png")


def parse_cookie_string(cookie_str: str, domain: str) -> list:
    """解析 Cookie 字符串为 Playwright cookie 格式"""
    if not cookie_str:
        return []
    
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        
        eq_index = item.index("=")
        name = item[:eq_index].strip()
        value = item[eq_index + 1:].strip()
        
        try:
            value = unquote(value)
        except:
            pass
        
        # 根据 cookie 名称设置属性
        http_only = any(x in name.lower() for x in ["session", "clearance"])
        
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": http_only,
            "sameSite": "Lax"
        })
    
    # 只显示 Cookie 名称，隐藏值
    cookie_names = [c["name"] for c in cookies]
    log("INFO", f"解析到 {len(cookies)} 个 Cookie: {', '.join(cookie_names)}")
    return cookies


def save_cookies_for_update(cookies: list) -> str:
    """保存重要 Cookie 用于后续更新"""
    # 只保留关键 Cookie
    important_names = ["session_id", "cf_clearance"]
    
    filtered = []
    for c in cookies:
        name = c.get("name", "")
        if name in important_names:
            filtered.append(c)
    
    if not filtered:
        log("WARN", "未找到关键 Cookie")
        return ""
    
    cookie_string = "; ".join([f"{c['name']}={quote(str(c.get('value', '')), safe='')}" for c in filtered])
    
    cookie_file = OUTPUT_DIR / "new_cookies.txt"
    cookie_file.write_text(cookie_string)
    log("INFO", f"新 Cookie 已保存 ({len(filtered)} 个)")
    
    return cookie_string


def update_github_secret(secret_name: str, secret_value: str) -> bool:
    """更新 GitHub Secret"""
    repo_token = env_or_default("REPO_TOKEN")
    if not repo_token:
        log("WARN", "REPO_TOKEN 未设置，跳过 Secret 更新")
        return False
    
    github_repo = os.environ.get("GITHUB_REPOSITORY")
    if not github_repo:
        log("WARN", "GITHUB_REPOSITORY 未设置，跳过 Secret 更新")
        return False
    
    try:
        from nacl import encoding, public
        
        headers = {
            "Authorization": f"Bearer {repo_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # 获取公钥
        pub_key_url = f"https://api.github.com/repos/{github_repo}/actions/secrets/public-key"
        resp = requests.get(pub_key_url, headers=headers, timeout=30)
        resp.raise_for_status()
        pub_key_data = resp.json()
        
        # 加密
        public_key = public.PublicKey(pub_key_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = encoding.Base64Encoder().encode(encrypted).decode("utf-8")
        
        # 更新 Secret
        secret_url = f"https://api.github.com/repos/{github_repo}/actions/secrets/{secret_name}"
        resp = requests.put(
            secret_url,
            headers=headers,
            json={
                "encrypted_value": encrypted_value,
                "key_id": pub_key_data["key_id"]
            },
            timeout=30
        )
        resp.raise_for_status()
        
        log("INFO", f"GitHub Secret 已更新")
        return True
        
    except ImportError:
        log("WARN", "PyNaCl 未安装，跳过 Secret 更新")
        return False
    except Exception as e:
        log("ERROR", f"更新 GitHub Secret 失败: {e}")
        return False


def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_file: str = None):
    """发送 Telegram 通知（带截图）"""
    bot_token = env_or_default("TG_BOT_TOKEN")
    chat_id = env_or_default("TG_CHAT_ID")
    
    if not bot_token or not chat_id:
        log("WARN", "Telegram 配置不完整，跳过通知")
        return
    
    try:
        status = "✅ 成功" if ok else "❌ 失败"
        text_lines = [
            f"📋 Billing Kerit 自动续订",
            f"",
            f"状态: {status}",
            f"阶段: {stage}",
        ]
        if msg:
            text_lines.append(f"")
            text_lines.append(msg)
        text_lines.append(f"")
        text_lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        caption = "\n".join(text_lines)
        
        # 发送带截图的消息
        if screenshot_file and Path(screenshot_file).exists():
            photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            with open(screenshot_file, "rb") as f:
                resp = requests.post(
                    photo_url, 
                    data={
                        "chat_id": chat_id,
                        "caption": caption
                    }, 
                    files={"photo": f}, 
                    timeout=60
                )
                
                if resp.status_code == 200:
                    log("INFO", "Telegram 通知已发送（带截图）")
                else:
                    log("WARN", f"Telegram 图片发送失败")
                    send_text_only(bot_token, chat_id, caption)
        else:
            send_text_only(bot_token, chat_id, caption)
        
    except Exception as e:
        log("WARN", f"Telegram 通知失败: {e}")


def send_text_only(bot_token: str, chat_id: str, text: str):
    """只发送文本消息"""
    try:
        send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(send_url, json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True
        }, timeout=30)
        
        if resp.status_code == 200:
            log("INFO", "Telegram 文本通知已发送")
        else:
            log("WARN", f"Telegram 消息发送失败")
    except Exception as e:
        log("WARN", f"发送文本失败: {e}")


# ==================== 主逻辑 ====================

def main():
    """主函数"""
    log("INFO", "=" * 50)
    log("INFO", "🚀 Billing Kerit 自动续订脚本启动")
    log("INFO", "=" * 50)
    
    ensure_output_dir()
    
    # 获取 Cookie
    try:
        preset_cookies = env_or_throw("BILLING_KERIT_COOKIES")
    except ValueError as e:
        log("ERROR", str(e))
        notify_telegram(False, "初始化失败", "Cookie 环境变量未设置")
        sys.exit(1)
    
    log("INFO", "🌐 启动浏览器...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ]
        )
        
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Asia/Shanghai"
        )
        
        page = context.new_page()
        
        # 反检测
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        
        final_screenshot = None
        
        try:
            # 1. 注入 Cookie
            log("INFO", "🍪 注入 Cookie...")
            cookies = parse_cookie_string(preset_cookies, "billing.kerit.cloud")
            if cookies:
                context.add_cookies(cookies)
            
            # 2. 访问首页
            log("INFO", f"🔗 访问 {SESSION_URL}...")
            page.goto(SESSION_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            
            current_url = page.url
            title = page.title()
            log("INFO", f"当前 URL: {current_url}")
            log("INFO", f"页面标题: {title}")
            
            # 截图首页
            sp_home = screenshot_path("01-homepage")
            page.screenshot(path=sp_home, full_page=True)
            final_screenshot = sp_home
            
            # 3. 检查登录状态
            if "/login" in current_url or "/auth" in current_url:
                log("ERROR", "❌ Cookie 已失效，需要重新登录")
                notify_telegram(False, "登录检查", "Cookie 已失效，请更新 Cookie", sp_home)
                sys.exit(1)
            
            # 检查页面是否有用户信息（登录成功的标志）
            try:
                user_element = page.locator('button:has-text("Logout")').first
                user_element.wait_for(state="visible", timeout=5000)
                log("INFO", "✅ Cookie 有效，已登录")
            except:
                try:
                    sidebar = page.locator('.sidebar').first
                    sidebar.wait_for(state="visible", timeout=5000)
                    log("INFO", "✅ Cookie 有效，已登录（检测到侧边栏）")
                except:
                    log("WARN", "⚠️ 无法确认登录状态，继续执行...")
            
            # 4. 进入 Free Plans 页面
            log("INFO", "🎁 进入 Free Plans 页面...")
            page.goto(FREE_PANEL_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            
            # 截图 Free Plans 页面
            sp_free = screenshot_path("02-free-plans")
            page.screenshot(path=sp_free, full_page=True)
            final_screenshot = sp_free
            
            log("INFO", f"当前 URL: {page.url}")
            
            # 5. 检查续订按钮状态
            log("INFO", "🔍 检查续订按钮状态...")
            
            try:
                renew_btn = page.locator('#renewServerBtn').first
                renew_btn.wait_for(state="visible", timeout=10000)
                
                # 检查按钮是否禁用
                is_disabled = renew_btn.is_disabled()
                has_disabled_attr = renew_btn.get_attribute("disabled") is not None
                
                log("INFO", f"按钮 disabled 属性: {is_disabled or has_disabled_attr}")
                
                # 获取续订次数信息
                try:
                    renewal_count_el = page.locator('#renewal-count').first
                    renewal_count = renewal_count_el.text_content()
                    log("INFO", f"本周已续订次数: {renewal_count}/7")
                except:
                    renewal_count = "未知"
                
                # 获取状态信息
                try:
                    status_text_el = page.locator('#renewal-status-text').first
                    status_text = status_text_el.text_content()
                    log("INFO", f"续订状态: {status_text}")
                except:
                    status_text = "未知"
                
                if is_disabled or has_disabled_attr:
                    # 按钮被禁用，无法续订
                    log("INFO", "⏭️ 续订按钮已禁用，跳过续订")
                    
                    msg = f"续订次数: {renewal_count}/7\n状态: {status_text}\n\n⏭️ 已达到续订限制或未到续订时间，跳过"
                    notify_telegram(True, "检查完成", msg, final_screenshot)
                    
                else:
                    # 按钮可用，尝试续订
                    log("INFO", "✨ 续订按钮可用，开始续订流程...")
                    
                    # 点击续订按钮打开模态框
                    renew_btn.click()
                    page.wait_for_timeout(2000)
                    
                    # 截图模态框
                    sp_modal = screenshot_path("03-renewal-modal")
                    page.screenshot(path=sp_modal, full_page=True)
                    final_screenshot = sp_modal
                    
                    # 检查模态框是否打开
                    try:
                        modal = page.locator('#renewalModal').first
                        modal.wait_for(state="visible", timeout=5000)
                        log("INFO", "📋 续订模态框已打开")
                        
                        # 点击广告横幅
                        log("INFO", "🖱️ 点击广告横幅...")
                        ad_banner = page.locator('#adBanner').first
                        
                        # 监听新窗口打开
                        with context.expect_page() as new_page_info:
                            ad_banner.click()
                        
                        # 等待广告页面
                        page.wait_for_timeout(3000)
                        
                        # 截图点击广告后的状态
                        sp_after_ad = screenshot_path("04-after-ad-click")
                        page.screenshot(path=sp_after_ad, full_page=True)
                        final_screenshot = sp_after_ad
                        
                        # 等待 Turnstile 验证
                        log("INFO", "⏳ 等待 Turnstile 验证...")
                        page.wait_for_timeout(5000)
                        
                        # 检查完成续订按钮是否可用
                        try:
                            complete_btn = page.locator('#renewBtn').first
                            complete_btn.wait_for(state="visible", timeout=10000)
                            
                            # 等待按钮变为可用（最多等待30秒）
                            for i in range(15):
                                if not complete_btn.is_disabled():
                                    break
                                log("INFO", f"等待验证完成... ({i+1}/15)")
                                page.wait_for_timeout(2000)
                            
                            if not complete_btn.is_disabled():
                                log("INFO", "✅ 验证通过，点击完成续订...")
                                complete_btn.click()
                                page.wait_for_timeout(5000)
                                
                                # 最终截图
                                sp_final = screenshot_path("05-renewal-complete")
                                page.screenshot(path=sp_final, full_page=True)
                                final_screenshot = sp_final
                                
                                log("INFO", "🎉 续订操作完成")
                                notify_telegram(True, "续订完成", "✅ 服务器续订成功！", final_screenshot)
                            else:
                                log("WARN", "⚠️ Turnstile 验证未完成，按钮仍被禁用")
                                notify_telegram(False, "验证失败", "Turnstile 验证未能完成，请手动续订", final_screenshot)
                                
                        except Exception as e:
                            log("ERROR", f"完成续订失败: {e}")
                            notify_telegram(False, "续订失败", f"完成续订按钮操作失败: {e}", final_screenshot)
                        
                    except Exception as e:
                        log("ERROR", f"模态框操作失败: {e}")
                        notify_telegram(False, "续订失败", f"打开续订模态框失败: {e}", final_screenshot)
                
            except PlaywrightTimeout:
                log("WARN", "⚠️ 未找到续订按钮，可能页面结构变化")
                notify_telegram(False, "页面异常", "未找到续订按钮", final_screenshot)
                
            except Exception as e:
                log("ERROR", f"检查续订按钮失败: {e}")
                notify_telegram(False, "检查失败", str(e), final_screenshot)
            
            # 6. 保存新 Cookie
            log("INFO", "💾 保存 Cookie...")
            new_cookies = context.cookies()
            new_cookie_str = save_cookies_for_update(new_cookies)
            
            # 7. 更新 GitHub Secret
            if new_cookie_str:
                update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
            
            log("INFO", "✅ 脚本执行完成")
            
        except Exception as e:
            log("ERROR", f"💥 发生异常: {e}")
            import traceback
            traceback.print_exc()
            
            sp_error = screenshot_path("99-error")
            try:
                page.screenshot(path=sp_error, full_page=True)
                final_screenshot = sp_error
            except:
                pass
            
            notify_telegram(
                False, 
                "脚本异常", 
                str(e), 
                final_screenshot if final_screenshot and Path(final_screenshot).exists() else None
            )
            sys.exit(1)
            
        finally:
            context.close()
            browser.close()
            log("INFO", "🔒 浏览器已关闭")


if __name__ == "__main__":
    main()
