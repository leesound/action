# nodeloc/browser.py
import os
import undetected_chromedriver as uc

def create_browser():
    options = uc.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    
    chrome_path = os.environ.get('CHROME_PATH')
    if chrome_path:
        options.binary_location = chrome_path
    
    driver = uc.Chrome(
        options=options,
        version_main=131,
        browser_executable_path=chrome_path
    )
    return driver
