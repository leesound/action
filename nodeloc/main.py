# nodeloc/main.py
import os
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

def mask_username(username):
    """隐藏用户名，只显示首尾字符"""
    if len(username) <= 2:
        return username[0] + '*'
    return username[0] + '*' * (len(username) - 2) + username[-1]

def send_telegram(message):
    """发送Telegram通知"""
    token = os.environ.get('TG_BOT_TOKEN')
    chat_id = os.environ.get('TG_CHAT_ID')
    
    if not token or not chat_id:
        return
    
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'
    }
    
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        logging.error(f'TG通知失败: {e}')

def parse_accounts(account_str):
    """解析账号配置，格式：username:password，每行一个"""
    accounts = []
    for line in account_str.strip().split('\n'):
        line = line.strip()
        if not line or ':' not in line:
            continue
        parts = line.split(':', 1)
        if len(parts) == 2:
            accounts.append({
                'username': parts[0].strip(),
                'password': parts[1].strip()
            })
    return accounts

def process_account(username, password):
    """处理单个账号签到"""
    masked = mask_username(username)
    driver = None
    
    try:
        driver = create_browser()
        wait = WebDriverWait(driver, 15)
        
        # 登录
        logging.info(f'🔐 开始登录: {masked}')
        driver.get('https://www.nodeloc.com/login')
        
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="identification"]'))).send_keys(username)
        driver.find_element(By.CSS_SELECTOR, 'input[name="password"]').send_keys(password)
        driver.find_element(By.CSS_SELECTOR, 'button.LogInButton').click()
        
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'header .SessionDropdown')))
        logging.info('✅ 登录成功')
        
        # 签到
        logging.info(f'📌 {masked} 执行签到')
        driver.get('https://www.nodeloc.com')
        
        checkin_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.checkin-button')))
        checkin_btn.click()
        logging.info('🖱️ 已点击签到按钮')
        
        # 等待弹窗
        alert = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.AlertManager .Alert')))
        msg = alert.text.strip()
        logging.info(f'🔔 弹窗内容: {msg}')
        
        if '签到成功' in msg:
            return 'success', f'{masked} 签到成功'
        elif '已经签到' in msg:
            return 'skipped', f'{masked} 今日已签到'
        else:
            return 'unknown', f'{masked} {msg}'
            
    except Exception as e:
        logging.error(f'❌ 签到失败: {e}')
        if driver:
            driver.save_screenshot(f'/tmp/error_{masked}.png')
        return 'failed', f'{masked} 签到失败'
        
    finally:
        if driver:
            driver.quit()

def main():
    account_str = os.environ.get('NL_ACCOUNT', '')
    
    if not account_str:
        logging.error('❌ 未配置 NL_ACCOUNT')
        return
    
    accounts = parse_accounts(account_str)
    
    if not accounts:
        logging.error('❌ 账号列表为空，格式：username:password，每行一个')
        return
    
    logging.info(f'✅ 共 {len(accounts)} 个账号，开始签到')
    
    results = []
    for i, acc in enumerate(accounts, 1):
        masked = mask_username(acc['username'])
        logging.info(f'--- 账号 {i}/{len(accounts)}: {masked} ---')
        status, msg = process_account(acc['username'], acc['password'])
        
        if status == 'success':
            results.append(f'✅ {msg}')
        elif status == 'skipped':
            results.append(f'⏭️ {msg}')
        else:
            results.append(f'❌ {msg}')
        
        logging.info(results[-1])
    
    logging.info('✅ 全部完成')
    
    # 发送TG通知
    tg_msg = '<b>📋 NodeLoc 签到结果</b>\n\n' + '\n'.join(results)
    send_telegram(tg_msg)
    
    # 输出结果
    print('\n'.join(results))

if __name__ == '__main__':
    main()
