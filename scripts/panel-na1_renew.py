#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Panel NA1 自动重启脚本
功能：
1. 使用 Cookie 登录 https://panel.na1.host/
2. 通过 API 获取服务器列表
3. 进入服务器页面点击 Restart 按钮
4. 发送 Telegram 通知（带截图）
5. 自动更新 Cookie 到 GitHub Secrets
"""

import os
import sys
import json
import time
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
BASE_URL = "https://panel.na1.host"
API_URL = f"{BASE_URL}/api/client"
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
    log("INFO", f"输出目录: {OUTPUT_DIR.absolute()}")


def screenshot_path(name: str) -> str:
    """生成截图路径"""
    return str(OUTPUT_DIR / f"{name}.png")


def parse_cookie_string(cookie_str: str, domain: str) -> list:
    """
    解析 Cookie 字符串为 Playwright cookie 格式
    """
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
        
        # URL 解码
        try:
            value = unquote(value)
        except:
            pass
        
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": "session" in name.lower() or "remember" in name.lower(),
            "sameSite": "Lax"
        })
    
    log("INFO", f"解析到 {len(cookies)} 个 Cookie")
    return cookies


def save_cookies_for_update(cookies: list) -> str:
    """
    保存重要 Cookie 用于后续更新
    返回 Cookie 字符串
    """
    important_prefixes = ["remember_web", "XSRF-TOKEN", "pterodactyl_session", "cf_clearance"]
    
    filtered = []
    for c in cookies:
        name = c.get("name", "")
        if any(name.startswith(prefix) or name == prefix for prefix in important_prefixes):
            filtered.append(c)
    
    if not filtered:
        return ""
    
    cookie_string = "; ".join([f"{c['name']}={quote(c['value'], safe='')}" for c in filtered])
    
    # 保存到文件
    cookie_file = OUTPUT_DIR / "new_cookies.txt"
    cookie_file.write_text(cookie_string)
    log("INFO", f"新 Cookie 已保存到 {cookie_file}")
    
    return cookie_string


def update_github_secret(secret_name: str, secret_value: str) -> bool:
    """
    更新 GitHub Secret
    需要 REPO_TOKEN 环境变量（具有 repo 权限的 PAT）
    """
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
        
        # 加密 Secret
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
        
        log("INFO", f"✅ GitHub Secret {secret_name} 已更新")
        return True
        
    except ImportError:
        log("WARN", "PyNaCl 未安装，跳过 Secret 加密更新")
        return False
    except Exception as e:
        log("ERROR", f"更新 GitHub Secret 失败: {e}")
        return False


def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_file: str = None):
    """
    发送 Telegram 通知（带截图）
    """
    bot_token = env_or_default("TG_BOT_TOKEN")
    chat_id = env_or_default("TG_CHAT_ID")
    
    if not bot_token or not chat_id:
        log("WARN", "Telegram 配置不完整，跳过通知")
        return
    
    try:
        status = "✅ 成功" if ok else "❌ 失败"
        text_lines = [
            f"🤖 NA1 自动重启脚本",
            f"",
            f"📊 状态：{status}",
            f"📍 阶段：{stage}",
        ]
        if msg:
            text_lines.append(f"💬 信息：{msg}")
        text_lines.append(f"")
        text_lines.append(f"⏰ 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        caption = "\n".join(text_lines)
        
        # 如果有截图，发送带图片的消息
        if screenshot_file and Path(screenshot_file).exists():
            photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            with open(screenshot_file, "rb") as f:
                resp = requests.post(
                    photo_url, 
                    data={
                        "chat_id": chat_id,
                        "caption": caption,
                        "parse_mode": "HTML"
                    }, 
                    files={"photo": f}, 
                    timeout=60
                )
                
                if resp.status_code == 200:
                    log("INFO", "✅ Telegram 通知已发送（带截图）")
                else:
                    log("WARN", f"Telegram 图片发送失败: {resp.text}")
                    # 图片发送失败，尝试只发送文本
                    send_text_only(bot_token, chat_id, caption)
        else:
            # 没有截图，只发送文本
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
            log("INFO", "✅ Telegram 文本通知已发送")
        else:
            log("WARN", f"Telegram 消息发送失败: {resp.text}")
    except Exception as e:
        log("WARN", f"发送文本失败: {e}")


def get_servers_from_api(cookies_str: str) -> list:
    """
    通过 API 获取服务器列表
    返回服务器信息列表
    """
    try:
        headers = {
            "Accept": "application/json",
            "Cookie": cookies_str,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        resp = requests.get(f"{API_URL}?page=1", headers=headers, timeout=30)
        resp.raise_for_status()
        
        data = resp.json()
        servers = []
        
        for item in data.get("data", []):
            if item.get("object") == "server":
                attrs = item.get("attributes", {})
                server_id = attrs.get("identifier")
                server_name = attrs.get("name", "unknown")
                server_status = attrs.get("status")
                is_suspended = attrs.get("is_suspended", False)
                
                if server_id:
                    servers.append({
                        "id": server_id,
                        "name": server_name,
                        "url": f"{BASE_URL}/server/{server_id}",
                        "status": server_status,
                        "is_suspended": is_suspended
                    })
                    log("INFO", f"📦 找到服务器: {server_name} (ID: {server_id})")
        
        return servers
        
    except Exception as e:
        log("ERROR", f"API 获取服务器列表失败: {e}")
        return []


# ==================== 主逻辑 ====================

def main():
    """主函数"""
    log("INFO", "=" * 50)
    log("INFO", "🚀 Panel NA1 自动重启脚本启动")
    log("INFO", "=" * 50)
    
    # 确保输出目录存在
    ensure_output_dir()
    
    # 获取环境变量
    try:
        preset_cookies = env_or_throw("PANEL_NA1_COOKIES")
    except ValueError as e:
        log("ERROR", str(e))
        notify_telegram(False, "初始化失败", "Cookie 环境变量未设置")
        sys.exit(1)
    
    # 尝试通过 API 获取服务器列表
    log("INFO", "📡 通过 API 获取服务器列表...")
    servers = get_servers_from_api(preset_cookies)
    
    if servers:
        log("INFO", f"✅ API 返回 {len(servers)} 个服务器")
    else:
        log("WARN", "⚠️ API 未返回服务器，将通过页面获取")
    
    # 启动浏览器
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
            timezone_id="America/New_York"
        )
        
        page = context.new_page()
        
        # 注入反检测脚本
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        
        final_screenshot = None
        
        try:
            # 1. 注入 Cookie
            log("INFO", "🍪 注入 Cookie...")
            cookies = parse_cookie_string(preset_cookies, "panel.na1.host")
            if cookies:
                context.add_cookies(cookies)
            
            # 2. 访问首页
            log("INFO", f"🔗 访问 {BASE_URL}...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            
            current_url = page.url
            title = page.title()
            log("INFO", f"📍 当前 URL: {current_url}")
            log("INFO", f"📄 页面标题: {title}")
            
            # 3. 检查登录状态
            if "/auth/login" in current_url:
                sp = screenshot_path("01-need-login")
                page.screenshot(path=sp, full_page=True)
                log("ERROR", "❌ Cookie 已失效，需要重新登录")
                notify_telegram(False, "登录检查", "Cookie 已失效，请更新 Cookie", sp)
                sys.exit(1)
            
            log("INFO", "✅ Cookie 有效，已登录")
            
            # 4. 截图 Dashboard
            sp_dashboard = screenshot_path("02-dashboard")
            page.screenshot(path=sp_dashboard, full_page=True)
            final_screenshot = sp_dashboard
            
            # 5. 保存新 Cookie
            new_cookies = context.cookies()
            new_cookie_str = save_cookies_for_update(new_cookies)
            
            # 6. 尝试更新 GitHub Secret
            if new_cookie_str:
                update_github_secret("PANEL_NA1_COOKIES", new_cookie_str)
            
            # 7. 获取服务器（如果 API 没获取到）
            if not servers:
                log("INFO", "🔍 从页面获取服务器链接...")
                page.wait_for_timeout(2000)
                server_links = page.locator('a[href^="/server/"]').all()
                
                for link in server_links[:5]:  # 最多处理5个
                    href = link.get_attribute("href")
                    if href:
                        server_id = href.replace("/server/", "").split("/")[0]
                        if server_id and server_id not in [s["id"] for s in servers]:
                            servers.append({
                                "id": server_id,
                                "name": "unknown",
                                "url": f"{BASE_URL}/server/{server_id}"
                            })
            
            if not servers:
                sp = screenshot_path("03-no-servers")
                page.screenshot(path=sp, full_page=True)
                log("ERROR", "❌ 未找到任何服务器")
                notify_telegram(False, "获取服务器", "未找到任何服务器", sp)
                sys.exit(1)
            
            log("INFO", f"📋 共找到 {len(servers)} 个服务器")
            
            # 8. 遍历服务器执行重启
            success_count = 0
            fail_count = 0
            results = []
            
            for idx, server in enumerate(servers):
                server_id = server["id"]
                server_name = server.get("name", server_id)
                server_url = server["url"]
                
                log("INFO", f"")
                log("INFO", f"[{idx + 1}/{len(servers)}] 🖥️ 处理服务器: {server_name} ({server_id})")
                
                try:
                    # 进入服务器页面
                    page.goto(server_url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(3000)
                    
                    sp_server = screenshot_path(f"04-server-{server_id}")
                    page.screenshot(path=sp_server, full_page=True)
                    
                    # 查找 Restart 按钮
                    restart_btn = None
                    btn_found = False
                    
                    # 方法1: 通过文本查找
                    try:
                        restart_btn = page.locator('button:has-text("Restart")').first
                        restart_btn.wait_for(state="visible", timeout=10000)
                        btn_found = True
                        log("INFO", "✅ 通过文本找到 Restart 按钮")
                    except:
                        pass
                    
                    # 方法2: 通过 class 查找
                    if not btn_found:
                        try:
                            restart_btn = page.locator('button[class*="restartButton"]').first
                            restart_btn.wait_for(state="visible", timeout=5000)
                            btn_found = True
                            log("INFO", "✅ 通过 class 找到 Restart 按钮")
                        except:
                            pass
                    
                    # 方法3: 通过 SVG 图标查找
                    if not btn_found:
                        try:
                            restart_btn = page.locator('button:has(svg[viewBox="0 0 20 20"])').nth(1)
                            restart_btn.wait_for(state="visible", timeout=5000)
                            btn_found = True
                            log("INFO", "✅ 通过 SVG 找到 Restart 按钮")
                        except:
                            pass
                    
                    if not btn_found:
                        log("WARN", f"⚠️ 未找到 Restart 按钮")
                        results.append(f"❓ {server_name}: 未找到按钮")
                        fail_count += 1
                        continue
                    
                    # 检查按钮是否禁用
                    is_disabled = restart_btn.is_disabled()
                    
                    if is_disabled:
                        log("WARN", f"⚠️ Restart 按钮被禁用（服务器可能已停止）")
                        
                        # 尝试点击 Start
                        try:
                            start_btn = page.locator('button:has-text("Start")').first
                            if not start_btn.is_disabled():
                                log("INFO", "🔄 尝试点击 Start 按钮...")
                                start_btn.click()
                                page.wait_for_timeout(3000)
                                
                                sp_started = screenshot_path(f"05-started-{server_id}")
                                page.screenshot(path=sp_started, full_page=True)
                                final_screenshot = sp_started
                                
                                log("INFO", f"✅ 服务器 {server_name} 已启动")
                                results.append(f"✅ {server_name}: 已启动")
                                success_count += 1
                                continue
                        except Exception as e:
                            log("WARN", f"Start 按钮操作失败: {e}")
                        
                        results.append(f"⚠️ {server_name}: 按钮禁用")
                        fail_count += 1
                        continue
                    
                    # 点击 Restart
                    log("INFO", f"🔄 点击 Restart 按钮...")
                    restart_btn.click()
                    page.wait_for_timeout(5000)
                    
                    sp_restarted = screenshot_path(f"05-restarted-{server_id}")
                    page.screenshot(path=sp_restarted, full_page=True)
                    final_screenshot = sp_restarted
                    
                    log("INFO", f"✅ 服务器 {server_name} 重启成功")
                    results.append(f"✅ {server_name}: 已重启")
                    success_count += 1
                    
                except PlaywrightTimeout as e:
                    log("ERROR", f"❌ 服务器 {server_id} 操作超时: {e}")
                    results.append(f"❌ {server_name}: 超时")
                    fail_count += 1
                    sp_error = screenshot_path(f"error-{server_id}")
                    try:
                        page.screenshot(path=sp_error, full_page=True)
                        final_screenshot = sp_error
                    except:
                        pass
                    
                except Exception as e:
                    log("ERROR", f"❌ 服务器 {server_id} 操作失败: {e}")
                    results.append(f"❌ {server_name}: {str(e)[:30]}")
                    fail_count += 1
                    sp_error = screenshot_path(f"error-{server_id}")
                    try:
                        page.screenshot(path=sp_error, full_page=True)
                        final_screenshot = sp_error
                    except:
                        pass
                
                # 避免请求过快
                page.wait_for_timeout(2000)
            
            # 9. 最终报告
            log("INFO", "")
            log("INFO", "=" * 50)
            total = len(servers)
            summary = f"成功: {success_count}/{total}, 失败: {fail_count}/{total}"
            log("INFO", f"📊 执行完成 - {summary}")
            log("INFO", "=" * 50)
            
            # 构建详细消息
            detail_msg = f"{summary}\n\n" + "\n".join(results)
            
            if fail_count == 0:
                notify_telegram(True, "全部完成", detail_msg, final_screenshot)
                sys.exit(0)
            elif success_count > 0:
                notify_telegram(True, "部分完成", detail_msg, final_screenshot)
                sys.exit(0)
            else:
                notify_telegram(False, "全部失败", detail_msg, final_screenshot)
                sys.exit(1)
            
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
