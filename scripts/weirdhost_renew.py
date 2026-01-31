#!/usr/bin/env python3
"""
WeirdHost 自动续期 v36
- 通过提示框颜色/类型判断结果
- 红色 Error = 冷却期
- 绿色 = 成功
"""

import os
import sys
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Dict
from base64 import b64encode, b64decode

BASE_URL = "https://hub.weirdhost.xyz"

def calculate_remaining_time(expiry_str: str) -> str:
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return "无法解析"
        
        diff = expiry_dt - datetime.now()
        if diff.total_seconds() < 0:
            return "已过期"
        
        days = diff.days
        hours = diff.seconds // 3600
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        return " ".join(parts) if parts else "不到1小时"
    except:
        return "计算失败"


def screenshot(sb, name: str) -> str:
    path = f"./{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[截图] {path}")
    except Exception as e:
        print(f"[截图] 失败: {e}")
    return path


def update_github_secret(secret_name: str, secret_value: str) -> bool:
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
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_data["key_id"]
        }).encode('utf-8')
        
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "WeirdHost-Renew")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print("[GitHub] ✓ Secret 已更新")
                return True
        return False
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
        return False


def send_telegram_photo(photo_path: str, caption: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    
    if not os.path.exists(photo_path):
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        
        body = b"\r\n".join([
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="chat_id"',
            b"",
            chat_id.encode(),
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="caption"',
            b"",
            caption.encode('utf-8'),
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="parse_mode"',
            b"",
            b"HTML",
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="photo"; filename="screenshot.png"',
            b"Content-Type: image/png",
            b"",
            photo_data,
            f"--{boundary}--".encode(),
            b""
        ])
        
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        urllib.request.urlopen(req, timeout=60)
        print("[TG] ✓ 图片通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 图片发送失败: {e}")
        return False


def send_telegram_text(message: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode('utf-8')
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=30)
        print("[TG] ✓ 文本通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")
        return False


def notify_telegram(message: str, photo_path: Optional[str] = None):
    if photo_path and os.path.exists(photo_path):
        if send_telegram_photo(photo_path, message):
            return
    send_telegram_text(message)


def is_turnstile_active(sb) -> bool:
    """检查 Turnstile 是否正在验证"""
    try:
        return sb.execute_script("""
            // iframe 存在
            if (document.querySelector('iframe[src*="challenges.cloudflare.com"]')) return true;
            // Verifying 文字
            if (document.body.innerText.includes('Verifying')) return true;
            return false;
        """) or False
    except:
        return False


def wait_for_turnstile(sb, timeout: int = 120) -> bool:
    """等待 Turnstile 验证完成"""
    print("[Turnstile] 等待验证...")
    start = time.time()
    clicks = 0
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        
        if not is_turnstile_active(sb):
            print(f"[Turnstile] ✓ 完成 ({elapsed}s)")
            return True
        
        # 每20秒点击一次
        if elapsed > 0 and elapsed % 20 == 0 and clicks < 4:
            try:
                print(f"[Turnstile] 点击... ({elapsed}s)")
                sb.uc_gui_click_captcha()
                clicks += 1
            except:
                pass
        
        if elapsed % 15 == 0:
            print(f"[Turnstile] 等待... ({elapsed}s)")
        
        time.sleep(1)
    
    print(f"[Turnstile] ✗ 超时")
    return False


def check_result_message(sb) -> dict:
    """检查页面上的结果提示框"""
    try:
        result = sb.execute_script("""
            var result = {found: false, type: null, message: ''};
            
            // 查找 MessageBox 提示框
            var boxes = document.querySelectorAll('[class*="MessageBox"], [role="alert"]');
            for (var box of boxes) {
                var text = box.innerText || '';
                var html = box.outerHTML || '';
                
                // 检查是否是 Error (红色/冷却期)
                if (text.includes('Error') || html.includes('error') || html.includes('red')) {
                    if (text.includes('갱신할 수 없습니다') || text.includes('기다려주세요')) {
                        result.found = true;
                        result.type = 'cooldown';
                        result.message = text;
                        return result;
                    }
                }
                
                // 检查是否是 Success (绿色)
                if (html.includes('success') || html.includes('green') || text.includes('Success')) {
                    result.found = true;
                    result.type = 'success';
                    result.message = text;
                    return result;
                }
            }
            
            // 备用：直接搜索页面文字
            var body = document.body.innerText || '';
            if (body.includes('아직 서버를 갱신할 수 없습니다')) {
                result.found = true;
                result.type = 'cooldown';
                result.message = '冷却期内';
            }
            
            return result;
        """)
        return result if result else {"found": False, "type": None, "message": ""}
    except:
        return {"found": False, "type": None, "message": ""}


def get_expiry(sb) -> Optional[str]:
    """获取到期时间"""
    try:
        source = sb.get_page_source()
        match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', source)
        return match.group(1) if match else None
    except:
        return None


def parse_cookies(cookie_str: str) -> Dict[str, str]:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def run_renew(cookie_str: str, server_id: str, proxy: Optional[str] = None) -> Dict:
    from seleniumbase import SB
    
    result = {
        "success": False,
        "is_cooldown": False,
        "cookie_expired": False,
        "message": "",
        "expiry": "",
        "new_cookie": None,
        "screenshot": None
    }
    
    cookies = parse_cookies(cookie_str)
    server_url = f"{BASE_URL}/server/{server_id}"
    
    sb_opts = {
        "uc": True,
        "test": True,
        "locale": "en",
        "headless": False,
        "uc_cdp_events": True,
    }
    
    if proxy:
        addr = proxy.replace("socks5://", "").replace("socks5h://", "")
        sb_opts["proxy"] = f"socks5://{addr}"
        print(f"[浏览器] 代理: {addr}")
    
    try:
        print("[浏览器] 启动...")
        
        with SB(**sb_opts) as sb:
            # 直接访问服务器页面
            print(f"[浏览器] 访问 {server_url}")
            sb.uc_open_with_reconnect(server_url, reconnect_time=8)
            time.sleep(4)
            
            # 注入 Cookie 并刷新
            for name, value in cookies.items():
                try:
                    sb.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
                except:
                    pass
            
            sb.refresh()
            time.sleep(5)
            screenshot(sb, "01-page")
            
            # 检查登录状态
            if "/login" in sb.get_current_url():
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                screenshot(sb, "debug_result")
                result["screenshot"] = "debug_result.png"
                return result
            
            # 获取初始到期时间
            initial_expiry = get_expiry(sb)
            if initial_expiry:
                result["expiry"] = initial_expiry
                print(f"[浏览器] 到期时间: {initial_expiry}")
            
            # 点击续期按钮
            print("[浏览器] 点击续期按钮...")
            clicked = sb.execute_script("""
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    if ((b.innerText || '').trim() === '시간추가') {
                        b.scrollIntoView({block: 'center'});
                        b.click();
                        return true;
                    }
                }
                return false;
            """)
            
            if not clicked:
                result["message"] = "未找到续期按钮"
                screenshot(sb, "debug_result")
                result["screenshot"] = "debug_result.png"
                return result
            
            print("[浏览器] ✓ 已点击")
            time.sleep(3)
            screenshot(sb, "02-clicked")
            
            # 处理 Turnstile
            if is_turnstile_active(sb):
                if not wait_for_turnstile(sb, 120):
                    result["message"] = "Turnstile 超时"
                    screenshot(sb, "debug_result")
                    result["screenshot"] = "debug_result.png"
                    return result
                time.sleep(2)
                screenshot(sb, "03-turnstile-done")
            
            # 等待结果提示框
            print("[浏览器] 等待结果...")
            
            for i in range(60):
                time.sleep(1)
                
                # 检查 URL
                if "hub.weirdhost.xyz" not in sb.get_current_url():
                    print("[浏览器] ⚠ 页面跳转")
                    sb.uc_open_with_reconnect(server_url, reconnect_time=5)
                    time.sleep(3)
                    continue
                
                # 如果还在验证
                if is_turnstile_active(sb):
                    if i % 10 == 0:
                        print(f"[浏览器] 验证中... ({i}s)")
                    continue
                
                # 检查结果提示框
                msg = check_result_message(sb)
                
                if msg["found"]:
                    if msg["type"] == "cooldown":
                        result["is_cooldown"] = True
                        result["message"] = "冷却期内"
                        print("[浏览器] ⏳ 冷却期")
                        break
                    elif msg["type"] == "success":
                        result["success"] = True
                        result["message"] = "续期成功"
                        print("[浏览器] ✓ 成功")
                        break
                
                # 检查日期变化
                new_expiry = get_expiry(sb)
                if new_expiry and initial_expiry and new_expiry != initial_expiry:
                    result["success"] = True
                    result["expiry"] = new_expiry
                    result["message"] = "续期成功"
                    print(f"[浏览器] ✓ 日期更新: {new_expiry}")
                    break
                
                if i % 15 == 14:
                    print(f"[浏览器] 等待... ({i+1}s)")
            
            # 最终截图
            screenshot(sb, "debug_result")
            result["screenshot"] = "debug_result.png"
            
            # 更新最终到期时间
            final_expiry = get_expiry(sb)
            if final_expiry:
                result["expiry"] = final_expiry
            
            # 获取新 Cookie
            try:
                for c in sb.get_cookies():
                    if c["name"].startswith("remember_web"):
                        new_c = c["name"] + "=" + c["value"]
                        if new_c != cookie_str:
                            result["new_cookie"] = new_c
                        break
            except:
                pass
        
    except Exception as e:
        result["message"] = str(e)
        print(f"[浏览器] ✗ 异常: {e}")
        import traceback
        traceback.print_exc()
    
    return result


def main():
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    proxy = os.environ.get("SOCKS_PROXY", "").strip()
    
    if not cookie or not server_id:
        print("❌ 请设置 WEIRDHOST_COOKIE 和 WEIRDHOST_ID")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v36")
    print("=" * 50)
    print(f"服务器: {server_id}")
    print(f"代理: {proxy or '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    result = run_renew(cookie, server_id, proxy or None)
    
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    shot = result.get("screenshot")
    
    if result["success"]:
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, shot)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[结果] 冷却期内，到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>", shot)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", shot)
        sys.exit(1)


if __name__ == "__main__":
    main()
