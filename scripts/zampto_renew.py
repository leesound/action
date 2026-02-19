#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zampto 自动续期脚本"""

import os, sys, time, platform, requests, re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple
from seleniumbase import SB

AUTH_URL = "https://auth.zampto.net/sign-in?app_id=bmhk6c8qdqxphlyscztgl"
DASHBOARD_URL = "https://dash.zampto.net/homepage"
SERVER_URL = "https://dash.zampto.net/server?id={}"
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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
    return str(OUTPUT_DIR / f"acc{idx}-{datetime.now().strftime('%H%M%S')}-{name}.png")

def notify(ok: bool, stage: str, msg: str = "", img: str = None):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat: return
    try:
        text = f"🔔 Zampto: {'✅' if ok else '❌'} {stage}\n{msg}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=30)
        if img and Path(img).exists():
            with open(img, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data={"chat_id": chat}, files={"photo": f}, timeout=60)
    except: pass

def parse_accounts(s: str) -> List[Tuple[str, str]]:
    return [(p[0].strip(), p[1].strip()) for line in s.strip().split('\n') 
            if '----' in line and len(p := line.strip().split('----', 1)) == 2 and p[0].strip() and p[1].strip()]

def wait_turnstile(sb, wait: int = 60) -> bool:
    for i in range(wait):
        try:
            if sb.execute_script('var c=document.querySelector("input[name=cf-turnstile-response]");return c&&c.value.length>20'):
                print("[INFO] ✅ Turnstile 完成"); return True
        except: pass
        if i % 15 == 0 and i: print(f"[INFO] 等待验证... {i}s")
        time.sleep(1)
    return False

def login(sb, user: str, pwd: str, idx: int) -> bool:
    print(f"\n{'='*50}\n[INFO] 账号 {idx}: 登录 {user}\n{'='*50}")
    sb.uc_open_with_reconnect(AUTH_URL, reconnect_time=8.0)
    time.sleep(3); sb.save_screenshot(shot(idx, "01-login"))
    
    if "dash.zampto.net" in sb.get_current_url():
        print("[INFO] ✅ 已登录"); return True
    
    try: sb.wait_for_element('input[name="identifier"]', timeout=20)
    except: print("[ERROR] 未找到登录表单"); return False
    
    sb.type('input[name="identifier"]', user); time.sleep(1)
    sb.click('button[type="submit"]'); time.sleep(3)
    
    try: sb.wait_for_element('input[name="password"]', timeout=15)
    except: print("[ERROR] 密码页面未加载"); return False
    
    sb.type('input[name="password"]', pwd); time.sleep(1)
    sb.click('button[type="submit"]'); time.sleep(5)
    sb.save_screenshot(shot(idx, "02-result"))
    
    if "dash.zampto.net" in sb.get_current_url() or "sign-in" not in sb.get_current_url():
        print("[INFO] ✅ 登录成功"); return True
    print("[ERROR] 登录失败"); return False

def logout(sb):
    try: sb.execute_script('var a=document.querySelector("a[href*=logout]");if(a)a.click()'); time.sleep(2)
    except: pass
    sb.delete_all_cookies(); time.sleep(1)

def get_servers(sb, idx: int) -> List[Dict[str, str]]:
    print("[INFO] 获取服务器...")
    sb.open(DASHBOARD_URL); time.sleep(5)
    sb.save_screenshot(shot(idx, "03-dashboard"))
    
    src = sb.get_page_source()
    if "Access Blocked" in src or "VPN" in src:
        print("[ERROR] ⚠️ 访问被阻止"); return []
    
    servers = sb.execute_script('''
        var s=[];document.querySelectorAll('a[href*="/server?id="]').forEach(function(a){
            var m=a.href.match(/id=(\\d+)/);if(m&&!s.some(x=>x.id===m[1]))s.push({id:m[1],name:'Server '+m[1]})
        });return s
    ''') or []
    
    if not servers:
        servers = [{"id": m, "name": f"Server {m}"} for m in set(re.findall(r'/server\?id=(\d+)', src))]
    
    print(f"[INFO] 找到 {len(servers)} 个服务器")
    return servers

def renew(sb, sid: str, idx: int) -> Dict[str, Any]:
    result = {"server_id": sid, "success": False, "message": "", "screenshot": None}
    print(f"[INFO] 续期 {sid}...")
    
    sb.open(SERVER_URL.format(sid)); time.sleep(3)
    if "Access Blocked" in sb.get_page_source():
        result["message"] = "被阻止"; return result
    
    old = sb.execute_script('var e=document.getElementById("lastRenewalTime");return e?e.textContent:""')
    
    try:
        clicked = sb.execute_script(f'''
            var a=document.querySelectorAll('a[onclick*="handleServerRenewal"]');
            for(var i=0;i<a.length;i++)if(a[i].onclick.toString().includes('{sid}')){{a[i].click();return true}}
            if(typeof handleServerRenewal==='function'){{handleServerRenewal(null,{sid});return true}}
            return false
        ''')
        if not clicked: result["message"] = "无续期按钮"; return result
    except Exception as e: result["message"] = str(e); return result
    
    time.sleep(2)
    try: sb.uc_gui_click_captcha(); time.sleep(3)
    except: pass
    wait_turnstile(sb, 45); time.sleep(3)
    
    sp = shot(idx, f"srv-{sid}"); sb.save_screenshot(sp); result["screenshot"] = sp
    
    sb.open(SERVER_URL.format(sid)); time.sleep(2)
    new = sb.execute_script('var e=document.getElementById("lastRenewalTime");return e?e.textContent:""')
    remain = sb.execute_script('var e=document.getElementById("nextRenewalTime");return e?e.textContent:""')
    
    today = datetime.now().strftime('%b %d, %Y')
    if new != old: result["success"], result["message"] = True, f"成功 | 到期: {remain}"
    elif today in str(new): result["success"], result["message"] = True, f"已续期 | 到期: {remain}"
    elif any(x in str(remain) for x in ["1 day", "2 day"]): result["success"], result["message"] = True, f"可能成功 | {remain}"
    else: result["message"] = f"未知 | {remain}"
    
    print(f"[INFO] {'✅' if result['success'] else '❌'} {result['message']}")
    return result

def process(sb, user: str, pwd: str, idx: int) -> Dict[str, Any]:
    result = {"username": user, "success": False, "message": "", "servers": []}
    
    if not login(sb, user, pwd, idx):
        result["message"] = "登录失败"; return result
    
    servers = get_servers(sb, idx)
    if not servers:
        result["message"] = "无服务器"; logout(sb); return result
    
    for srv in servers:
        try:
            r = renew(sb, srv["id"], idx)
            r["name"] = srv.get("name", srv["id"])
            result["servers"].append(r)
            time.sleep(2)
        except Exception as e:
            result["servers"].append({"server_id": srv["id"], "success": False, "message": str(e)})
    
    ok = sum(1 for s in result["servers"] if s.get("success"))
    result["success"] = ok > 0
    result["message"] = f"{ok}/{len(result['servers'])} 成功"
    logout(sb); return result

def main():
    acc_str = os.environ.get("ZAMPTO_ACCOUNT", "")
    if not acc_str: print("[ERROR] 缺少 ZAMPTO_ACCOUNT"); sys.exit(1)
    
    accounts = parse_accounts(acc_str)
    if not accounts: print("[ERROR] 无有效账号"); sys.exit(1)
    print(f"[INFO] {len(accounts)} 个账号")
    
    proxy = os.environ.get("PROXY_SOCKS5", "")
    if proxy:
        try:
            ip = requests.get("https://api.ipify.org", proxies={"http": proxy, "https": proxy}, timeout=10).text
            print(f"[INFO] 代理IP: {ip}")
        except: pass
    
    display = setup_display()
    results, last_shot = [], None
    
    try:
        opts = {"uc": True, "test": True, "locale": "en", "headed": not is_linux()}
        if proxy: opts["proxy"] = proxy.replace("socks5://", "socks5://")
        
        with SB(**opts) as sb:
            for i, (u, p) in enumerate(accounts, 1):
                try:
                    r = process(sb, u, p, i)
                    results.append(r)
                    for s in reversed(r.get("servers", [])):
                        if s.get("screenshot"): last_shot = s["screenshot"]; break
                    time.sleep(3)
                except Exception as e:
                    results.append({"username": u, "success": False, "message": str(e), "servers": []})
            
            final = shot(0, "final"); sb.save_screenshot(final); last_shot = final
    except Exception as e:
        print(f"[ERROR] {e}"); notify(False, "错误", str(e)); sys.exit(1)
    finally:
        if display: display.stop()
    
    ok_acc = sum(1 for r in results if r.get("success"))
    total_srv = sum(len(r.get("servers", [])) for r in results)
    ok_srv = sum(sum(1 for s in r.get("servers", []) if s.get("success")) for r in results)
    
    summary = f"📊 账号: {ok_acc}/{len(results)} | 服务器: {ok_srv}/{total_srv}\n{'─'*30}\n"
    for r in results:
        summary += f"{'✅' if r.get('success') else '❌'} {r['username']}: {r.get('message','')}\n"
        for s in r.get("servers", []):
            summary += f"  {'✓' if s.get('success') else '✗'} {s.get('name',s['server_id'])}: {s.get('message','')}\n"
    
    print(f"\n{'='*50}\n{summary}{'='*50}")
    notify(ok_acc == len(results) and ok_srv == total_srv, "完成", summary, last_shot)
    sys.exit(0 if ok_srv > 0 else 1)

if __name__ == "__main__":
    main()
