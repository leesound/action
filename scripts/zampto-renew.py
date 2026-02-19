#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zampto 自动续期脚本
自动获取所有服务器并续期
"""

import os
import sys
import time
import platform
import requests
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from seleniumbase import SB

# 配置
AUTH_URL = "https://auth.zampto.net/sign-in?app_id=bmhk6c8qdqxphlyscztgl"
DASHBOARD_URL = "https://dash.zampto.net/homepage"
SERVER_URL_TEMPLATE = "https://dash.zampto.net/server?id={}"
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def is_linux() -> bool:
    return platform.system().lower() == "linux"


def setup_display():
    """设置 Linux 虚拟显示"""
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            print("[INFO] 已启动虚拟显示")
            return display
        except Exception as e:
            print(f"[ERROR] 虚拟显示失败: {e}")
            sys.exit(1)
    return None


def screenshot_path(name: str) -> str:
    return str(OUTPUT_DIR / f"{datetime.now().strftime('%H%M%S')}-{name}.png")


def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_file: str = None):
    """发送 Telegram 通知"""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        emoji = "✅" if ok else "❌"
        text = f"🔔 Zampto 续期：{emoji} {stage}\n"
        if msg:
            text += f"{msg}\n"
        text += f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30
        )

        if screenshot_file and Path(screenshot_file).exists():
            with open(screenshot_file, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": f"截图（{stage}）"},
                    files={"photo": f},
                    timeout=60
                )
    except Exception as e:
        print(f"[WARN] Telegram 通知失败: {e}")


def wait_for_turnstile(sb, max_wait: int = 60) -> bool:
    """等待 Cloudflare Turnstile 验证完成"""
    print("[INFO] 等待 Turnstile 验证...")
    
    for i in range(max_wait):
        try:
            result = sb.execute_script('''
                var cf = document.querySelector('input[name="cf-turnstile-response"]');
                if (cf && cf.value && cf.value.length > 20) return "token";
                var success = document.querySelector('[data-success="true"], .cf-turnstile-success');
                if (success) return "success";
                return null;
            ''')
            if result:
                print(f"[INFO] ✅ Turnstile 验证完成 ({result})")
                return True
        except:
            pass
        
        if i % 10 == 0 and i > 0:
            print(f"[INFO] 等待验证中... ({i}/{max_wait}s)")
        time.sleep(1)
    
    return False


def login_zampto(sb, username: str, password: str) -> bool:
    """执行 Zampto 登录"""
    print("=" * 50)
    print(f"[INFO] 开始登录 | 用户: {username}")
    print("=" * 50)
    
    sb.uc_open_with_reconnect(AUTH_URL, reconnect_time=8.0)
    time.sleep(3)
    sb.save_screenshot(screenshot_path("01-login-page"))
    
    current_url = sb.get_current_url()
    print(f"[INFO] 当前 URL: {current_url}")
    
    if "dash.zampto.net" in current_url:
        print("[INFO] ✅ 已经登录")
        return True
    
    try:
        sb.wait_for_element('input[name="identifier"]', timeout=20)
    except:
        print("[ERROR] 未找到登录表单")
        sb.save_screenshot(screenshot_path("02-no-form"))
        return False
    
    print("[INFO] 输入用户名...")
    sb.type('input[name="identifier"]', username)
    time.sleep(1)
    
    sb.click('button[type="submit"][name="submit"]')
    time.sleep(3)
    sb.save_screenshot(screenshot_path("03-after-username"))
    
    try:
        sb.wait_for_element('input[name="password"]', timeout=15)
    except:
        print("[ERROR] 未跳转到密码页面")
        sb.save_screenshot(screenshot_path("04-no-password-page"))
        return False
    
    print("[INFO] 输入密码...")
    sb.type('input[name="password"]', password)
    time.sleep(1)
    
    sb.click('button[type="submit"][name="submit"]')
    time.sleep(5)
    
    current_url = sb.get_current_url()
    sb.save_screenshot(screenshot_path("05-login-result"))
    
    if "dash.zampto.net" in current_url or "sign-in" not in current_url:
        print("[INFO] ✅ 登录成功")
        return True
    else:
        print(f"[ERROR] 登录失败，URL: {current_url}")
        return False


def get_all_servers(sb) -> List[Dict[str, str]]:
    """从 Dashboard 获取所有服务器列表"""
    print("\n[INFO] 获取服务器列表...")
    
    sb.open(DASHBOARD_URL)
    time.sleep(3)
    sb.save_screenshot(screenshot_path("06-dashboard"))
    
    servers = sb.execute_script('''
        var servers = [];
        document.querySelectorAll('.server-item').forEach(function(item) {
            var link = item.querySelector('a[href*="/server?id="]');
            var nameEl = item.querySelector('h3');
            var statusEl = item.querySelector('.server-status');
            
            if (link) {
                var href = link.getAttribute('href');
                var match = href.match(/id=(\\d+)/);
                if (match) {
                    var name = nameEl ? nameEl.textContent.trim().split('\\n')[0].trim() : '';
                    var status = 'unknown';
                    if (statusEl) {
                        if (statusEl.classList.contains('status-online')) status = 'online';
                        else if (statusEl.classList.contains('status-offline')) status = 'offline';
                    }
                    servers.push({ id: match[1], name: name, status: status });
                }
            }
        });
        
        if (servers.length === 0) {
            document.querySelectorAll('a[href*="/server?id="]').forEach(function(link) {
                var match = link.href.match(/id=(\\d+)/);
                if (match && !servers.some(function(s) { return s.id === match[1]; })) {
                    servers.push({ id: match[1], name: 'Server ' + match[1], status: 'unknown' });
                }
            });
        }
        return servers;
    ''')
    
    if not servers:
        page_source = sb.get_page_source()
        matches = re.findall(r'/server\?id=(\d+)', page_source)
        servers = [{"id": sid, "name": f"Server {sid}", "status": "unknown"} for sid in set(matches)]
    
    print(f"[INFO] 找到 {len(servers)} 个服务器:")
    for s in servers:
        print(f"  - ID: {s['id']}, 名称: {s.get('name', 'N/A')}, 状态: {s.get('status', 'N/A')}")
    
    return servers


def get_server_expiry(sb, server_id: str) -> Dict[str, Any]:
    """获取服务器到期信息"""
    info = {"id": server_id, "last_renewal": "", "next_renewal": "", "hours_remaining": -1}
    
    sb.open(SERVER_URL_TEMPLATE.format(server_id))
    time.sleep(2)
    
    try:
        info["last_renewal"] = sb.execute_script(
            'var el = document.getElementById("lastRenewalTime"); return el ? el.textContent.trim() : "";'
        ) or "未知"
        
        info["next_renewal"] = sb.execute_script(
            'var el = document.getElementById("nextRenewalTime"); return el ? el.textContent.trim() : "";'
        ) or "未知"
        
        if info["next_renewal"] and info["next_renewal"] != "未知":
            text = info["next_renewal"]
            days = int(re.search(r'(\d+)\s*day', text).group(1)) if re.search(r'(\d+)\s*day', text) else 0
            hours = int(re.search(r'(\d+)\s*h', text).group(1)) if re.search(r'(\d+)\s*h', text) else 0
            minutes = int(re.search(r'(\d+)\s*m', text).group(1)) if re.search(r'(\d+)\s*m', text) else 0
            info["hours_remaining"] = days * 24 + hours + minutes / 60
            
    except Exception as e:
        print(f"[WARN] 获取服务器 {server_id} 到期信息失败: {e}")
    
    return info


def renew_server(sb, server_id: str) -> Dict[str, Any]:
    """续期单个服务器"""
    result = {"server_id": server_id, "success": False, "message": "", "screenshot": None}
    
    print(f"\n[INFO] 续期服务器 {server_id}...")
    
    sb.open(SERVER_URL_TEMPLATE.format(server_id))
    time.sleep(2)
    
    old_renewal = sb.execute_script(
        'var el = document.getElementById("lastRenewalTime"); return el ? el.textContent.trim() : "";'
    )
    
    try:
        clicked = sb.execute_script(f'''
            var links = document.querySelectorAll('a[onclick*="handleServerRenewal"]');
            for (var i = 0; i < links.length; i++) {{
                var onclick = links[i].getAttribute('onclick');
                if (onclick && onclick.includes('{server_id}')) {{
                    links[i].click();
                    return true;
                }}
            }}
            if (typeof handleServerRenewal === 'function') {{
                handleServerRenewal(null, {server_id});
                return true;
            }}
            return false;
        ''')
        
        if not clicked:
            result["message"] = "未找到续期按钮"
            return result
            
    except Exception as e:
        result["message"] = f"点击续期按钮失败: {e}"
        return result
    
    time.sleep(2)
    sb.save_screenshot(screenshot_path(f"server-{server_id}-modal"))
    
    print("[INFO] 处理 Turnstile 验证...")
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
    except Exception as e:
        print(f"[WARN] uc_gui_click_captcha: {e}")
    
    wait_for_turnstile(sb, max_wait=45)
    time.sleep(3)
    
    sp = screenshot_path(f"server-{server_id}-result")
    sb.save_screenshot(sp)
    result["screenshot"] = sp
    
    sb.open(SERVER_URL_TEMPLATE.format(server_id))
    time.sleep(2)
    
    new_renewal = sb.execute_script(
        'var el = document.getElementById("lastRenewalTime"); return el ? el.textContent.trim() : "";'
    )
    new_remaining = sb.execute_script(
        'var el = document.getElementById("nextRenewalTime"); return el ? el.textContent.trim() : "";'
    )
    
    today = datetime.now().strftime('%b %d, %Y')
    
    if new_renewal != old_renewal:
        result["success"] = True
        result["message"] = f"续期成功 | 到期: {new_remaining}"
    elif today in str(new_renewal):
        result["success"] = True
        result["message"] = f"已续期 | 到期: {new_remaining}"
    elif "1 day" in new_remaining or "2 day" in new_remaining:
        result["success"] = True
        result["message"] = f"续期可能成功 | 到期: {new_remaining}"
    else:
        result["message"] = f"续期状态未知 | 到期: {new_remaining}"
    
    print(f"[INFO] 服务器 {server_id}: {'✅' if result['success'] else '❌'} {result['message']}")
    return result


def main():
    username = os.environ.get("ZAMPTO_USERNAME")
    password = os.environ.get("ZAMPTO_PASSWORD")
    
    if not username or not password:
        print("[ERROR] 缺少环境变量 ZAMPTO_USERNAME 或 ZAMPTO_PASSWORD")
        sys.exit(1)
    
    display = setup_display()
    results = []
    last_screenshot = None
    
    try:
        with SB(
            uc=True,
            test=True,
            locale="en",
            headed=not is_linux(),
            chromium_arg="--disable-blink-features=AutomationControlled",
        ) as sb:
            
            if not login_zampto(sb, username, password):
                notify_telegram(False, "登录失败", "无法登录 Zampto 账户")
                sys.exit(1)
            
            servers = get_all_servers(sb)
            
            if not servers:
                notify_telegram(False, "无服务器", "未找到任何服务器")
                sys.exit(1)
            
            for server in servers:
                server_id = server["id"]
                try:
                    expiry = get_server_expiry(sb, server_id)
                    print(f"[INFO] 服务器 {server_id}: 剩余 {expiry['next_renewal']}")
                    
                    result = renew_server(sb, server_id)
                    result["name"] = server.get("name", f"Server {server_id}")
                    results.append(result)
                    
                    if result.get("screenshot"):
                        last_screenshot = result["screenshot"]
                    
                    time.sleep(2)
                except Exception as e:
                    print(f"[ERROR] 处理服务器 {server_id} 失败: {e}")
                    results.append({
                        "server_id": server_id,
                        "name": server.get("name", f"Server {server_id}"),
                        "success": False,
                        "message": str(e)
                    })
            
            final_sp = screenshot_path("final")
            sb.save_screenshot(final_sp)
            last_screenshot = final_sp
    
    except Exception as e:
        print(f"[ERROR] 脚本执行失败: {e}")
        notify_telegram(False, "脚本错误", str(e))
        sys.exit(1)
    finally:
        if display:
            display.stop()
    
    success_count = sum(1 for r in results if r.get("success"))
    total_count = len(results)
    
    summary = f"服务器: {success_count}/{total_count} 成功\n"
    for r in results:
        status = "✅" if r.get("success") else "❌"
        summary += f"{status} {r.get('name', r['server_id'])} (ID:{r['server_id']}): {r.get('message', '未知')}\n"
    
    print(f"\n{'='*50}\n[结果汇总]\n{summary}{'='*50}")
    
    notify_telegram(
        ok=success_count == total_count,
        stage="完成",
        msg=summary,
        screenshot_file=last_screenshot
    )
    
    sys.exit(0 if success_count > 0 else 1)


if __name__ == "__main__":
    main()
