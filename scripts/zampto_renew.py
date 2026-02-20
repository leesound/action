name: Zampto 自动续期

on:
  schedule:
    - cron: '0 18 * * *'
  workflow_dispatch:

jobs:
  renew:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    
    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 设置 Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 安装依赖
        run: |
          pip install seleniumbase pyvirtualdisplay requests
          sudo apt-get update
          sudo apt-get install -y xvfb

      - name: 安装并启动代理
        env:
          PROXY_NODE: ${{ secrets.PROXY_NODE }}
        run: |
          if [ -z "$PROXY_NODE" ]; then
            echo "[INFO] 未配置代理节点，将直连"
            exit 0
          fi
          
          wget -q https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
          unzip -q Xray-linux-64.zip -d xray
          chmod +x xray/xray
          
          PROTOCOL="${PROXY_NODE%%://*}"
          echo "[INFO] 协议: $PROTOCOL"
          
          parse_params() {
            local params="$1"
            local key="$2"
            echo "$params" | tr '&' '\n' | grep "^${key}=" | cut -d'=' -f2
          }
          
          case "$PROTOCOL" in
            vless)
              content="${PROXY_NODE#vless://}"
              content="${content%%#*}"
              uuid="${content%%@*}"
              rest="${content#*@}"
              server="${rest%%:*}"
              port="${rest#*:}"
              port="${port%%\?*}"
              params="${rest#*\?}"
              
              sni=$(parse_params "$params" "sni")
              pbk=$(parse_params "$params" "pbk")
              fp=$(parse_params "$params" "fp")
              flow=$(parse_params "$params" "flow")
              security=$(parse_params "$params" "security")
              type_=$(parse_params "$params" "type")
              type_="${type_:-tcp}"
              
              if [ "$security" = "reality" ]; then
                cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "vless",
              "settings": {"vnext": [{"address": "$server", "port": $port, "users": [{"id": "$uuid", "encryption": "none", "flow": "$flow"}]}]},
              "streamSettings": {"network": "$type_", "security": "reality", "realitySettings": {"serverName": "$sni", "fingerprint": "${fp:-chrome}", "publicKey": "$pbk"}}
            }]
          }
          EOF
              elif [ "$security" = "tls" ]; then
                cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "vless",
              "settings": {"vnext": [{"address": "$server", "port": $port, "users": [{"id": "$uuid", "encryption": "none"}]}]},
              "streamSettings": {"network": "$type_", "security": "tls", "tlsSettings": {"serverName": "${sni:-$server}"}}
            }]
          }
          EOF
              else
                cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "vless",
              "settings": {"vnext": [{"address": "$server", "port": $port, "users": [{"id": "$uuid", "encryption": "none"}]}]},
              "streamSettings": {"network": "$type_", "security": "none"}
            }]
          }
          EOF
              fi
              ;;
              
            vmess)
              content="${PROXY_NODE#vmess://}"
              content="${content%%#*}"
              json=$(echo "$content" | base64 -d 2>/dev/null)
              
              server=$(echo "$json" | grep -o '"add"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
              port=$(echo "$json" | grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' | grep -o '[0-9]*')
              uuid=$(echo "$json" | grep -o '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
              aid=$(echo "$json" | grep -o '"aid"[[:space:]]*:[[:space:]]*[0-9]*' | grep -o '[0-9]*')
              aid="${aid:-0}"
              net=$(echo "$json" | grep -o '"net"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
              net="${net:-tcp}"
              tls=$(echo "$json" | grep -o '"tls"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
              sni=$(echo "$json" | grep -o '"sni"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
              
              if [ "$tls" = "tls" ]; then
                cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "vmess",
              "settings": {"vnext": [{"address": "$server", "port": $port, "users": [{"id": "$uuid", "alterId": $aid, "security": "auto"}]}]},
              "streamSettings": {"network": "$net", "security": "tls", "tlsSettings": {"serverName": "${sni:-$server}"}}
            }]
          }
          EOF
              else
                cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "vmess",
              "settings": {"vnext": [{"address": "$server", "port": $port, "users": [{"id": "$uuid", "alterId": $aid, "security": "auto"}]}]},
              "streamSettings": {"network": "$net", "security": "none"}
            }]
          }
          EOF
              fi
              ;;
              
            ss|shadowsocks)
              content="${PROXY_NODE#*://}"
              content="${content%%#*}"
              
              if [[ "$content" == *"@"* ]]; then
                userinfo="${content%%@*}"
                serverinfo="${content#*@}"
                if [[ "$userinfo" != *":"* ]]; then
                  userinfo=$(echo "$userinfo" | base64 -d 2>/dev/null)
                fi
                method="${userinfo%%:*}"
                password="${userinfo#*:}"
                server="${serverinfo%%:*}"
                port="${serverinfo#*:}"
                port="${port%%\?*}"
              else
                decoded=$(echo "$content" | base64 -d 2>/dev/null)
                userinfo="${decoded%%@*}"
                serverinfo="${decoded#*@}"
                method="${userinfo%%:*}"
                password="${userinfo#*:}"
                server="${serverinfo%%:*}"
                port="${serverinfo#*:}"
              fi
              
              cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "shadowsocks",
              "settings": {"servers": [{"address": "$server", "port": $port, "method": "$method", "password": "$password"}]}
            }]
          }
          EOF
              ;;
              
            trojan)
              content="${PROXY_NODE#trojan://}"
              content="${content%%#*}"
              password="${content%%@*}"
              rest="${content#*@}"
              server="${rest%%:*}"
              port="${rest#*:}"
              port="${port%%\?*}"
              params="${rest#*\?}"
              sni=$(parse_params "$params" "sni")
              sni="${sni:-$server}"
              
              cat > xray/config.json << EOF
          {
            "inbounds": [{"port": 1080, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],
            "outbounds": [{
              "protocol": "trojan",
              "settings": {"servers": [{"address": "$server", "port": $port, "password": "$password"}]},
              "streamSettings": {"network": "tcp", "security": "tls", "tlsSettings": {"serverName": "$sni"}}
            }]
          }
          EOF
              ;;
              
            socks|socks5)
              content="${PROXY_NODE#*://}"
              content="${content%%#*}"
              if [[ "$content" == *"@"* ]]; then
                auth="${content%%@*}"
                serverinfo="${content#*@}"
                echo "PROXY_SOCKS5=socks5://$auth@$serverinfo" >> $GITHUB_ENV
              else
                echo "PROXY_SOCKS5=socks5://$content" >> $GITHUB_ENV
              fi
              echo "[INFO] ✅ SOCKS5 代理已配置"
              exit 0
              ;;
              
            http|https)
              content="${PROXY_NODE#*://}"
              content="${content%%#*}"
              echo "PROXY_HTTP=http://$content" >> $GITHUB_ENV
              echo "[INFO] ✅ HTTP 代理已配置"
              exit 0
              ;;
              
            *)
              echo "[ERROR] 不支持的协议: $PROTOCOL"
              exit 1
              ;;
          esac
          
          # 启动 Xray (不输出配置)
          cd xray && ./xray run -config config.json > /dev/null 2>&1 &
          cd ..
          sleep 5
          
          # 测试代理
          for i in {1..5}; do
            if curl -s --connect-timeout 10 -x socks5://127.0.0.1:1080 https://api.ipify.org > /dev/null 2>&1; then
              echo "[INFO] ✅ 代理连接成功"
              echo "PROXY_SOCKS5=socks5://127.0.0.1:1080" >> $GITHUB_ENV
              exit 0
            fi
            sleep 3
          done
          
          echo "[ERROR] ❌ 代理连接失败"
          exit 1

      - name: 运行续期脚本
        env:
          ZAMPTO_ACCOUNT: ${{ secrets.ZAMPTO_ACCOUNT }}
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
          PROXY_SOCKS5: ${{ env.PROXY_SOCKS5 }}
        run: python scripts/zampto_renew.py

      - name: 上传截图
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: screenshots-${{ github.run_number }}
          path: output/screenshots/
          retention-days: 7
          if-no-files-found: ignore
