#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zampto  自动续期脚本"""

import os, sys, time, platform, requests, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple
from seleniumbase import SB

AUTH_URL = "https://auth.zampto.net/sign-in?app_id=bmhk6c8qdqxphlyscztgl"
DASHBOARD_URL = "https://dash.zampto.net/homepage"
OVERVIEW_URL = "https://dash.zampto.net/overview"
SERVER_URL = "https://dash.zampto.net/server?id={}"
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CN_TZ = timezone(timedelta(hours=8))

def cn_now() -> datetime:
    return datetime.now(CN_TZ)

def cn_time_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return cn_now().strftime(fmt)

def parse_renewal_time(time_str: str) -> str:
    if not time_str:
        return "未知"
    try:
        dt = datetime.strptime(time_str, "%b %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=timezone.utc)
        dt_cn = dt.astimezone(CN_TZ)
        return dt_cn.strftime("%Y年%m月%d日 %H时%M分")
    except:
        return time_str

def calc_expiry_time(renewal_time_str: str, minutes: int = 2880) -> str:
    if not renewal_time_str:
        return "未知"
    try:
        dt = datetime.strptime(renewal_time_str, "%b %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=timezone.utc)
        expiry = dt + timedelta(minutes=minutes)
        expiry_cn = expiry.astimezone(CN_TZ)
        return expiry_cn.strftime("%Y年%m月%d日 %H时%M分")
    except:
        return "未知"

def mask(s: str, show: int = 1) -> str:
    if not s: return "***"
    s = str(s)
    if len(s) <= show: return s[0] + "***"
    return s[:show] + "*" * min(3, len(s) - show)

def is_linux(): return platform.system().lower() == "linux"

def setup_display():
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            d = Display(visible=False, size=(1920, 1080))
            d.start()
            os.environ["DISPLAY"] = d.new_display_var
            print("[INFO] 虚拟显示已启动")
            return d
        except Exception as e:
            print(f"[ERROR] 虚拟显示失败: {e}"); sys.exit(1)
    return None

def shot(idx: int, name: str) -> str:
    return str(OUTPUT_DIR / f"acc{idx}-{cn_now().strftime('%H%M%S')}-{name}.png")

def notify(ok: bool, stage: str, msg: str = "", img: str = None):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat: return
    try:
        text = f"🔔 Zampto: {'✅' if ok else '❌'} {stage}\n{msg}\n⏰ {cn_time_str()}"
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=30)
        if img and Path(img).exists():
            with open(img, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data={"chat_id": chat}, files={"photo": f}, timeout=60)
    except: pass

def parse_accounts(s: str) -> List[Tuple[str, str]]:
    return [(p[0].strip(), p[1].strip()) for line in s.strip().split('\n') 
            if '----' in line and len(p := line.strip().split('----', 1)) == 2 and p[0].strip() and p[1].strip()]

def wait_turnstile(sb, wait: int = 60) -> bool:
    print("[INFO] 等待 Turnstile...")
    for i in range(wait):
        try:
            result = sb.execute_script('''
                (function() {
                    var cf = document.querySelector("input[name='cf-turnstile-response']");
                    if (cf && cf.value && cf.value.length > 20) return "done";
                    return null;
                })()
            ''')
            if result == "done":
                print("[INFO] ✅ Turnstile 完成")
                return True
        except: pass
        if i % 15 == 0 and i: print(f"[INFO] 等待验证... {i}s")
        time.sleep(1)
    return False

def login(sb, user: str, pwd: str, idx: int) -> bool:
    user_masked = mask(user)
    print(f"\n{'='*50}\n[INFO] 账号 {idx}: 登录 {user_masked}\n{'='*50}")
    
    # 最多重试3次打开登录页
    for attempt in range(3):
        try:
            print(f"[INFO] 打开登录页 (尝试 {attempt + 1}/3)...")
            sb.uc_open_with_reconnect(AUTH_URL, reconnect_time=10.0)
            time.sleep(5)
            
            # 检查是否已登录
            current_url = sb.get_current_url()
            if "dash.zampto.net" in current_url:
                print("[INFO] ✅ 已登录")
                return True
            
            sb.save_screenshot(shot(idx, f"01-login-{attempt}"))
            
            # 等待页面加载完成
            for _ in range(10):
                src = sb.get_page_source()
                if 'identifier' in src or 'email' in src or 'username' in src:
                    break
                time.sleep(2)
            
            # 尝试多种选择器查找输入框
            selectors = [
                'input[name="identifier"]',
                'input[type="email"]',
                'input[type="text"]',
                '#identifier',
                'input[placeholder*="email" i]',
                'input[placeholder*="user" i]'
            ]
            
            input_found = False
            for sel in selectors:
                try:
                    sb.wait_for_element(sel, timeout=5)
                    print(f"[INFO] 找到输入框: {sel}")
                    input_found = True
                    
                    # 输入用户名
                    sb.type(sel, user)
                    time.sleep(1)
                    break
                except:
                    continue
            
            if not input_found:
                print(f"[WARN] 尝试 {attempt + 1}: 未找到输入框")
                sb.save_screenshot(shot(idx, f"01-noinput-{attempt}"))
                if attempt < 2:
                    time.sleep(5)
                    continue
                else:
                    print("[ERROR] 未找到登录表单")
                    return False
            
            # 点击继续按钮
            try:
                sb.click('button[type="submit"]')
            except:
                try:
                    sb.click('button')
                except:
                    pass
            
            time.sleep(4)
            
            # 等待密码输入框
            pwd_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                '#password'
            ]
            
            pwd_found = False
            for _ in range(15):
                for sel in pwd_selectors:
                    try:
                        if sb.is_element_visible(sel):
                            sb.type(sel, pwd)
                            pwd_found = True
                            print("[INFO] 已输入密码")
                            break
                    except:
                        continue
                if pwd_found:
                    break
                time.sleep(1)
            
            if not pwd_found:
                print("[WARN] 密码页面未加载")
                sb.save_screenshot(shot(idx, f"02-nopwd-{attempt}"))
                if attempt < 2:
                    continue
                return False
            
            time.sleep(1)
            
            # 点击登录按钮
            try:
                sb.click('button[type="submit"]')
            except:
                try:
                    sb.click('button')
                except:
                    pass
            
            time.sleep(6)
            sb.save_screenshot(shot(idx, "02-result"))
            
            # 检查登录结果
            current_url = sb.get_current_url()
            if "dash.zampto.net" in current_url or "sign-in" not in current_url:
                print("[INFO] ✅ 登录成功")
                return True
            
            print(f"[WARN] 尝试 {attempt + 1}: 登录未成功，URL: {current_url}")
            
        except Exception as e:
            print(f"[WARN] 尝试 {attempt + 1} 异常: {e}")
            sb.save_screenshot(shot(idx, f"01-error-{attempt}"))
            if attempt < 2:
                time.sleep(5)
                continue
    
    print("[ERROR] 登录失败")
    return False

def logout(sb):
    try:
        sb.delete_all_cookies()
        sb.open("about:blank")
        time.sleep(1)
        print("[INFO] 已退出登录")
    except Exception as e:
        print(f"[WARN] 退出时出错: {e}")

def get_servers(sb, idx: int) -> List[Dict[str, str]]:
    print("[INFO] 获取服务器列表...")
    servers = []
    seen_ids = set()
    
    sb.open(DASHBOARD_URL)
    time.sleep(5)
    sb.save_screenshot(shot(idx, "03-dashboard"))
    
    src = sb.get_page_source()
    if "Access Blocked" in src or "VPN or Proxy Detected" in src:
        print("[ERROR] ⚠️ 访问被阻止")
        return []
    
    matches = re.findall(r'href="[^"]*?/server\?id=(\d+)"', src)
    for sid in matches:
        if sid not in seen_ids:
            seen_ids.add(sid)
            servers.append({"id": sid, "name": f"Server {sid}"})
    
    sb.open(OVERVIEW_URL)
    time.sleep(3)
    sb.save_screenshot(shot(idx, "04-overview"))
    
    src = sb.get_page_source()
    matches = re.findall(r'href="[^"]*?/server\?id=(\d+)"', src)
    for sid in matches:
        if sid not in seen_ids:
            seen_ids.add(sid)
            servers.append({"id": sid, "name": f"Server {sid}"})
    
    try:
        js_servers = sb.execute_script('''
            (function() {
                var servers = [];
                var links = document.querySelectorAll('a[href*="/server?id="]');
                links.forEach(function(a) {
                    var match = a.href.match(/id=(\\d+)/);
                    if (match) servers.push(match[1]);
                });
                return [...new Set(servers)];
            })()
        ''')
        if js_servers:
            for sid in js_servers:
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    servers.append({"id": str(sid), "name": f"Server {sid}"})
    except: pass
    
    print(f"[INFO] 找到 {len(servers)} 个服务器")
    for s in servers:
        print(f"  - ID: {mask(s['id'])}")
    return servers

def renew(sb, sid: str, idx: int) -> Dict[str, Any]:
    sid_masked = mask(sid)
    result = {"server_id": sid, "success": False, "message": "", "screenshot": None, 
              "old_time": "", "new_time": "", "old_time_cn": "", "new_time_cn": "", "expiry_cn": ""}
    print(f"[INFO] 续期服务器 {sid_masked}...")
    
    sb.open(SERVER_URL.format(sid))
    time.sleep(4)
    
    src = sb.get_page_source()
    if "Access Blocked" in src:
        result["message"] = "访问被阻止"
        return result
    
    old_renewal = ""
    try:
        old_renewal = sb.execute_script('''
            (function() {
                var el = document.getElementById("lastRenewalTime");
                return el ? el.textContent.trim() : "";
            })()
        ''')
    except: pass
    
    result["old_time"] = old_renewal
    result["old_time_cn"] = parse_renewal_time(old_renewal)
    print(f"[INFO] 续期前时间: {old_renewal}")
    
    try:
        clicked = sb.execute_script(f'''
            (function() {{
                var links = document.querySelectorAll('a[onclick*="handleServerRenewal"]');
                for (var i = 0; i < links.length; i++) {{
                    if (links[i].getAttribute('onclick').includes('{sid}')) {{
                        links[i].click();
                        return true;
                    }}
                }}
                var btns = document.querySelectorAll('a.action-button, button');
                for (var i = 0; i < btns.length; i++) {{
                    if (btns[i].textContent.toLowerCase().includes('renew')) {{
                        btns[i].click();
                        return true;
                    }}
                }}
                return false;
            }})()
        ''')
        
        if not clicked:
            result["message"] = "未找到续期按钮"
            sb.save_screenshot(shot(idx, f"srv-{sid}-nobtn"))
            return result
            
    except Exception as e:
        result["message"] = f"点击失败: {e}"
        return result
    
    print("[INFO] 已点击续期按钮，等待验证...")
    time.sleep(3)
    
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
    except: pass
    
    wait_turnstile(sb, 60)
    time.sleep(5)
    
    sp = shot(idx, f"srv-{sid}")
    sb.save_screenshot(sp)
    result["screenshot"] = sp
    
    sb.open(SERVER_URL.format(sid))
    time.sleep(3)
    
    new_renewal = ""
    remain = ""
    try:
        new_renewal = sb.execute_script('''
            (function() {
                var el = document.getElementById("lastRenewalTime");
                return el ? el.textContent.trim() : "";
            })()
        ''')
        remain = sb.execute_script('''
            (function() {
                var el = document.getElementById("nextRenewalTime");
                return el ? el.textContent.trim() : "";
            })()
        ''')
    except: pass
    
    result["new_time"] = new_renewal
    result["new_time_cn"] = parse_renewal_time(new_renewal)
    result["expiry_cn"] = calc_expiry_time(new_renewal)
    
    print(f"[INFO] 续期后时间: {new_renewal}, 剩余: {remain}")
    
    today = datetime.now().strftime('%b %d, %Y')
    if new_renewal and new_renewal != old_renewal:
        result["success"] = True
        result["message"] = f"{result['old_time_cn']} -> {result['expiry_cn']}"
    elif today in str(new_renewal):
        result["success"] = True
        result["message"] = f"今日已续期 | {result['new_time_cn']} -> {result['expiry_cn']}"
    elif remain and ("1 day" in remain or "2 day" in remain or "hour" in remain):
        result["success"] = True
        result["message"] = f"{result['new_time_cn']} -> {result['expiry_cn']}"
    else:
        result["message"] = f"状态未知 | {result['new_time_cn']}"
    
    result_shot = shot(idx, f"srv-{sid}-result")
    sb.save_screenshot(result_shot)
    result["screenshot"] = result_shot
    
    print(f"[INFO] {'✅' if result['success'] else '⚠️'} {result['message']}")
    return result

def process(sb, user: str, pwd: str, idx: int) -> Dict[str, Any]:
    result = {"username": user, "success": False, "message": "", "servers": []}
    
    if not login(sb, user, pwd, idx):
        result["message"] = "登录失败"
        return result
    
    servers = get_servers(sb, idx)
    if not servers:
        result["message"] = "无服务器或访问被阻止"
        logout(sb)
        return result
    
    for srv in servers:
        try:
            r = renew(sb, srv["id"], idx)
            r["name"] = srv.get("name", srv["id"])
            result["servers"].append(r)
            time.sleep(3)
        except Exception as e:
            print(f"[ERROR] 服务器 {mask(srv['id'])} 续期异常: {e}")
            result["servers"].append({"server_id": srv["id"], "success": False, "message": str(e)})
    
    ok = sum(1 for s in result["servers"] if s.get("success"))
    result["success"] = ok > 0
    result["message"] = f"{ok}/{len(result['servers'])} 成功"
    
    sb.open(DASHBOARD_URL)
    time.sleep(2)
    final_shot = shot(idx, "05-final")
    sb.save_screenshot(final_shot)
    result["final_screenshot"] = final_shot
    
    logout(sb)
    return result

def main():
    acc_str = os.environ.get("ZAMPTO_ACCOUNT", "")
    if not acc_str:
        print("[ERROR] 缺少 ZAMPTO_ACCOUNT")
        sys.exit(1)
    
    accounts = parse_accounts(acc_str)
    if not accounts:
        print("[ERROR] 无有效账号")
        sys.exit(1)
    
    print(f"[INFO] {len(accounts)} 个账号")
    
    proxy = os.environ.get("PROXY_SOCKS5", "")
    if proxy:
        try:
            requests.get("https://api.ipify.org", proxies={"http": proxy, "https": proxy}, timeout=10)
            print("[INFO] 代理连接正常")
        except Exception as e:
            print(f"[WARN] 代理测试失败: {e}")
    
    display = setup_display()
    results, last_shot = [], None
    
    try:
        opts = {"uc": True, "test": True, "locale": "en", "headed": not is_linux()}
        if proxy:
            opts["proxy"] = proxy
            print("[INFO] 使用代理模式")
        
        with SB(**opts) as sb:
            for i, (u, p) in enumerate(accounts, 1):
                try:
                    r = process(sb, u, p, i)
                    results.append(r)
                    if r.get("final_screenshot"):
                        last_shot = r["final_screenshot"]
                    else:
                        for s in reversed(r.get("servers", [])):
                            if s.get("screenshot"):
                                last_shot = s["screenshot"]
                                break
                    time.sleep(3)
                except Exception as e:
                    print(f"[ERROR] 账号 {mask(u)} 异常: {e}")
                    results.append({"username": u, "success": False, "message": str(e), "servers": []})
            
    except Exception as e:
        print(f"[ERROR] 脚本异常: {e}")
        notify(False, "错误", str(e))
        sys.exit(1)
    finally:
        if display:
            display.stop()
    
    ok_acc = sum(1 for r in results if r.get("success"))
    total_srv = sum(len(r.get("servers", [])) for r in results)
    ok_srv = sum(sum(1 for s in r.get("servers", []) if s.get("success")) for r in results)
    
    log_summary = f"📊 账号: {ok_acc}/{len(results)} | 服务器: {ok_srv}/{total_srv}\n{'─'*30}\n"
    for r in results:
        log_summary += f"{'✅' if r.get('success') else '❌'} {mask(r['username'])}: {r.get('message','')}\n"
        for s in r.get("servers", []):
            log_summary += f"  {'✓' if s.get('success') else '✗'} Server {mask(s['server_id'])}: {s.get('message','')}\n"
    
    print(f"\n{'='*50}\n{log_summary}{'='*50}")
    
    notify_summary = f"📊 账号: {ok_acc}/{len(results)} | 服务器: {ok_srv}/{total_srv}\n{'─'*30}\n"
    for r in results:
        notify_summary += f"{'✅' if r.get('success') else '❌'} {r['username']}: {r.get('message','')}\n"
        for s in r.get("servers", []):
            status = '✓' if s.get('success') else '✗'
            notify_summary += f"  {status} Server {s['server_id']}: {s.get('message','')}\n"
    
    notify(ok_acc == len(results) and ok_srv == total_srv, "完成", notify_summary, last_shot)
    sys.exit(0 if ok_srv > 0 else 1)

if __name__ == "__main__":
    main()
