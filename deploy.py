# -*- coding: utf-8 -*-
"""一键部署脚本：SSH 到服务器，部署 video-dedup-station + CD + 本地建 dev 分支"""
import sys
import time
import paramiko

HOST = "124.71.209.36"
USER = "root"
PASS = "Www135168."

REPO = "https://github.com/luckly06/video-mcp.git"
APP_DIR = "/opt/video-dedup"


def run(ssh, cmd, desc=""):
    """执行远程命令，返回 (exit_code, stdout)"""
    if desc:
        print(f"  → {desc} ...", end=" ", flush=True)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=300)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    exit_code = stdout.channel.recv_exit_status()
    if desc:
        print("OK" if exit_code == 0 else f"FAIL ({exit_code})")
    if out:
        for line in out.splitlines()[-10:]:
            print(f"    {line}")
    if err and exit_code != 0:
        for line in err.splitlines()[-5:]:
            print(f"    [stderr] {line}")
    return exit_code, out


def main():
    print("=" * 60)
    print("video-dedup-station 一键部署")
    print(f"目标: root@{HOST}")
    print("=" * 60)

    # 1) 连接
    print("\n[1/8] 连接服务器 ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(HOST, username=USER, password=PASS, timeout=15, look_for_keys=False, allow_agent=False)
    except Exception as e:
        print(f"❌ SSH 连接失败: {e}")
        return 1
    print("  ✅ 已连接")

    # 2) 检查服务器环境
    print("\n[2/8] 检查服务器环境 ...")
    run(client, "uname -a", "系统信息")
    run(client, "cat /etc/os-release | head -3", "发行版")

    # 3) 安装依赖
    print("\n[3/8] 安装系统依赖 ...")
    run(client, "apt-get update -qq", "apt update")
    run(client, "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git 2>&1", "安装 python3/ffmpeg/git")

    # 4) 克隆 / 更新仓库
    print("\n[4/8] 部署代码 ...")
    code, out = run(client, f"ls {APP_DIR}/station/server/mcp_server.py 2>/dev/null", "检查是否已有项目")
    if code == 0:
        print("  已有项目，git pull 更新 ...")
        run(client, f"cd {APP_DIR} && git stash && git pull origin main", "git pull")
    else:
        run(client, f"git clone {REPO} {APP_DIR}", "git clone")

    # 5) 配置虚拟环境
    print("\n[5/8] 配置 Python 虚拟环境 ...")
    run(client, f"python3 -m venv {APP_DIR}/.venv", "创建 venv")
    run(client, f"{APP_DIR}/.venv/bin/pip install -q -r {APP_DIR}/station/requirements.txt", "pip install 依赖")

    # 6) 创建环境变量与目录
    print("\n[6/8] 配置环境与 systemd ...")
    env_content = f"""# video-dedup-station 生产环境变量
VU_HOST=0.0.0.0
VU_PORT=8765
VU_FFMPEG=/usr/bin/ffmpeg
VU_FFPROBE=/usr/bin/ffprobe
VU_ASSETS={APP_DIR}/station/assets
VU_OUTPUT={APP_DIR}/output
"""
    run(client, f"cat > {APP_DIR}/.env << 'ENVEOF'\n{env_content}\nENVEOF", "写 .env")
    run(client, f"mkdir -p {APP_DIR}/output && chmod 755 {APP_DIR}/output", "创建 output/ 目录")

    systemd_unit = f"""[Unit]
Description=video-dedup-station MCP Server
After=network.target

[Service]
Type=simple
User=nobody
Group=nogroup
WorkingDirectory={APP_DIR}/station

EnvironmentFile={APP_DIR}/.env

ExecStart={APP_DIR}/.venv/bin/python {APP_DIR}/station/server/mcp_server.py

Restart=on-failure
RestartSec=5

PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
"""
    run(client, f"cat > /etc/systemd/system/video-dedup.service << 'SVC_EOF'\n{systemd_unit}\nSVC_EOF", "写 systemd unit")
    run(client, "systemctl daemon-reload", "systemctl daemon-reload")
    run(client, "systemctl enable video-dedup", "enable 服务")
    run(client, "systemctl restart video-dedup", "启动服务")
    time.sleep(2)
    run(client, "systemctl status video-dedup --no-pager -l | head -15", "服务状态")

    # 7) 配置 CD（cron 每 2 分钟检查 main 更新，有变动则 pull + 重启）
    print("\n[7/8] 配置 CD 脚本（监控 main 分支） ...")
    cd_script = f"""#!/bin/bash
# CD: 每 2 分钟检查 origin/main 是否有新提交，有则 pull + 重启
DEPLOY_DIR="{APP_DIR}"
BRANCH="main"
SERVICE="video-dedup"
LOCKFILE="/tmp/video-dedup-cd.lock"

# 防并发
exec 9>"$LOCKFILE"
flock -n 9 || exit 0

cd "$DEPLOY_DIR" || exit 1

git fetch origin "$BRANCH" -q 2>/dev/null
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[CD] $(date -Iseconds) 检测到新提交 $REMOTE，开始部署 ..."
    git reset --hard "origin/$BRANCH"
    systemctl restart "$SERVICE"
    sleep 3
    systemctl status "$SERVICE" --no-pager -l | head -5
    echo "[CD] 部署完成。"
fi
"""
    run(client, f"cat > /usr/local/bin/video-dedup-cd.sh << 'CDEOF'\n{cd_script}\nCDEOF", "写 CD 脚本")
    run(client, "chmod +x /usr/local/bin/video-dedup-cd.sh", "chmod +x CD 脚本")

    # cron: 每 2 分钟
    cron_line = "*/2 * * * * root /usr/local/bin/video-dedup-cd.sh >> /var/log/video-dedup-cd.log 2>&1"
    run(client, f'(crontab -l 2>/dev/null | grep -v video-dedup-cd; echo "{cron_line}") | crontab -', "写入 cron")

    # 8) 验证
    print("\n[8/8] 验证部署 ...")
    time.sleep(2)
    code, out = run(client, 'curl -s -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}\'', "MCP discover")
    if code == 0 and "video-dedup-station" in out:
        print("\n✅ 部署成功！MCP server 已在运行。")
    else:
        # 再等 5 秒重试（可能服务刚起）
        print("  等待 5 秒重试 ...")
        time.sleep(5)
        code, out = run(client, 'curl -s -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}\'', "MCP discover (retry)")
        if code == 0 and "video-dedup-station" in out:
            print("\n✅ 部署成功！")
        else:
            print(f"\n⚠️ MCP 验证未通过，请手动检查。输出:\n{out}")

    client.close()
    print("=" * 60)
    print("部署完成。")
    print(f"  MCP URL:  http://{HOST}:8765/mcp")
    print(f"  Web UI:   http://{HOST}:8765/")
    print(f"  CD cron:  每 2 分钟检查 main 分支变更")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
