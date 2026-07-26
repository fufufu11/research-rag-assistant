#!/bin/sh
# nginx 容器入口脚本（阶段 11.6 切片 E #100）
#
# 职责：
# 1. 用 envsubst 把 ${DOMAIN} 占位替换进 nginx.conf 模板，输出到 /etc/nginx/nginx.conf
# 2. 若真实 Let's Encrypt 证书不存在，生成 1 天有效的占位自签证书
#    （SSL server block 要求 cert 文件存在 nginx 才能启动）
# 3. 启动 crond 周期 reload nginx（读取 certbot 续期后的新证书，每 6 小时 reload 一次）
# 4. 前台运行 nginx（docker 容器要求前台进程，否则容器立即退出）
#
# 环境变量：
# - DOMAIN：签发证书的域名（必填，由 compose 注入）
#
# 依赖：
# - envsubst（alpine 默认含 gettext 或 busybox）
# - openssl（生成占位自签证书，nginx:alpine 默认含）
# - crond（busybox crond，alpine 默认含）

set -e
set -u

# 必须显式提供 DOMAIN 环境变量（生产 compose 应注入 DOMAIN=api.example.com）
DOMAIN="${DOMAIN:?DOMAIN env var must be set}"

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
FULLCHAIN="${CERT_DIR}/fullchain.pem"
PRIVKEY="${CERT_DIR}/privkey.pem"

# 1. envsubst 替换 ${DOMAIN} 占位
# 显式指定变量名列表，避免误替换 nginx 内置 $host / $remote_addr / $scheme 等变量
envsubst '${DOMAIN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# 2. 若真实证书不存在，生成占位自签证书
# 让 nginx SSL server block 能加载配置启动；certbot 签发后替换为真实证书
if [ ! -f "${FULLCHAIN}" ]; then
    echo "[nginx-entrypoint] 证书不存在: ${FULLCHAIN}，生成临时自签证书"
    mkdir -p "${CERT_DIR}"
    # 1 天有效期，明确标记为占位（certbot 签发后会被覆盖）
    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "${PRIVKEY}" \
        -out "${FULLCHAIN}" \
        -subj "/CN=${DOMAIN}" \
        -days 1
    echo "[nginx-entrypoint] 占位自签证书已生成（1 天有效期）"
fi

# 3. 设置 crond 周期 reload nginx（每 6 小时一次）
# certbot 容器续期后的新证书需 reload 才能生效；6 小时 reload 一次足够及时
# busybox crond 用 /etc/crontabs/root 作为 root 用户的 crontab
mkdir -p /etc/crontabs
echo "0 */6 * * * nginx -s reload" > /etc/crontabs/root
crond

# 4. 前台运行 nginx
# docker 容器要求主进程前台运行；nginx -g 'daemon off;' 关闭 daemon 模式
exec nginx -g 'daemon off;'
