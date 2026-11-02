# Build widget (1 file IIFE) + trang demo, phục vụ bằng nginx ở cổng 80.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# URL backend mà widget gọi tới; trang demo nhúng script với data-api-base = giá trị này.
# Để trống -> trang demo chạy chế độ giả lập (data-mock="true").
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
