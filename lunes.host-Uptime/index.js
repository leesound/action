// 如果启动命令固定为 node 就要 index.js 文件调用 bash 来执行你的 start.sh 脚本 
const { spawn } = require('child_process');
const path = require('path');

const appDir = __dirname;
const scriptPath = path.join(appDir, 'start.sh');

// 启动 bash 脚本
const child = spawn('bash', [scriptPath], {
    cwd: appDir,
    stdio: 'inherit',
    env: {
        ...process.env,
        APP_DIR: appDir
    }
});

// 错误处理
child.on('error', (err) => {
    console.error('[ERROR] 无法启动 start.sh:', err.message);
    
    // 如果 bash 不可用，尝试 sh
    console.log('[INFO] 尝试使用 sh...');
    const fallback = spawn('sh', [scriptPath], {
        cwd: appDir,
        stdio: 'inherit',
        env: {
            ...process.env,
            APP_DIR: appDir
        }
    });
    
    fallback.on('error', (e) => {
        console.error('[ERROR] sh 也失败了:', e.message);
        process.exit(1);
    });
    
    fallback.on('exit', (code) => {
        process.exit(code || 0);
    });
});

// 传递退出信号
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));

child.on('exit', (code) => {
    process.exit(code || 0);
});
