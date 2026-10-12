FROM nginx:1.28-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/index.html frontend/app.js frontend/styles.css /usr/share/nginx/html/
COPY examples/hospital1.csv /usr/share/nginx/html/example.csv
EXPOSE 80
