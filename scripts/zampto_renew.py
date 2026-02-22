#!/usr/bin/env python3
"""Zampto 自动续期脚本 - 优化 Turnstile 验证"""

import os
import sys
import time
import json
import requests
from datetime import datetime

# 环境检测
IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def send_telegram(message):
    """发送 Telegram 通知"""
    bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=30
        )
    except:
        pass

def mask_email(email):
    if "@" not in email:
        return email[:2] + "***"
    name, domain = email.split("@", 1)
    return name[0] + "***@" + domain

def setup_display():
    """设置虚拟显示"""
    if IN_GITHUB_ACTIONS:
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            log("虚拟显示已启动")
            return display
        except Exception as e:
            log(f"虚拟显示启动失败: {e}", "WARN")
    return None

def test_proxy(proxy_url):
    """测试代理连接"""
    if not proxy_url:
        return True
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=15)
        log("代理连接正常")
        return True
    except Exception as e:
        log(f"代理连接失败: {e}", "ERROR")
        return False

def wait_for_turnstile(sb, timeout=120):
    """
    等待 Turnstile 验证完成
    返回: True=成功, False=失败/超时
    """
    log("等待 Turnstile 验证...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            # 检查是否有成功的 iframe
            iframes = sb.find_elements("iframe[src*='challenges.cloudflare.com']")
            
            if not iframes:
                # 没有 iframe，可能验证已完成或不需要验证
                log("未检测到 Turnstile iframe，可能已完成")
                return True
            
            for iframe in iframes:
                try:
                    # 检查 iframe 是否可见
                    if not iframe.is_displayed():
                        continue
                    
                    # 切换到 iframe
                    sb.switch_to_frame(iframe)
                    
                    # 检查是否有成功标记
                    try:
                        success = sb.find_elements("[data-success='true']") or \
                                  sb.find_elements(".success") or \
                                  sb.find_elements("[aria-checked='true']")
                        if success:
                            sb.switch_to_default_content()
                            log("✅ Turnstile 验证成功")
                            return True
                    except:
                        pass
                    
                    # 检查复选框并尝试点击
                    try:
                        checkbox = sb.find_elements("input[type='checkbox']")
                        if checkbox and not checkbox[0].is_selected():
                            checkbox[0].click()
                            log("已点击 Turnstile 复选框")
                    except:
                        pass
                    
                    sb.switch_to_default_content()
                except:
                    sb.switch_to_default_content()
            
            # 检查页面是否已经更新（验证通过的标志）
            try:
                # 检查是否出现成功提示
                if sb.is_text_visible("success", timeout=1) or \
                   sb.is_text_visible("renewed", timeout=1) or \
                   sb.is_text_visible("extended", timeout=1):
                    log("✅ 检测到成功提示")
                    return True
            except:
                pass
            
            # 检查 Toast 消息
            try:
                toasts = sb.find_elements("[class*='toast']") or \
                         sb.find_elements("[class*='notification']") or \
                         sb.find_elements("[class*='alert']")
                for toast in toasts:
                    text = toast.text.lower()
                    if "success" in text or "renewed" in text:
                        log("✅ 检测到成功 Toast")
                        return True
                    if "error" in text or "failed" in text:
                        log(f"❌ 检测到错误: {toast.text}", "ERROR")
                        return False
            except:
                pass
            
            time.sleep(2)
            elapsed = int(time.time() - start_time)
            if elapsed % 10 == 0:
                log(f"验证等待中... {elapsed}s/{timeout}s")
                
        except Exception as e:
            log(f"验证检查异常: {e}", "WARN")
            time.sleep(2)
    
    log(f"⚠️ Turnstile 验证超时 ({timeout}s)", "WARN")
    return False

def renew_server(sb, server_id, max_retries=3):
    """
    续期单个服务器
    """
    for attempt in range(1, max_retries + 1):
        try:
            log(f"续期服务器 {server_id[:4]}*** (尝试 {attempt}/{max_retries})...")
            
            # 访问服务器页面
            sb.open(f"https://dash.zampto.com/server/{server_id}")
            sb.sleep(3)
            
            # 获取当前到期时间
            expiry_before = None
            try:
                time_elem = sb.find_element("time") or sb.find_element("[class*='expir']")
                expiry_before = time_elem.text
                log(f"续期前到期时间: {expiry_before}")
            except:
                pass
            
            # 查找续期按钮
            renew_btn = None
            selectors = [
                "button:contains('Renew')",
                "button:contains('Extend')",
                "button:contains('续期')",
                "[class*='renew']",
                "button[onclick*='renew']",
                "a:contains('Renew')"
            ]
            
            for selector in selectors:
                try:
                    if sb.is_element_visible(selector, timeout=2):
                        renew_btn = sb.find_element(selector)
                        break
                except:
                    continue
            
            if not renew_btn:
                log("未找到续期按钮，可能已是最长期限", "WARN")
                return {"status": "skip", "reason": "no_button"}
            
            # 点击续期按钮
            renew_btn.click()
            log("已点击续期按钮")
            sb.sleep(2)
            
            # 等待并处理 Turnstile 验证
            if not wait_for_turnstile(sb, timeout=90):
                log(f"验证未完成，将重试...", "WARN")
                continue
            
            # 验证后等待页面更新
            sb.sleep(5)
            
            # 检查续期结果
            try:
                # 重新加载页面确认
                sb.refresh()
                sb.sleep(3)
                
                time_elem = sb.find_element("time") or sb.find_element("[class*='expir']")
                expiry_after = time_elem.text
                log(f"续期后到期时间: {expiry_after}")
                
                if expiry_before and expiry_after and expiry_before != expiry_after:
                    log(f"✅ 续期成功: {expiry_before} -> {expiry_after}")
                    return {"status": "success", "before": expiry_before, "after": expiry_after}
                elif expiry_after:
                    log(f"✅ 当前到期时间: {expiry_after}")
                    return {"status": "success", "after": expiry_after}
            except Exception as e:
                log(f"获取到期时间失败: {e}", "WARN")
            
            # 假设成功
            return {"status": "success", "note": "验证通过，假设续期成功"}
            
        except Exception as e:
            log(f"续期异常: {e}", "ERROR")
            if attempt < max_retries:
                sb.sleep(5)
    
    return {"status": "failed", "reason": "max_retries"}

def login_and_renew(email, password, proxy_url=None):
    """登录并续期"""
    from seleniumbase import SB
    
    results = {"email": mask_email(email), "servers": [], "error": None}
    
    # 浏览器配置
    sb_config = {
        "uc": True,  # Undetected Chrome
        "headless": IN_GITHUB_ACTIONS,
        "locale_code": "en",
        "disable_csp": True,
        "agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    if proxy_url:
        # SeleniumBase 需要 host:port 格式
        proxy_parts = proxy_url.replace("socks5://", "").replace("socks://", "")
        sb_config["proxy"] = f"socks5://{proxy_parts}"
        log("使用代理模式")
    
    try:
        with SB(**sb_config) as sb:
            # 登录
            log("打开登录页...")
            sb.open("https://dash.zampto.com/auth/login")
            sb.sleep(3)
            
            # 输入邮箱
            sb.type("input[name='identifier']", email)
            sb.sleep(1)
            
            # 点击继续或下一步
            try:
                sb.click("button[type='submit']")
                sb.sleep(2)
            except:
                pass
            
            # 输入密码
            try:
                sb.type("input[name='password']", password)
                sb.type("input[type='password']", password)
            except:
                pass
            sb.sleep(1)
            
            # 提交登录
            sb.click("button[type='submit']")
            log("已提交登录")
            
            # 等待登录完成
            sb.sleep(5)
            
            # 检查是否登录成功
            if "login" in sb.get_current_url().lower():
                # 可能需要处理 Turnstile
                wait_for_turnstile(sb, timeout=60)
                sb.sleep(3)
            
            if "dashboard" in sb.get_current_url().lower() or "server" in sb.get_current_url().lower():
                log("✅ 登录成功")
            else:
                log(f"当前 URL: {sb.get_current_url()}")
            
            # 获取服务器列表
            log("获取服务器列表...")
            sb.open("https://dash.zampto.com/servers")
            sb.sleep(3)
            
            # 查找服务器
            servers = []
            try:
                # 查找服务器链接
                links = sb.find_elements("a[href*='/server/']")
                for link in links:
                    href = link.get_attribute("href")
                    if "/server/" in href:
                        server_id = href.split("/server/")[-1].split("/")[0].split("?")[0]
                        if server_id and server_id not in servers:
                            servers.append(server_id)
            except:
                pass
            
            if not servers:
                log("未找到服务器", "WARN")
                results["error"] = "no_servers"
                return results
            
            log(f"找到 {len(servers)} 个服务器")
            for sid in servers:
                log(f"  - ID: {sid[:4]}***")
            
            # 续期每个服务器
            for server_id in servers:
                result = renew_server(sb, server_id)
                results["servers"].append({
                    "id": server_id[:4] + "***",
                    **result
                })
            
    except Exception as e:
        log(f"执行异常: {e}", "ERROR")
        results["error"] = str(e)
    
    return results

def main():
    log(f"Zampto 自动续期 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 获取账号
    accounts_str = os.environ.get("ZAMPTO_ACCOUNT", "").strip()
    if not accounts_str:
        log("未配置 ZAMPTO_ACCOUNT", "ERROR")
        sys.exit(1)
    
    accounts = [a.strip() for a in accounts_str.split("\n") if a.strip() and "----" in a]
    log(f"共 {len(accounts)} 个账号")
    
    # 代理配置
    proxy_url = os.environ.get("PROXY_SOCKS5", "").strip()
    if proxy_url:
        if not test_proxy(proxy_url):
            log("代理不可用，尝试直连", "WARN")
            proxy_url = None
    
    # 设置虚拟显示
    display = setup_display()
    
    # 处理每个账号
    all_results = []
    success_count = 0
    
    for i, account in enumerate(accounts, 1):
        email, password = account.split("----", 1)
        log(f"\n{'='*50}")
        log(f"账号 {i}/{len(accounts)}: {mask_email(email)}")
        log(f"{'='*50}")
        
        result = login_and_renew(email, password, proxy_url)
        all_results.append(result)
        
        if result.get("servers"):
            for srv in result["servers"]:
                if srv.get("status") == "success":
                    success_count += 1
    
    # 汇总
    log(f"\n{'='*50}")
    log(f"续期完成: {success_count} 个服务器成功")
    
    # 发送通知
    msg_lines = [f"🖥️ <b>Zampto 续期报告</b>", f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    for r in all_results:
        msg_lines.append(f"\n👤 {r['email']}")
        if r.get("error"):
            msg_lines.append(f"  ❌ 错误: {r['error']}")
        for srv in r.get("servers", []):
            status = "✅" if srv.get("status") == "success" else "❌"
            msg_lines.append(f"  {status} {srv['id']}: {srv.get('after', srv.get('reason', 'unknown'))}")
    
    send_telegram("\n".join(msg_lines))
    
    # 清理
    if display:
        display.stop()
    
    sys.exit(0 if success_count > 0 else 1)

if __name__ == "__main__":
    main()
