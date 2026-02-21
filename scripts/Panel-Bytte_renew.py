# scripts/Panel-Bytte_renew.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Panel Bytte 自动重启&续约脚本
功能：
1. 使用 Cookie 登录 https://panel.bytte.cloud/
2. 从 API 获取服务器列表
3. 进入服务器页面检查是否需要重启
4. 进入设置页面检查是否需要续约
5. 发送 Telegram 通知（带截图）
6. 自动更新 Cookie 到 GitHub Secrets
"""

import os
import sys
import json
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
BASE_URL = "https://panel.bytte.cloud"
OUTPUT_DIR = Path("output/screenshots")

# ==================== 工具函数 ====================

def log(level: str, msg: str):
    """统一日志格式"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def mask_id(server_id: str) -> str:
    """隐藏服务器 ID，只显示首尾字符"""
    if not server_id or len(server_id) <= 4:
        return server_id
    return f"{server_id[:2]}{'*' * (len(server_id) - 4)}{server_id[-2:]}"


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
    """保存重要 Cookie 用于后续更新"""
    important_prefixes = ["remember_web", "XSRF-TOKEN", "pterodactyl_session", "cf_clearance"]
    
    filtered = []
    for c in cookies:
        name = c.get("name", "")
        if any(name.startswith(prefix) or name == prefix for prefix in important_prefixes):
            filtered.append(c)
    
    if not filtered:
        return ""
    
    cookie_string = "; ".join([f"{c['name']}={quote(c['value'], safe='')}" for c in filtered])
    
    cookie_file = OUTPUT_DIR / "new_cookies.txt"
    cookie_file.write_text(cookie_string)
    log("INFO", f"新 Cookie 已保存到文件")
    
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
        
        pub_key_url = f"https://api.github.com/repos/{github_repo}/actions/secrets/public-key"
        resp = requests.get(pub_key_url, headers=headers, timeout=30)
        resp.raise_for_status()
        pub_key_data = resp.json()
        
        public_key = public.PublicKey(pub_key_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = encoding.Base64Encoder().encode(encrypted).decode("utf-8")
        
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
            f"📋 Bytte 自动重启&续约",
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
                    log("WARN", f"Telegram 图片发送失败: {resp.text}")
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
            log("WARN", f"Telegram 消息发送失败: {resp.text}")
    except Exception as e:
        log("WARN", f"发送文本失败: {e}")


# ==================== 业务逻辑 ====================

def check_need_restart(page) -> bool:
    """
    检查是否需要重启
    - 如果 Start 按钮可用（未禁用），说明服务器已停止，需要启动
    - 如果 Stop 按钮禁用，说明服务器已停止
    """
    try:
        # 检查 Start 按钮是否禁用
        start_btn = page.locator('#power-start')
        if start_btn.count() > 0:
            is_start_disabled = start_btn.get_attribute('disabled') is not None
            if not is_start_disabled:
                log("INFO", "🔴 服务器已停止，需要启动")
                return True
        
        # 检查 Stop 按钮是否禁用
        stop_btn = page.locator('#power-stop')
        if stop_btn.count() > 0:
            is_stop_disabled = stop_btn.get_attribute('disabled') is not None
            if is_stop_disabled:
                log("INFO", "🔴 服务器已停止（Stop按钮禁用），需要启动")
                return True
        
        log("INFO", "🟢 服务器正在运行")
        return False
        
    except Exception as e:
        log("WARN", f"检查重启状态失败: {e}")
        return False


def do_restart(page) -> bool:
    """执行重启/启动操作"""
    try:
        # 先尝试点击 Start 按钮
        start_btn = page.locator('#power-start')
        if start_btn.count() > 0:
            is_disabled = start_btn.get_attribute('disabled') is not None
            if not is_disabled:
                log("INFO", "▶️ 点击 Start 按钮...")
                start_btn.click()
                page.wait_for_timeout(3000)
                return True
        
        # 尝试点击 Restart 按钮
        restart_btn = page.locator('#power-restart')
        if restart_btn.count() > 0:
            is_disabled = restart_btn.get_attribute('disabled') is not None
            if not is_disabled:
                log("INFO", "🔄 点击 Restart 按钮...")
                restart_btn.click()
                page.wait_for_timeout(3000)
                return True
        
        log("WARN", "未找到可用的启动/重启按钮")
        return False
        
    except Exception as e:
        log("ERROR", f"重启操作失败: {e}")
        return False


def check_and_renew(page) -> dict:
    """
    检查续约信息并执行续约
    返回: {"need_renew": bool, "renewed": bool, "expiration": str, "balance": str, "price": str, "message": str}
    """
    result = {
        "need_renew": False,
        "renewed": False,
        "expiration": "",
        "balance": "",
        "price": "",
        "message": ""
    }
    
    try:
        # 等待页面加载
        page.wait_for_timeout(2000)
        
        # 1. 获取余额
        balance_selectors = [
            'code.RenewServerBox___StyledCode-sc-pwczq4-3',
            '.RenewServerBox___StyledDiv2-sc-pwczq4-2 code',
            'code.TqUPO'
        ]
        for selector in balance_selectors:
            balance_elem = page.locator(selector)
            if balance_elem.count() > 0:
                result["balance"] = balance_elem.first.inner_text().strip()
                log("INFO", f"💰 账户余额: {result['balance']}")
                break
        
        # 2. 获取过期时间
        expiration_selectors = [
            'code.RenewServerBox___StyledCode2-sc-pwczq4-5',
            '.RenewServerBox___StyledDiv3-sc-pwczq4-4 code',
            'code.kggZVS'
        ]
        for selector in expiration_selectors:
            expiration_elem = page.locator(selector)
            if expiration_elem.count() > 0:
                result["expiration"] = expiration_elem.first.inner_text().strip()
                log("INFO", f"📅 到期时间: {result['expiration']}")
                break
        
        # 3. 查找续约按钮
        renew_selectors = [
            'button:has-text("Renew Server")',
            '.RenewServerBox___StyledDiv4-sc-pwczq4-8 button',
            'button.bvYLfo'
        ]
        
        renew_btn = None
        btn_text = ""
        
        for selector in renew_selectors:
            btn = page.locator(selector)
            if btn.count() > 0:
                renew_btn = btn.first
                btn_text = renew_btn.inner_text().strip()
                log("INFO", f"🔘 找到续约按钮: {btn_text}")
                break
        
        if not renew_btn or not btn_text:
            result["message"] = "未找到续约按钮"
            log("INFO", "ℹ️ 未找到续约按钮")
            return result
        
        # 4. 解析价格
        # 按钮文本格式: "Renew Server - 0.00 USD"
        if "-" in btn_text:
            price_part = btn_text.split("-")[-1].strip()
            result["price"] = price_part
            log("INFO", f"💵 续约价格: {price_part}")
        
        # 5. 判断是否可以免费续约
        is_free = False
        if "0.00" in btn_text:
            is_free = True
        elif result["price"]:
            try:
                import re
                price_match = re.search(r'(\d+\.?\d*)', result["price"])
                if price_match:
                    price_value = float(price_match.group(1))
                    is_free = (price_value == 0)
            except:
                pass
        
        if is_free:
            result["need_renew"] = True
            log("INFO", "🆓 可免费续约!")
            
            # 点击续约按钮
            log("INFO", "🔄 点击续约按钮...")
            renew_btn.click()
            page.wait_for_timeout(2000)
            
            # 等待确认弹窗出现并点击 "Yes, Renew Server"
            confirm_selectors = [
                'button:has-text("Yes, Renew Server")',
                'button:has-text("Yes, renew server")',
                '.ConfirmationModal___StyledButton2-sc-1sxt2cr-4',
                'button.iNKfxp'
            ]
            
            confirm_clicked = False
            for selector in confirm_selectors:
                try:
                    confirm_btn = page.locator(selector)
                    if confirm_btn.count() > 0 and confirm_btn.first.is_visible():
                        log("INFO", "📝 找到确认按钮，点击 'Yes, Renew Server'...")
                        confirm_btn.first.click()
                        page.wait_for_timeout(3000)
                        confirm_clicked = True
                        break
                except:
                    continue
            
            if confirm_clicked:
                result["renewed"] = True
                result["message"] = "免费续约成功"
                log("INFO", "✅ 续约成功!")
            else:
                result["message"] = "未找到确认按钮"
                log("WARN", "⚠️ 未找到确认按钮，续约可能未完成")
            
        else:
            result["message"] = f"需付费续约: {result['price']}"
            log("INFO", f"💵 续约需要付费: {result['price']}，跳过")
        
    except Exception as e:
        log("ERROR", f"续约检查失败: {e}")
        result["message"] = f"检查失败: {str(e)[:50]}"
    
    return result


# ==================== 主函数 ====================

def main():
    """主函数"""
    log("INFO", "=" * 50)
    log("INFO", "🚀 Panel Bytte 自动重启&续约脚本启动")
    log("INFO", "=" * 50)
    
    ensure_output_dir()
    
    try:
        preset_cookies = env_or_throw("PANEL_BYTTE_COOKIES")
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
            timezone_id="America/New_York"
        )
        
        page = context.new_page()
        
        # 反检测
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        
        # 用于存储拦截到的 API 数据
        api_servers = []
        api_captured = False
        
        # 拦截首页 API 请求（只拦截一次）
        def handle_response(response):
            nonlocal api_captured
            if api_captured:
                return
            if "/api/client" in response.url and "page=" in response.url and response.status == 200:
                try:
                    data = response.json()
                    if isinstance(data, dict) and "data" in data:
                        for item in data.get("data", []):
                            if isinstance(item, dict) and item.get("object") == "server":
                                attrs = item.get("attributes", {})
                                server_id = attrs.get("identifier")
                                server_name = attrs.get("name", "unknown")
                                if server_id:
                                    api_servers.append({
                                        "id": server_id,
                                        "name": server_name,
                                        "url": f"{BASE_URL}/server/{server_id}"
                                    })
                                    log("INFO", f"📦 发现服务器: {server_name} (ID: {mask_id(server_id)})")
                        api_captured = True
                        log("INFO", "✅ API 数据已捕获，移除监听器")
                except Exception as e:
                    log("WARN", f"解析 API 响应失败: {e}")
        
        page.on("response", handle_response)
        
        final_screenshot = None
        new_cookie_str = ""  # 用于最后更新
        
        try:
            # 1. 注入 Cookie
            log("INFO", "🍪 注入 Cookie...")
            cookies = parse_cookie_string(preset_cookies, "panel.bytte.cloud")
            if cookies:
                context.add_cookies(cookies)
            
            # 2. 访问首页
            log("INFO", f"🔗 访问 {BASE_URL}...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            
            current_url = page.url
            title = page.title()
            log("INFO", f"当前 URL: {current_url}")
            log("INFO", f"页面标题: {title}")
            
            # 3. 检查登录状态
            if "/auth/login" in current_url:
                sp = screenshot_path("01-need-login")
                page.screenshot(path=sp, full_page=True)
                log("ERROR", "❌ Cookie 已失效，需要重新登录")
                notify_telegram(False, "登录检查", "Cookie 已失效，请更新 Cookie", sp)
                sys.exit(1)
            
            log("INFO", "✅ Cookie 有效，已登录")
            
            # 移除 API 拦截
            page.remove_listener("response", handle_response)
            
            # 关闭可能的弹窗
            try:
                dismiss_btn = page.locator('text=Dismiss').first
                if dismiss_btn.is_visible():
                    dismiss_btn.click()
                    log("INFO", "关闭公告弹窗")
                    page.wait_for_timeout(1000)
            except:
                pass
            
            # 4. 截图 Dashboard
            sp_dashboard = screenshot_path("02-dashboard")
            page.screenshot(path=sp_dashboard, full_page=True)
            final_screenshot = sp_dashboard
            
            # 5. 获取服务器列表
            servers = api_servers
            
            if not servers:
                sp = screenshot_path("03-no-servers")
                page.screenshot(path=sp, full_page=True)
                log("ERROR", "❌ 未找到任何服务器")
                notify_telegram(False, "获取服务器", "未找到任何服务器", sp)
                sys.exit(1)
            
            log("INFO", f"📋 共找到 {len(servers)} 个服务器")
            
            # 6. 遍历服务器执行操作
            results = []
            
            for idx, server in enumerate(servers):
                server_id = server["id"]
                server_name = server.get("name", server_id)
                server_url = server["url"]
                masked_id = mask_id(server_id)
                
                log("INFO", "")
                log("INFO", "=" * 40)
                log("INFO", f"[{idx + 1}/{len(servers)}] 🖥️ 处理: {server_name} ({masked_id})")
                log("INFO", "=" * 40)
                
                server_result = {
                    "name": server_name,
                    "restart": {"needed": False, "done": False},
                    "renew": {"needed": False, "done": False, "expiration": ""}
                }
                
                try:
                    # ========== 步骤 A: 检查重启状态 ==========
                    log("INFO", "")
                    log("INFO", "📍 步骤 A: 检查重启状态...")
                    page.goto(server_url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(3000)
                    
                    sp_console = screenshot_path(f"04-console-{idx + 1}")
                    page.screenshot(path=sp_console, full_page=True)
                    
                    need_restart = check_need_restart(page)
                    server_result["restart"]["needed"] = need_restart
                    
                    if need_restart:
                        restart_success = do_restart(page)
                        server_result["restart"]["done"] = restart_success
                        
                        sp_after_restart = screenshot_path(f"05-after-restart-{idx + 1}")
                        page.screenshot(path=sp_after_restart, full_page=True)
                        final_screenshot = sp_after_restart
                    
                    # ========== 步骤 B: 检查续约状态 ==========
                    log("INFO", "")
                    log("INFO", "📍 步骤 B: 检查续约状态...")
                    settings_url = f"{server_url}/settings"
                    page.goto(settings_url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(3000)
                    
                    sp_settings = screenshot_path(f"06-settings-{idx + 1}")
                    page.screenshot(path=sp_settings, full_page=True)
                    final_screenshot = sp_settings
                    
                    renew_result = check_and_renew(page)
                    server_result["renew"]["needed"] = renew_result["need_renew"]
                    server_result["renew"]["done"] = renew_result["renewed"]
                    server_result["renew"]["expiration"] = renew_result["expiration"]
                    
                    if renew_result["renewed"]:
                        sp_after_renew = screenshot_path(f"07-after-renew-{idx + 1}")
                        page.screenshot(path=sp_after_renew, full_page=True)
                        final_screenshot = sp_after_renew
                    
                    # 生成结果摘要
                    status_parts = []
                    if server_result["restart"]["needed"]:
                        if server_result["restart"]["done"]:
                            status_parts.append("🔄重启成功")
                        else:
                            status_parts.append("❌重启失败")
                    else:
                        status_parts.append("🟢运行中")
                    
                    if server_result["renew"]["needed"]:
                        if server_result["renew"]["done"]:
                            status_parts.append("✅续约成功")
                        else:
                            status_parts.append("❌续约失败")
                    else:
                        if renew_result.get("message"):
                            status_parts.append(f"ℹ️{renew_result['message'][:20]}")
                    
                    if server_result["renew"]["expiration"]:
                        status_parts.append(f"📅{server_result['renew']['expiration']}")
                    
                    results.append(f"🖥️ {server_name}: {' | '.join(status_parts)}")
                    
                except PlaywrightTimeout:
                    log("ERROR", f"❌ {server_name} 操作超时")
                    results.append(f"❌ {server_name}: 超时")
                    
                except Exception as e:
                    log("ERROR", f"❌ {server_name} 操作失败: {e}")
                    results.append(f"❌ {server_name}: {str(e)[:30]}")
                
                page.wait_for_timeout(2000)
            
            # 7. 保存并更新 Cookie（放在最后）
            log("INFO", "")
            log("INFO", "🍪 保存并更新 Cookie...")
            new_cookies = context.cookies()
            new_cookie_str = save_cookies_for_update(new_cookies)
            
            if new_cookie_str:
                update_github_secret("PANEL_BYTTE_COOKIES", new_cookie_str)
            
            # 8. 最终报告
            log("INFO", "")
            log("INFO", "=" * 50)
            log("INFO", "📊 执行完成")
            log("INFO", "=" * 50)
            
            detail_msg = "\n".join(results)
            notify_telegram(True, "全部完成", detail_msg, final_screenshot)
            sys.exit(0)
            
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
