#!/bin/sh
# certbot 容器入口脚本（阶段 11.6 切片 E #100）
#
# 职责：
# 1. 等待 nginx 启动（webroot challenge 需 nginx 服务 80 端口）
# 2. 首次签发：若证书目录不存在，运行 certbot certonly --webroot 签发证书
# 3. 续期 cron：用 crond 周期性运行 certbot renew（每 12 小时检查一次，
#    certbot renew 仅在证书临近过期时实际续期，否则 no-op）
# 4. 前台运行 crond 保持容器存活
#
# 环境变量：
# - DOMAIN：签发证书的域名（必填）
# - LETSENCRYPT_EMAIL：Let's Encrypt 注册邮箱（必填，用于证书到期提醒）
#
# 与 nginx 容器的协作：
# - 共享 webroot 卷（/var/www/certbot）：certbot 写 challenge 响应，nginx 对外服务
# - 共享证书卷（/etc/letsencrypt）：certbot 写签发的证书，nginx 读取
# - nginx 容器有独立的 reload cron（每 6 小时）读取续期后的新证书

set -e
set -u

# 必须显式提供 DOMAIN 和 LETSENCRYPT_EMAIL 环境变量
DOMAIN="${DOMAIN:?DOMAIN env var must be set}"
: "${LETSENCRYPT_EMAIL:?LETSENCRYPT_EMAIL env var must be set}"

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"

# 1. 等待 nginx 启动（端口 80 可达）
# webroot challenge 需 nginx 把 /.well-known/acme-challenge/ 路径对外服务
# 用 wget 探测 nginx 是否已就绪（certbot/certbot 镜像含 busybox wget）
echo "[certbot-entrypoint] 等待 nginx 启动..."
NGINX_READY=0
for _ in $(seq 1 30); do
    if wget -q -O /dev/null "http://nginx/" 2>/dev/null; then
        NGINX_READY=1
        break
    fi
    sleep 2
done
if [ "${NGINX_READY}" -eq 0 ]; then
    echo "[certbot-entrypoint] 警告：等待 nginx 超时（60s），仍尝试运行 certbot"
fi
echo "[certbot-entrypoint] nginx 已就绪"

# 2. 首次签发：若证书目录不存在，运行 certbot certonly --webroot
# --webroot 模式不停 nginx（standalone 模式需停 nginx 占用 80 端口）
# --non-interactive：非交互模式，避免提示输入（容器环境无 tty）
# --agree-tos / --no-eff-email：同意 Let's Encrypt 服务条款，不订阅 EFF 邮件
if [ ! -d "${CERT_DIR}" ]; then
    echo "[certbot-entrypoint] 首次签发证书: ${DOMAIN}"
    certbot certonly --webroot \
        --webroot-path=/var/www/certbot \
        -d "${DOMAIN}" \
        -m "${LETSENCRYPT_EMAIL}" \
        --agree-tos \
        --no-eff-email \
        --non-interactive
    echo "[certbot-entrypoint] 证书签发成功"
else
    echo "[certbot-entrypoint] 证书已存在，尝试续期: ${DOMAIN}"
    certbot renew || true
fi

# 3. 设置 crond 周期续期（每 12 小时检查一次）
# certbot renew 自动检查证书有效期，仅临近过期（< 30 天）时实际续期
# busybox crond 用 /etc/crontabs/root 作为 root 用户的 crontab
mkdir -p /etc/crontabs
echo "0 */12 * * * certbot renew --quiet" > /etc/crontabs/root

# 4. 前台运行 crond 保持容器存活
# docker 容器要求主进程前台运行；crond -f 关闭 daemon 模式
exec crond -f
