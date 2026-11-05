FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY www/index.html /usr/share/nginx/html/index.html
# Fail the build if the config is broken
RUN nginx -t
