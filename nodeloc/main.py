# nodeloc/main.py
import os
import re
import time
import logging
import requests
from browser import create_browser
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

BASE_URL = "https://www.nodeloc.com"

def mask_username(username):
    """隐藏用户名中间部分"""
    if len(username) <= 3:
        return username[0] + '*' * (len(username) - 1)
    elif len(username) <= 6:
        return username[:2] + '*' * (len(username) - 2)
    else:
        return username[:2] + '*' * (len(username) - 4) + username[-2:]

def send_telegram(message):
    """发送Telegram通知"""
    token = os.environ.get('TG_BOT_TOKEN')
    chat_id = os.environ.get('TG_CHAT_ID')
    
    if not token or not chat_id:
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'
    }
    
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            log.info("📨 Telegram通知发送成功")
        else:
            log.warning(f"Telegram通知失败: {resp.text}")
    except Exception as e:
        log.warning(f"Telegram通知异常: {e}")

def parse_accounts():
    """解析账号配置，格式: 每行 username:password"""
    raw = os.environ.get("NL_ACCOUNT", "")
    accounts = []
    
    for line in raw.strip().split('\n'):
        line = line.strip()
        if not line or ':' not in line:
            continue
        parts = line.split(':', 1)
        if len(parts) == 2:
            accounts.append({
                "username": parts[0].strip(),
                "password": parts[1].strip()
            })
    
    return accounts

def process_account(username, password):
    """处理单个账号签到"""
    masked = mask_username(username)
    driver = None
    
    try:
        driver = create_browser()
        
        # 登录
        log.info(f"🔐 开始登录: {masked}")
        driver.get(f"{BASE_URL}/login")
        time.sleep(3)
        
        username_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "identification"))
        )
        username_input.clear()
        username_input.send_keys(username)
        
        password_input = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        password_input.clear()
        password_input.send_keys(password)
        
        login_btn = driver.find_element(By.CSS_SELECTOR, "button.Button--primary")
        login_btn.click()
        time.sleep(5)
        
        # 验证登录
        if "login" in driver.current_url.lower():
            log.error(f"❌ 登录失败")
            return {"user": masked, "status": "login_failed", "msg": "登录失败"}
        
        log.info("✅ 登录成功")
        
        # 获取用户名确认
        try:
            user_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".SessionDropdown span.Avatar"))
            )
            log.info(f"👤 当前账号: {masked}")
        except:
            pass
        
        # 签到
        driver.get(BASE_URL)
        time.sleep(3)
        
        log.info(f"📌 {masked} 执行签到")
        
        # 查找签到按钮
        btn_selectors = [
            "button.checkin-button",
            "button.Button--primary.checkin",
            "button[class*='checkin']",
            "a.Button--primary.checkin"
        ]
        
        checkin_btn = None
        for selector in btn_selectors:
            try:
                checkin_btn = driver.find_element(By.CSS_SELECTOR, selector)
                if checkin_btn:
                    log.info(f"✅ 找到签到按钮: {selector}")
                    break
            except:
                continue
        
        if not checkin_btn:
            log.warning("⚠️ 未找到签到按钮")
            return {"user": masked, "status": "no_button", "msg": "未找到签到按钮"}
        
        checkin_btn.click()
        log.info("🖱️ 已点击签到按钮")
        time.sleep(3)
        
        # 检查弹窗结果
        try:
            alert = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".Modal-content, .Alert, .Toast"))
            )
            msg = alert.text.strip()
            log.info(f"🔔 弹窗内容: {msg}")
            
            if "已经签到" in msg or "already" in msg.lower():
                return {"user": masked, "status": "already", "msg": "今日已签到"}
            elif "成功" in msg or "success" in msg.lower() or "连续" in msg:
                match = re.search(r'(\d+)', msg)
                days = match.group(1) if match else "?"
                return {"user": masked, "status": "success", "msg": f"签到成功，连续{days}天"}
            else:
                return {"user": masked, "status": "unknown", "msg": msg}
        except:
            return {"user": masked, "status": "done", "msg": "已执行签到"}
            
    except Exception as e:
        log.error(f"❌ 处理异常: {e}")
        if driver:
            try:
                driver.save_screenshot(f"/tmp/error_{masked}.png")
            except:
                pass
        return {"user": masked, "status": "error", "msg": str(e)[:50]}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def main():
    accounts = parse_accounts()
    
    if not accounts:
        log.error("❌ 未配置账号，请设置 NL_ACCOUNT 环境变量")
        return
    
    log.info(f"✅ 共 {len(accounts)} 个账号，开始签到")
    
    results = []
    for i, acc in enumerate(accounts, 1):
        masked = mask_username(acc["username"])
        log.info(f"--- 账号 {i}/{len(accounts)}: {masked} ---")
        
        try:
            result = process_account(acc["username"], acc["password"])
            results.append(result)
        except Exception as e:
            log.error(f"❌ 浏览器启动失败: {e}")
            results.append({"user": masked, "status": "error", "msg": "浏览器启动失败"})
        
        if i < len(accounts):
            time.sleep(5)
    
    log.info("✅ 全部完成")
    
    # 生成报告
    status_icons = {
        "success": "✅",
        "already": "⏭️",
        "login_failed": "🔐",
        "error": "❌",
        "no_button": "⚠️",
        "unknown": "❓",
        "done": "📌"
    }
    
    report_lines = ["<b>📋 NodeLoc 签到报告</b>", ""]
    
    for r in results:
        icon = status_icons.get(r["status"], "❓")
        report_lines.append(f"{icon} {r['user']}: {r['msg']}")
        print(f"[{icon}] {r['user']} {r['msg']}")
    
    # 统计
    success_count = sum(1 for r in results if r["status"] in ["success", "already", "done"])
    report_lines.append("")
    report_lines.append(f"📊 成功: {success_count}/{len(results)}")
    
    # 发送通知
    send_telegram("\n".join(report_lines))

if __name__ == "__main__":
    main()
