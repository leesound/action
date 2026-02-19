#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zampto 自动续期脚本
支持多账号，自动获取所有服务器并续期
"""

import os
import sys
import time
import platform
import requests
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

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


def screenshot_path(account_index: int, name: str) -> str:
    return str(OUTPUT_DIR / f"acc{account_index}-{datetime.now().strftime('%H%M%S')}-{name}.png")


def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_file: str = None):
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        emoji = "✅" if ok else "❌"
        text = f"🔔 Zampto 续期：{emoji} {stage}\n{msg}\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

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


def parse_accounts(account_str: str) -> List[Tuple[str, str]]:
    """解析账号配置"""
    accounts = []
    for line in account_str.strip().split('\n'):
        line = line.strip()
        if not line or '----' not in line:
            continue
        parts = line.split('----', 1)
        if len(parts) == 2:
            username, password = parts[0].strip(), parts[1].strip()
            if username and password:
                accounts.append((username, password))
    return accounts


def wait_for_turnstile(sb, max_wait: int = 60) -> bool:
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
                print(f"[INFO] ✅ Turnstile 验证完成")
                return True
        except:
            pass
        if i % 10 == 0 and i > 0:
            print(f"[INFO] 等待验证中... ({i}/{max_wait}s)")
        time.sleep(1)
    return False


def login_zampto(sb, username: str, password: str, acc_idx: int) -> bool:
    print(f"\n{'='*50}")
    print(f"[INFO] 账号 {acc_idx}: 登录 {username}")
    print('='*50)
    
    sb.uc_open_with_reconnect(AUTH_URL, reconnect_time=8.0)
    time.sleep(3)
    sb.save_screenshot(screenshot_path(acc_idx, "01-login"))
    
    current_url = sb.get_current_url()
    if "dash.zampto.net" in current_url:
        print("[INFO] ✅ 已登录")
        return True
    
    try:
        sb.wait_for_element('input[name="identifier"]', timeout=20)
    except:
        print("[ERROR] 未找到登录表单")
        return False
    
    sb.type('input[name="identifier"]', username)
    time.sleep(1)
    sb.click('button[type="submit"][name="submit"]')
    time.sleep(3)
    
    try:
        sb.wait_for_element('input[name="password"]', timeout=15)
    except:
        print("[ERROR] 未跳转到密码页面")
        return False
    
    sb.type('input[name="password"]', password)
    time.sleep(1)
    sb.click('button[type="submit"][name="submit"]')
    time.sleep(5)
    
    sb.save_screenshot(screenshot_path(acc_idx, "02-login-result"))
    
    current_url = sb.get_current_url()
    if "dash.zampto.net" in current_url or "sign-in" not in current_url:
        print("[INFO] ✅ 登录成功")
        return True
    print(f"[ERROR] 登录失败")
    return False


def logout_zampto(sb):
    """登出当前账号"""
    try:
        sb.execute_script('''
            var logoutLink = document.querySelector('a[href*="logout"], button[onclick*="logout"]');
            if (logoutLink) logoutLink.click();
        ''')
        time.sleep(2)
    except:
        pass
    # 清除 cookies
    sb.delete_all_cookies()
    time.sleep(1)


def get_all_servers(sb, acc_idx: int) -> List[Dict[str, str]]:
    print(f"[INFO] 获取服务器列表...")
    sb.open(DASHBOARD_URL)
    time.sleep(3)
    sb.save_screenshot(screenshot_path(acc_idx, "03-dashboard"))
    
    servers = sb.execute_script('''
        var servers = [];
        document.querySelectorAll('.server-item').forEach(function(item) {
            var link = item.querySelector('a[href*="/server?id="]');
            var nameEl = item.querySelector('h3');
            if (link) {
                var match = link.href.match(/id=(\\d+)/);
                if (match) {
                    var name = nameEl ? nameEl.textContent.trim().split('\\n')[0].trim() : 'Server ' + match[1];
                    servers.push({ id: match[1], name: name });
                }
            }
        });
        if (servers.length === 0) {
            document.querySelectorAll('a[href*="/server?id="]').forEach(function(link) {
                var match = link.href.match(/id=(\\d+)/);
                if (match && !servers.some(function(s) { return s.id === match[1]; })) {
                    servers.push({ id: match[1], name: 'Server ' + match[1] });
                }
            });
        }
        return servers;
    ''')
    
    if not servers:
        page_source = sb.get_page_source()
        matches = re.findall(r'/server\?id=(\d+)', page_source)
        servers = [{"id": sid, "name": f"Server {sid}"} for sid in set(matches)]
    
    print(f"[INFO] 找到 {len(servers)} 个服务器")
    for s in servers:
        print(f"  - {s['name']} (ID: {s['id']})")
    return servers


def renew_server(sb, server_id: str, acc_idx: int) -> Dict[str, Any]:
    result = {"server_id": server_id, "success": False, "message": "", "screenshot": None}
    
    print(f"[INFO] 续期服务器 {server_id}...")
    sb.open(SERVER_URL_TEMPLATE.format(server_id))
    time.sleep(2)
    
    old_renewal = sb.execute_script(
        'var el = document.getElementById("lastRenewalTime"); return el ? el.textContent.trim() : "";'
    )
    
    try:
        clicked = sb.execute_script(f'''
            var links = document.querySelectorAll('a[onclick*="handleServerRenewal"]');
            for (var i = 0; i < links.length; i++) {{
                if (links[i].getAttribute('onclick').includes('{server_id}')) {{
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
        result["message"] = f"点击失败: {e}"
        return result
    
    time.sleep(2)
    
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
    except:
        pass
    
    wait_for_turnstile(sb, max_wait=45)
    time.sleep(3)
    
    sp = screenshot_path(acc_idx, f"server-{server_id}")
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
        result["message"] = f"状态未知 | 到期: {new_remaining}"
    
    status = "✅" if result["success"] else "❌"
    print(f"[INFO] {status} {result['message']}")
    return result


def process_account(sb, username: str, password: str, acc_idx: int) -> Dict[str, Any]:
    """处理单个账号"""
    account_result = {
        "username": username,
        "success": False,
        "message": "",
        "servers": []
    }
    
    # 登录
    if not login_zampto(sb, username, password, acc_idx):
        account_result["message"] = "登录失败"
        return account_result
    
    # 获取服务器
    servers = get_all_servers(sb, acc_idx)
    if not servers:
        account_result["message"] = "无服务器"
        logout_zampto(sb)
        return account_result
    
    # 续期每个服务器
    for server in servers:
        try:
            result = renew_server(sb, server["id"], acc_idx)
            result["name"] = server.get("name", f"Server {server['id']}")
            account_result["servers"].append(result)
            time.sleep(2)
        except Exception as e:
            account_result["servers"].append({
                "server_id": server["id"],
                "name": server.get("name"),
                "success": False,
                "message": str(e)
            })
    
    # 统计结果
    success_count = sum(1 for s in account_result["servers"] if s.get("success"))
    total_count = len(account_result["servers"])
    account_result["success"] = success_count > 0
    account_result["message"] = f"{success_count}/{total_count} 服务器续期成功"
    
    # 登出
    logout_zampto(sb)
    
    return account_result


def main():
    account_str = os.environ.get("ZAMPTO_ACCOUNT", "")
    
    if not account_str:
        print("[ERROR] 缺少环境变量 ZAMPTO_ACCOUNT")
        sys.exit(1)
    
    accounts = parse_accounts(account_str)
    if not accounts:
        print("[ERROR] 未解析到有效账号")
        sys.exit(1)
    
    print(f"[INFO] 解析到 {len(accounts)} 个账号")
    
    display = setup_display()
    all_results = []
    last_screenshot = None
    
    try:
        with SB(
            uc=True,
            test=True,
            locale="en",
            headed=not is_linux(),
            chromium_arg="--disable-blink-features=AutomationControlled",
        ) as sb:
            
            for idx, (username, password) in enumerate(accounts, 1):
                try:
                    result = process_account(sb, username, password, idx)
                    all_results.append(result)
                    
                    # 获取最后一张截图
                    for srv in reversed(result.get("servers", [])):
                        if srv.get("screenshot"):
                            last_screenshot = srv["screenshot"]
                            break
                    
                    time.sleep(3)
                except Exception as e:
                    print(f"[ERROR] 账号 {username} 处理失败: {e}")
                    all_results.append({
                        "username": username,
                        "success": False,
                        "message": str(e),
                        "servers": []
                    })
            
            final_sp = screenshot_path(0, "final")
            sb.save_screenshot(final_sp)
            last_screenshot = final_sp
    
    except Exception as e:
        print(f"[ERROR] 脚本执行失败: {e}")
        notify_telegram(False, "脚本错误", str(e))
        sys.exit(1)
    finally:
        if display:
            display.stop()
    
    # 汇总
    total_accounts = len(all_results)
    success_accounts = sum(1 for r in all_results if r.get("success"))
    total_servers = sum(len(r.get("servers", [])) for r in all_results)
    success_servers = sum(
        sum(1 for s in r.get("servers", []) if s.get("success"))
        for r in all_results
    )
    
    summary = f"📊 账号: {success_accounts}/{total_accounts} | 服务器: {success_servers}/{total_servers}\n"
    summary += "─" * 30 + "\n"
    
    for r in all_results:
        acc_status = "✅" if r.get("success") else "❌"
        summary += f"{acc_status} {r['username']}: {r.get('message', '')}\n"
        for srv in r.get("servers", []):
            srv_status = "  ✓" if srv.get("success") else "  ✗"
            summary += f"{srv_status} {srv.get('name', srv['server_id'])}: {srv.get('message', '')}\n"
    
    print(f"\n{'='*50}\n[结果汇总]\n{summary}{'='*50}")
    
    notify_telegram(
        ok=success_accounts == total_accounts and success_servers == total_servers,
        stage="完成",
        msg=summary,
        screenshot_file=last_screenshot
    )
    
    sys.exit(0 if success_servers > 0 else 1)


if __name__ == "__main__":
    main()
