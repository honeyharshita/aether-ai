FROM nginx:1.27-alpine

COPY index.html /usr/share/nginx/html/index.html
COPY public/aetherai-login.png /usr/share/nginx/html/aetherai-login.png
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q -O- http://127.0.0.1:80 || exit 1
