name: WeirdHost Renew

on:
  schedule:
    - cron: '30 22 * * *'
  workflow_dispatch:

jobs:
  renew:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y xvfb x11-utils
          pip install seleniumbase pynacl
          seleniumbase install chromedriver
      
      - name: Setup SSH tunnel
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.SSH_PRIVATE_KEY }}" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          ssh -o StrictHostKeyChecking=no -D 1080 -f -N ${{ secrets.SSH_USER }}@${{ secrets.SSH_HOST }} -p ${{ secrets.SSH_PORT || 22 }}
          sleep 3
        if: ${{ secrets.SSH_HOST != '' }}
      
      - name: Run renewal script
        env:
          WEIRDHOST_COOKIE: ${{ secrets.WEIRDHOST_COOKIE }}
          WEIRDHOST_ID: ${{ secrets.WEIRDHOST_ID }}
          SOCKS_PROXY: "socks5://127.0.0.1:1080"
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
          REPO_TOKEN: ${{ secrets.REPO_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          DISPLAY: ":99"
        run: |
          # 启动 Xvfb 虚拟显示器
          Xvfb :99 -screen 0 1920x1080x24 &
          sleep 2
          python scripts/weirdhost_renew.py
      
      - name: Upload screenshots
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: screenshots
          path: "*.png"
          retention-days: 3
